"""Export WORM con hash encadenado previo al DROP de particiones de auditoría (KC-DB-10-2).

Criterios del ticket:
  1. La purga aborta el DROP de una partición de auditoría si no existe el comprobante de export WORM verificado.
  2. El archivo exportado conserva el hash encadenado íntegro y pasa la verificación externa de no-alteración
     (db/tools/worm_auditoria.py, solo biblioteca estándar, sin base de datos).

El trigger de sellado fija creado_en = now(), así que para tener particiones antiguas se construyen las cadenas dentro de
una transacción con session_replication_role = replica (sin triggers) y se sellan con la propia audit.calcular_sello.
Todo se deshace al final.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import stat
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
GENESIS = "0" * 64
AHORA_FICTICIO = dt.datetime(2024, 4, 1, tzinfo=dt.UTC)


def _cargar_herramienta() -> ModuleType:
    ruta = RAIZ / "db" / "tools" / "worm_auditoria.py"
    spec = importlib.util.spec_from_file_location("worm_auditoria", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["worm_auditoria"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


worm = _cargar_herramienta()


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SELECT '[1]'::vector")
    c.execute("SET LOCAL session_replication_role = replica")
    c.execute("SET LOCAL TIME ZONE 'UTC'")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def destino(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for f in tmp_path.rglob("*"):  # los exports quedan de solo lectura
        if f.is_file():
            f.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _suf() -> str:
    return uuid.uuid4().hex[:8]


def _particion(c: psycopg.Connection, nombre: str, desde: str, hasta: str) -> str:
    c.execute(  # type: ignore[call-overload]
        f"CREATE TABLE audit.{nombre} PARTITION OF audit.audit_events FOR VALUES FROM ('{desde}') TO ('{hasta}')"
    )
    return f"audit.{nombre}"


class Cadena:
    """Cadena de eventos de un tenant construida a mano y sellada con audit.calcular_sello."""

    def __init__(self, c: psycopg.Connection, tenant: str) -> None:
        self.c, self.tenant = c, tenant
        self.ultimo, self.n = GENESIS, 0

    def evento(self, cuando: str, texto: str, datos: dict[str, Any] | None = None) -> int:
        fila = self.c.execute(
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, datos, sello_previo, sello, creado_en, "
            "version_sello, ip, user_agent, request_id) VALUES (%s, 'sistema', 'prueba_worm', %s, %s::jsonb, %s, %s, %s, 2, "
            "'10.0.0.7', 'agente/1', %s) RETURNING id",
            (
                self.tenant,
                texto,
                json.dumps(datos) if datos is not None else None,
                self.ultimo,
                "a" * 64,
                cuando,
                f"req-{_suf()}",
            ),
        ).fetchone()
        assert fila is not None
        eid = int(fila[0])
        sello = self.c.execute(
            "UPDATE audit.audit_events e SET sello = audit.calcular_sello(e) WHERE e.id = %s RETURNING sello",
            (eid,),
        ).fetchone()
        assert sello is not None
        self.ultimo, self.n = str(sello[0]), self.n + 1
        self.c.execute(
            "INSERT INTO audit.chain_heads (tenant_id, ultimo_sello, eventos) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET ultimo_sello = EXCLUDED.ultimo_sello, eventos = EXCLUDED.eventos",
            (self.tenant, self.ultimo, self.n),
        )
        return eid


@pytest.fixture
def escenario(conn: psycopg.Connection) -> dict[str, Any]:
    """Dos particiones (enero y febrero de 2024); el tenant A tiene eventos en las dos y el B solo en enero."""
    s = _suf()
    p1 = _particion(conn, f"zz_p1_{s}", "2024-01-01", "2024-02-01")
    p2 = _particion(conn, f"zz_p2_{s}", "2024-02-01", "2024-03-01")
    a, b = Cadena(conn, f"org_worm_a_{s}"), Cadena(conn, f"org_worm_b_{s}")
    a.evento("2024-01-10 10:00:00+00", "a1", {"k": 1, "lista": [1, 2], "txt": "ñandú"})
    a.evento("2024-01-20 10:00:00+00", "a2", {"k": 2})
    b.evento("2024-01-15 10:00:00+00", "b1")
    b.evento("2024-01-25 10:00:00+00", "b2", {"x": None})
    a.evento("2024-02-05 10:00:00+00", "a3")
    a.evento("2024-02-15 10:00:00+00", "a4", {"k": 4})
    conn.execute(
        "UPDATE core.retention_policies SET action = 'drop', retention_days = 1, is_active = true "
        "WHERE entity_name = 'audit.audit_events' AND tenant_id IS NULL"
    )
    return {"p1": p1, "p2": p2, "a": a, "b": b, "s": s}


def _motor(c: psycopg.Connection) -> dict[str, tuple[str, str | None]]:
    """Resultado del motor para las particiones de auditoría: {partición: (acción, motivo)}."""
    filas = c.execute(
        "SELECT objeto, accion, motivo FROM core.purgar_retencion(%s, false) WHERE entidad = 'audit.audit_events'",
        (AHORA_FICTICIO,),
    ).fetchall()
    return {str(o): (str(a), m) for o, a, m in filas}


def _existe(c: psycopg.Connection, tabla: str) -> bool:
    return c.execute("SELECT to_regclass(%s) IS NOT NULL", (tabla,)).fetchone() == (True,)


def _verify(c: psycopg.Connection, tenant: str) -> tuple[Any, ...]:
    fila = c.execute(
        "SELECT integra, eventos_verificados, eventos_esperados, eventos_observados FROM audit.verify_chain(%s)",
        (tenant,),
    ).fetchone()
    assert fila is not None
    return tuple(fila)


def _v16_v17(c: psycopg.Connection) -> dict[str, int]:
    return {
        str(r[0]): int(r[1])
        for r in c.execute(
            "SELECT chequeo, problemas FROM core.validar_integridad() WHERE chequeo IN ('V16_cadena_auditoria', 'V17_cabeza_cadena')"
        ).fetchall()
    }


# --- criterio 1: sin comprobante no se descarta ------------------------------------------------------------
def test_sin_comprobante_la_purga_no_descarta_ninguna_particion_de_auditoria(
    conn: psycopg.Connection, escenario: dict[str, Any]
) -> None:
    r = _motor(conn)
    for p in (escenario["p1"], escenario["p2"]):
        accion, motivo = r[p]
        assert accion == "omitida" and "sin comprobante de export WORM" in str(motivo)
        assert _existe(conn, p)
    assert conn.execute("SELECT count(*) FROM audit.chain_anchors").fetchone() == (0,)


def test_con_comprobante_se_descarta_en_orden_y_la_cadena_restante_sigue_integra(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    antes = _v16_v17(conn)
    assert (_verify(conn, escenario["a"].tenant))[0] is True
    r_exp = worm.exportar(conn, escenario["p1"], destino, "prueba")
    assert r_exp["filas"] == 4

    r = _motor(conn)
    assert r[escenario["p1"]][0] == "eliminada"
    assert r[escenario["p2"]][0] == "omitida"  # P2 no tiene comprobante todavía
    assert not _existe(conn, escenario["p1"]) and _existe(conn, escenario["p2"])

    # el tenant A conserva a3 y a4 (anclados a a2); el B ya no tiene eventos y su recuento cuadra
    assert _verify(conn, escenario["a"].tenant) == (True, 2, 2, 2)
    assert _verify(conn, escenario["b"].tenant) == (True, 0, 0, 0)
    anclas: dict[str, int] = dict(
        conn.execute(
            "SELECT tenant_id, eventos_descartados FROM audit.chain_anchors ORDER BY 1"
        ).fetchall()
    )
    assert anclas == {escenario["a"].tenant: 2, escenario["b"].tenant: 2}
    assert _v16_v17(conn) == antes, "V16 y V17 siguen sin problemas tras el descarte"

    # la partición siguiente, ya exportada, también se descarta (ahora sí es prefijo)
    worm.exportar(conn, escenario["p2"], destino, "prueba")
    assert _motor(conn)[escenario["p2"]][0] == "eliminada"
    assert _verify(conn, escenario["a"].tenant) == (True, 0, 0, 0)
    assert _v16_v17(conn) == antes


def test_el_ancla_no_oculta_un_borrado_posterior(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    """Tras el descarte, perder un evento de los que quedan sigue rompiendo la cadena."""
    worm.exportar(conn, escenario["p1"], destino, "prueba")
    _motor(conn)
    conn.execute("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
    conn.execute(
        "DELETE FROM audit.audit_events WHERE tenant_id = %s AND resumen = 'a4'",
        (escenario["a"].tenant,),
    )
    integra = _verify(conn, escenario["a"].tenant)[0]
    assert integra is False


def test_no_se_puede_descartar_una_particion_que_no_es_prefijo_de_la_cadena(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    worm.exportar(
        conn, escenario["p2"], destino, "prueba"
    )  # febrero exportado, pero enero sigue ahí
    accion, motivo = _motor(conn)[escenario["p2"]]
    assert accion == "omitida" and "no es un prefijo de la cadena" in str(motivo)
    assert _existe(conn, escenario["p2"])


def test_si_la_particion_cambia_despues_del_export_se_bloquea_hasta_exportar_de_nuevo(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    worm.exportar(conn, escenario["p1"], destino, "prueba")
    escenario["b"].evento("2024-01-28 10:00:00+00", "b3")  # un evento nuevo en enero tras el export
    accion, motivo = _motor(conn)[escenario["p1"]]
    assert accion == "omitida" and "cambió después del export" in str(motivo)
    assert _existe(conn, escenario["p1"])
    worm.exportar(
        conn, escenario["p1"], destino, "prueba"
    )  # nuevo comprobante del contenido actual
    assert _motor(conn)[escenario["p1"]][0] == "eliminada"


def test_no_se_registra_un_comprobante_de_un_contenido_que_no_es_el_actual(
    conn: psycopg.Connection, escenario: dict[str, Any]
) -> None:
    man = conn.execute(
        "SELECT audit.manifiesto_particion(%s::regclass)", (escenario["p1"],)
    ).fetchone()
    assert man is not None
    with pytest.raises(errors.RaiseException, match="digest distinto"):
        conn.execute(
            "SELECT audit.registrar_export_worm(%s::regclass, %s, %s, %s, %s, 'x', %s::jsonb, 'prueba')",
            (escenario["p1"], "b" * 64, "c" * 64, "d" * 64, GENESIS, json.dumps(man[0])),
        )


def test_no_se_exporta_una_cadena_rota(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    conn.execute(
        "UPDATE audit.audit_events SET resumen = 'alterado' WHERE tenant_id = %s AND resumen = 'a1'",
        (escenario["a"].tenant,),
    )
    with pytest.raises(ValueError, match="no íntegra"):
        worm.exportar(conn, escenario["p1"], destino, "prueba")
    assert not list(destino.iterdir()), "no se escribe nada si la cadena está rota"


# --- criterio 2: el archivo conserva el hash encadenado y pasa la verificación externa ----------------------------
def test_el_archivo_exportado_pasa_la_verificacion_externa_sin_base_de_datos(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    r = worm.exportar(conn, escenario["p1"], destino, "prueba")
    assert worm.verificar_archivo(r["manifiesto"]) == []
    # el verificador es independiente: su canónico coincide con el de la base para todos los eventos
    lineas = [json.loads(ln) for ln in r["archivo"].read_text(encoding="utf-8").splitlines()]
    assert len(lineas) == 4
    db: dict[int, str] = dict(
        conn.execute(
            "SELECT id, audit.calcular_sello(e) FROM audit.audit_events e WHERE tableoid = %s::regclass",
            (escenario["p1"],),
        ).fetchall()
    )
    for e in lineas:
        assert worm.sello_canonico(e) == e["sello"] == db[e["id"]]
    # un archivo y un manifiesto de solo lectura, no sobrescribibles
    assert not os.access(r["archivo"], os.W_OK)
    with pytest.raises(FileExistsError):
        r["archivo"].open("x")
    # y el comprobante quedó registrado con esos hashes
    fila = conn.execute(
        "SELECT archivo_sha256, filas, verificado_por FROM audit.worm_exports WHERE id = %s",
        (r["export_id"],),
    ).fetchone()
    assert fila == (r["archivo_sha256"], 4, "prueba")
    assert worm.main(["verificar", str(r["manifiesto"])]) == 0


def _reescribir(r: dict[str, Any], f: Any) -> None:
    for ruta in (r["archivo"], r["manifiesto"]):
        ruta.chmod(stat.S_IRUSR | stat.S_IWUSR)
    f()


def test_alterar_un_byte_del_archivo_se_detecta(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    r = worm.exportar(conn, escenario["p1"], destino, "prueba")
    _reescribir(
        r, lambda: r["archivo"].write_bytes(r["archivo"].read_bytes().replace(b"a1", b"a9", 1))
    )
    problemas = worm.verificar_archivo(r["manifiesto"])
    assert any("sha256 del archivo" in p for p in problemas)
    assert worm.main(["verificar", str(r["manifiesto"])]) == 1


def test_alterar_un_evento_y_rehacer_el_hash_del_archivo_se_detecta_por_el_sello(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    """Un atacante que reescribe el archivo y el manifiesto no puede rehacer los sellos encadenados."""
    r = worm.exportar(conn, escenario["p1"], destino, "prueba")

    def rehacer() -> None:
        r["archivo"].write_bytes(
            r["archivo"].read_bytes().replace(b'"resumen":"a1"', b'"resumen":"A1"', 1)
        )
        man = json.loads(r["manifiesto"].read_text(encoding="utf-8"))
        man["archivo_sha256"] = worm.sha256_archivo(r["archivo"])
        r["manifiesto"].write_text(json.dumps(man, indent=2), encoding="utf-8")

    _reescribir(r, rehacer)
    problemas = worm.verificar_archivo(r["manifiesto"])
    assert any("el sello no coincide con su contenido" in p for p in problemas), problemas


def test_quitar_un_evento_de_la_cadena_se_detecta(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    r = worm.exportar(conn, escenario["p1"], destino, "prueba")

    def quitar() -> None:
        lineas = r["archivo"].read_text(encoding="utf-8").splitlines()
        quedan = [ln for ln in lineas if '"resumen":"a2"' not in ln]
        assert len(quedan) == 3
        r["archivo"].write_text("\n".join(quedan) + "\n", encoding="utf-8")
        eventos = [json.loads(ln) for ln in quedan]
        man = json.loads(r["manifiesto"].read_text(encoding="utf-8"))
        man.update(
            archivo_sha256=worm.sha256_archivo(r["archivo"]),
            filas=3,
            contenido_digest=worm.digest_contenido(eventos),
        )
        r["manifiesto"].write_text(json.dumps(man, indent=2), encoding="utf-8")

    _reescribir(r, quitar)
    problemas = worm.verificar_archivo(r["manifiesto"])
    assert any(
        "comienzos" in p or "eslabones sueltos" in p or "no es el del archivo" in p
        for p in problemas
    ), problemas
    # y quien reescriba también el manifiesto no coincide con el hash del manifiesto REGISTRADO en la base
    registrado = conn.execute(
        "SELECT manifiesto_sha256 FROM audit.worm_exports WHERE id = %s", (r["export_id"],)
    ).fetchone()
    assert registrado is not None
    assert any(
        "sha256 del manifiesto" in p
        for p in worm.verificar_archivo(r["manifiesto"], manifiesto_sha256=registrado[0])
    )


def test_los_exports_forman_una_cadena_y_el_enlace_se_verifica(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    uno = worm.exportar(conn, escenario["p1"], destino, "prueba")
    _motor(conn)
    dos = worm.exportar(conn, escenario["p2"], destino, "prueba")
    man2 = json.loads(dos["manifiesto"].read_text(encoding="utf-8"))
    assert man2["export_anterior_sha256"] == uno["archivo_sha256"]
    assert (
        json.loads(uno["manifiesto"].read_text(encoding="utf-8"))["export_anterior_sha256"]
        == GENESIS
    )
    assert worm.verificar_archivo(dos["manifiesto"], anterior=uno["manifiesto"]) == []
    # enlazar con el export equivocado se detecta
    assert any(
        "no es el que enlaza" in p
        for p in worm.verificar_archivo(uno["manifiesto"], anterior=dos["manifiesto"])
    )
    # y la base se niega a registrar un export que no enlaza con el último
    man = conn.execute(
        "SELECT audit.manifiesto_particion(%s::regclass)", (escenario["p2"],)
    ).fetchone()
    assert man is not None
    with pytest.raises(errors.RaiseException, match="export anterior"):
        conn.execute(
            "SELECT audit.registrar_export_worm(%s::regclass, audit.digest_particion(%s::regclass), %s, %s, %s, 'x', %s::jsonb, 'prueba')",
            (escenario["p2"], escenario["p2"], "e" * 64, "f" * 64, GENESIS, json.dumps(man[0])),
        )


# --- los comprobantes y las anclas son inmutables ------------------------------------------------------------
def test_los_comprobantes_son_append_only(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    worm.exportar(conn, escenario["p1"], destino, "prueba")
    conn.execute(
        "SET LOCAL session_replication_role = origin"
    )  # con «replica» no disparan los triggers
    for sql in (
        "UPDATE audit.worm_exports SET verificado_por = 'otro'",
        "DELETE FROM audit.worm_exports",
        "TRUNCATE audit.worm_exports",
    ):
        conn.execute("SAVEPOINT s")
        with pytest.raises(errors.RaiseException, match="append-only"):
            conn.execute(sql)  # type: ignore[call-overload]
        conn.execute("ROLLBACK TO SAVEPOINT s")


def test_las_anclas_solo_avanzan(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    worm.exportar(conn, escenario["p1"], destino, "prueba")
    _motor(conn)
    conn.execute(
        "SET LOCAL session_replication_role = origin"
    )  # con «replica» no disparan los triggers
    t = escenario["a"].tenant
    for sql, args in (
        ("DELETE FROM audit.chain_anchors WHERE tenant_id = %s", (t,)),
        ("UPDATE audit.chain_anchors SET eventos_descartados = 1 WHERE tenant_id = %s", (t,)),
        ("TRUNCATE audit.chain_anchors", ()),
    ):
        conn.execute("SAVEPOINT s")
        with pytest.raises(errors.RaiseException):
            conn.execute(sql, args)  # type: ignore[call-overload]
        conn.execute("ROLLBACK TO SAVEPOINT s")


def test_la_aplicacion_no_ve_los_comprobantes_ni_puede_anclar_ni_exportar(
    conn: psycopg.Connection, escenario: dict[str, Any]
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_app")
    for sql in (
        "SELECT * FROM audit.worm_exports",
        "SELECT audit.digest_particion('audit.audit_events_default'::regclass)",
        f"SELECT audit.anclar_descarte('{escenario['p1']}'::regclass)",
        "INSERT INTO audit.chain_anchors (tenant_id, eventos_descartados, ultimo_sello_descartado) VALUES ('x', 1, repeat('a', 64))",
    ):
        conn.execute("SAVEPOINT s")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql)  # type: ignore[call-overload]
        conn.execute("ROLLBACK TO SAVEPOINT s")


def test_anclar_sin_comprobante_falla(conn: psycopg.Connection, escenario: dict[str, Any]) -> None:
    with pytest.raises(errors.RaiseException, match="sin comprobante"):
        conn.execute("SELECT audit.anclar_descarte(%s::regclass)", (escenario["p1"],))


def test_la_vista_de_particiones_muestra_cuales_tienen_comprobante(
    conn: psycopg.Connection, escenario: dict[str, Any], destino: Path
) -> None:
    r = worm.exportar(conn, escenario["p1"], destino, "prueba")
    filas = {
        str(p): ex
        for p, ex in conn.execute(
            "SELECT particion, ultimo_export_id FROM audit.v_particiones_worm WHERE particion = ANY(%s)",
            ([escenario["p1"], escenario["p2"]],),
        ).fetchall()
    }
    assert filas == {escenario["p1"]: r["export_id"], escenario["p2"]: None}


def test_la_politica_vigente_de_auditoria_sigue_siendo_conservar(conn: psycopg.Connection) -> None:
    """El mecanismo no cambia la decisión de negocio: sin una política que lo ordene, la auditoría no se purga."""
    fila = conn.execute(
        "SELECT action FROM core.retention_policies WHERE entity_name = 'audit.audit_events' AND tenant_id IS NULL"
    ).fetchone()
    assert fila == ("conservar",)
