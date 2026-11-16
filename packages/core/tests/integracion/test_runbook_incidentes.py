"""Runbook de incidentes de base de datos (KC-DB-11-5).

Criterios del ticket:
  1. Cada procedimiento incluye síntoma, diagnóstico, contención, resolución y verificación, y está vinculado a la alerta
     que lo dispara.
  2. Los seis procedimientos están ensayados en el entorno de demostración con acta, incluida una cadena de auditoría rota
     provocada a propósito.

Aquí se prueba lo determinista: el catálogo de alertas (core.alertas_db), la coherencia entre el runbook y las alertas, y
las dos operaciones nuevas de la herramienta WORM (comparar y restaurar). El ensayo completo con Docker
(db/tools/ensayo_incidentes.py) tarda minutos y se ejecuta solo con KC_INCIDENTES_ENSAYO=1 (y en el flujo programado).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import stat
import sys
import threading
import time
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
RUNBOOK = RAIZ / "docs" / "runbook_incidentes_db.md"
ACTA = RAIZ / "docs" / "runbook_incidentes_db_acta.md"
GENESIS = "0" * 64
ALERTAS = ["DB-A01", "DB-A02", "DB-A03", "DB-A04", "DB-A05", "DB-A06"]


def _cargar(nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / "db" / "tools" / f"{nombre}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


worm = _cargar("worm_auditoria")


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _dsn() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    return _normalizar_dsn(url)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        c = psycopg.connect(_dsn(), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    if c.execute("SELECT to_regproc('core.alertas_db')").fetchone() == (None,):
        c.close()
        pytest.skip("migración 065 sin aplicar")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _alertas(c: psycopg.Connection, **kw: Any) -> dict[str, dict[str, Any]]:
    p = {"u_bloqueo": 30, "u_con": 0.8, "u_idle": 300, "u_hinch": 30, "integridad": False}
    p.update(kw)
    filas = c.execute(
        "SELECT alerta, nombre, severidad, evaluada, activa, valor, umbral, detalle, runbook "
        "FROM core.alertas_db(%s::integer, %s::numeric, %s::integer, %s::numeric, %s::boolean)",
        (p["u_bloqueo"], p["u_con"], p["u_idle"], p["u_hinch"], p["integridad"]),
    ).fetchall()
    return {
        f[0]: dict(
            zip(
                (
                    "nombre",
                    "severidad",
                    "evaluada",
                    "activa",
                    "valor",
                    "umbral",
                    "detalle",
                    "runbook",
                ),
                f[1:],
                strict=True,
            )
        )
        for f in filas
    }


# ---------------------------------------------------------------------------------------------------------
# El catálogo de alertas
# ---------------------------------------------------------------------------------------------------------
def test_el_catalogo_define_las_seis_alertas_con_severidad_y_runbook(
    conn: psycopg.Connection,
) -> None:
    a = _alertas(conn)
    assert list(a) == ALERTAS
    assert {k: v["severidad"] for k, v in a.items()} == {
        "DB-A01": "critica", "DB-A02": "critica", "DB-A03": "critica", "DB-A04": "alta", "DB-A05": "media", "DB-A06": "alta",
    }  # fmt: skip
    assert [a[k]["runbook"] for k in ALERTAS] == [
        f"docs/runbook_incidentes_db.md#i{n}" for n in range(1, 7)
    ]


def test_las_alertas_de_integridad_no_se_dan_por_verdes_sin_evaluarse(
    conn: psycopg.Connection,
) -> None:
    a = _alertas(conn)
    for k in ("DB-A03", "DB-A04"):
        assert (
            a[k]["evaluada"] is False and a[k]["activa"] is None
        )  # «no evaluada» no es «sin problemas»
        assert "p_integridad" in a[k]["detalle"]
    b = _alertas(conn, integridad=True)
    assert b["DB-A03"]["evaluada"] is True and b["DB-A04"]["evaluada"] is True
    assert isinstance(b["DB-A03"]["activa"], bool) and isinstance(b["DB-A04"]["activa"], bool)


def test_el_rol_administrador_tiene_pg_monitor_para_ver_las_sesiones_de_otros_roles(
    conn: psycopg.Connection,
) -> None:
    assert conn.execute(
        "SELECT pg_has_role('kallicode_admin', 'pg_read_all_stats', 'member')"
    ).fetchone() == (True,)
    a = _alertas(conn)
    assert a["DB-A01"]["evaluada"] is True and a["DB-A02"]["evaluada"] is True  # no «sin permiso»


def test_solo_el_administrador_puede_evaluar_las_alertas(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE kallicode_app")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT * FROM core.alertas_db()")
    conn.execute("RESET ROLE")
    conn.execute("SET LOCAL ROLE kallicode_worker")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT * FROM core.alertas_db()")


def test_db_a02_salta_por_ratio_de_conexiones_o_por_transaccion_colgada(
    conn: psycopg.Connection,
) -> None:
    # umbral de ratio casi nulo: cualquier conexión lo supera
    assert _alertas(conn, u_con=0.0001)["DB-A02"]["activa"] is True
    # umbral de ratio imposible y sin transacciones colgadas: apagada (esta conexión acaba de abrir su transacción)
    assert _alertas(conn, u_con=2.0, u_idle=10_000)["DB-A02"]["activa"] is False


def test_db_a06_detecta_filas_en_una_particion_default_y_se_apaga_al_vaciarla(
    conn: psycopg.Connection,
) -> None:
    assert _alertas(conn)["DB-A06"]["activa"] is False, (
        "la base de pruebas no debería tener filas en una DEFAULT"
    )
    s = uuid.uuid4().hex[:8]
    t = f"org_a06_{s}"
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
    conn.execute(
        "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
        (f"u_{s}", t, f"{s}@a06.dev"),
    )
    conn.execute(
        "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, creada_en) VALUES (%s, %s, %s, 'sistema', 'x', %s)",
        (f"nt_{s}", t, f"u_{s}", dt.datetime(2045, 3, 1, tzinfo=dt.UTC)),
    )
    a = _alertas(conn)["DB-A06"]
    assert a["activa"] is True and a["valor"] == 1 and "core.notifications_default" in a["detalle"]
    conn.execute("DELETE FROM core.notifications WHERE id = %s", (f"nt_{s}",))
    assert _alertas(conn)["DB-A06"]["activa"] is False


def test_db_a01_ve_a_la_sesion_que_espera_un_bloqueo_y_se_apaga_al_liberarla() -> None:
    dsn = _dsn()
    titular = psycopg.connect(dsn, autocommit=False)
    espera: dict[str, Any] = {}
    try:
        titular.execute("LOCK TABLE core.organizations IN ACCESS EXCLUSIVE MODE")

        def esperar() -> None:
            c = psycopg.connect(dsn, autocommit=True)
            c.execute("SET lock_timeout = '15s'")
            try:
                c.execute("SELECT count(*) FROM core.organizations").fetchone()
                espera["fin"] = "liberada"
            except psycopg.Error as exc:
                espera["fin"] = type(exc).__name__
            finally:
                c.close()

        h = threading.Thread(target=esperar, daemon=True)
        h.start()
        time.sleep(1.5)
        with psycopg.connect(dsn, autocommit=True) as obs:
            a = _alertas(obs, u_bloqueo=1)["DB-A01"]
        assert a["evaluada"] is True and a["activa"] is True and a["valor"] >= 1
        assert str(titular.info.backend_pid) in a["detalle"]  # nombra al bloqueante
    finally:
        titular.rollback()
        titular.close()
    h.join(timeout=20)
    assert espera.get("fin") == "liberada"
    with psycopg.connect(dsn, autocommit=True) as obs:
        assert _alertas(obs, u_bloqueo=1)["DB-A01"]["activa"] is False


# ---------------------------------------------------------------------------------------------------------
# El runbook y sus alertas (criterio 1)
# ---------------------------------------------------------------------------------------------------------
def _secciones() -> dict[str, str]:
    texto = RUNBOOK.read_text(encoding="utf-8")
    partes = re.split(r'<a id="(i[1-6])"></a>', texto)
    return {partes[i]: partes[i + 1] for i in range(1, len(partes) - 1, 2)}


def test_el_runbook_tiene_las_seis_secciones() -> None:
    assert sorted(_secciones()) == ["i1", "i2", "i3", "i4", "i5", "i6"]


@pytest.mark.parametrize("seccion", ["i1", "i2", "i3", "i4", "i5", "i6"])
def test_cada_procedimiento_tiene_sintoma_diagnostico_contencion_resolucion_y_verificacion(
    seccion: str,
) -> None:
    cuerpo = _secciones()[seccion]
    for campo in ("**Síntoma", "**Diagnóstico", "**Contención", "**Resolución", "**Verificación"):
        assert campo in cuerpo, f"{seccion} no tiene «{campo}»"


@pytest.mark.parametrize(("seccion", "alerta"), [(f"i{n}", f"DB-A0{n}") for n in range(1, 7)])
def test_cada_procedimiento_nombra_la_alerta_que_lo_dispara_y_la_verifica_apagada(
    seccion: str, alerta: str
) -> None:
    cuerpo = _secciones()[seccion]
    assert alerta in cuerpo.split("**Síntoma")[0]  # la alerta figura en el título de la sección
    assert re.search(rf"alerta\s*=\s*'{alerta}'", cuerpo), (
        "la verificación debe comprobar que la alerta se apaga"
    )


def test_la_columna_runbook_de_cada_alerta_apunta_a_una_seccion_que_existe(
    conn: psycopg.Connection,
) -> None:
    secciones = _secciones()
    for alerta, fila in _alertas(conn).items():
        ancla = fila["runbook"].split("#")[1]
        assert ancla in secciones, f"{alerta} apunta a #{ancla}, que no existe en el runbook"
        assert alerta in RUNBOOK.read_text(encoding="utf-8")


def test_el_runbook_declara_lo_que_falta() -> None:
    texto = RUNBOOK.read_text(encoding="utf-8")
    cola = texto.split('<a id="que-falta"></a>')[1]
    for tema in (
        "no avisan a nadie",
        "PgBouncer",
        "quién",
        "no restaura eventos borrados",
        "no ensayada",
    ):
        assert tema in cola, tema


def test_el_acta_recoge_los_seis_incidentes_y_la_cadena_provocada() -> None:
    acta = ACTA.read_text(encoding="utf-8")
    for n in range(1, 7):
        assert f"I{n} ·" in acta
    assert "provocada a propósito" in acta and "LOS SEIS INCIDENTES SE ENSAYAN Y VERIFICAN" in acta


# ---------------------------------------------------------------------------------------------------------
# Herramienta WORM: comparar y restaurar (diagnóstico y resolución de I3)
# ---------------------------------------------------------------------------------------------------------
class Cadena:
    """Cadena de eventos de un tenant construida a mano (sin disparadores) y sellada con audit.calcular_sello."""

    def __init__(self, c: psycopg.Connection, tenant: str) -> None:
        self.c, self.tenant, self.ultimo, self.n = c, tenant, GENESIS, 0

    def evento(self, cuando: str, texto: str) -> int:
        fila = self.c.execute(
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, datos, sello_previo, sello, creado_en, "
            "version_sello, ip, user_agent, request_id) VALUES (%s, 'sistema', 'prueba_runbook', %s, %s::jsonb, %s, %s, %s, 2, "
            "'10.0.0.7', 'agente/1', %s) RETURNING id",
            (
                self.tenant,
                texto,
                json.dumps({"k": texto}),
                self.ultimo,
                "a" * 64,
                cuando,
                f"req-{uuid.uuid4().hex[:8]}",
            ),
        ).fetchone()
        assert fila is not None
        sello = self.c.execute(
            "UPDATE audit.audit_events e SET sello = audit.calcular_sello(e) WHERE e.id = %s RETURNING sello",
            (fila[0],),
        ).fetchone()
        assert sello is not None
        self.ultimo, self.n = str(sello[0]), self.n + 1
        self.c.execute(
            "INSERT INTO audit.chain_heads (tenant_id, ultimo_sello, eventos) VALUES (%s, %s, %s) "
            "ON CONFLICT (tenant_id) DO UPDATE SET ultimo_sello = EXCLUDED.ultimo_sello, eventos = EXCLUDED.eventos",
            (self.tenant, self.ultimo, self.n),
        )
        return int(fila[0])


@pytest.fixture
def exportado(conn: psycopg.Connection, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Una partición de 2024 con la cadena de un tenant, exportada y registrada."""
    conn.execute("SET LOCAL session_replication_role = replica")
    conn.execute("SET LOCAL TIME ZONE 'UTC'")
    s = uuid.uuid4().hex[:8]
    particion = f"audit.zz_rb_{s}"
    conn.execute(
        f"CREATE TABLE {particion} PARTITION OF audit.audit_events FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')"
    )  # type: ignore[call-overload]
    cadena = Cadena(conn, f"org_rb_{s}")
    ids = [cadena.evento(f"2024-01-{10 + i} 10:00:00+00", f"evento {i}") for i in range(5)]
    exp = worm.exportar(conn, particion, tmp_path, "prueba")
    sha = conn.execute(
        "SELECT manifiesto_sha256 FROM audit.worm_exports WHERE id = %s", (exp["export_id"],)
    ).fetchone()[0]
    yield {
        "tenant": cadena.tenant,
        "ids": ids,
        "manifiesto": Path(exp["manifiesto"]),
        "sha": sha,
        "particion": particion,
    }
    for f in tmp_path.rglob("*"):  # los exports quedan de solo lectura
        if f.is_file():
            f.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _alterar(c: psycopg.Connection, evento: int, texto: str = "ALTERADO") -> None:
    c.execute("SET LOCAL session_replication_role = replica")
    c.execute("UPDATE audit.audit_events SET resumen = %s WHERE id = %s", (texto, evento))


def _integra(c: psycopg.Connection, tenant: str) -> bool:
    fila = c.execute("SELECT integra FROM audit.verify_chain(%s)", (tenant,)).fetchone()
    return bool(fila and fila[0])


def test_comparar_no_encuentra_diferencias_en_una_particion_intacta(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    d = worm.comparar_con_export(conn, exportado["manifiesto"])
    assert d["alterados"] == [] and d["borrados"] == [] and d["anadidos"] == []


def test_comparar_dice_que_evento_y_que_columnas_se_alteraron(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    objetivo = exportado["ids"][2]
    _alterar(conn, objetivo)
    d = worm.comparar_con_export(conn, exportado["manifiesto"])
    assert d["alterados"] == [{"id": objetivo, "columnas": ["resumen"]}]
    assert not _integra(conn, exportado["tenant"])  # y la cadena lo confirma


def test_comparar_distingue_borrados_y_anadidos(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    conn.execute("SET LOCAL session_replication_role = replica")
    conn.execute("DELETE FROM audit.audit_events WHERE id = %s", (exportado["ids"][4],))
    cadena = Cadena(conn, "org_rb_otro")
    nuevo = cadena.evento("2024-01-20 10:00:00+00", "posterior al export")
    d = worm.comparar_con_export(conn, exportado["manifiesto"])
    assert (
        d["borrados"] == [exportado["ids"][4]] and d["anadidos"] == [nuevo] and d["alterados"] == []
    )


def test_restaurar_devuelve_el_evento_a_su_contenido_original_y_la_cadena_vuelve_a_ser_integra(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    objetivo = exportado["ids"][3]
    original = conn.execute(
        "SELECT resumen FROM audit.audit_events WHERE id = %s", (objetivo,)
    ).fetchone()[0]
    _alterar(conn, objetivo)
    assert not _integra(conn, exportado["tenant"])
    res = worm.restaurar_evento(conn, exportado["manifiesto"], objetivo, exportado["sha"])
    assert res["evento"] == objetivo and "resumen" in res["columnas_restauradas"]
    assert conn.execute(
        "SELECT resumen FROM audit.audit_events WHERE id = %s", (objetivo,)
    ).fetchone() == (original,)
    assert _integra(conn, exportado["tenant"])
    assert worm.comparar_con_export(conn, exportado["manifiesto"])["alterados"] == []


def test_restaurar_se_niega_si_el_export_no_es_el_registrado(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    _alterar(conn, exportado["ids"][1])
    with pytest.raises(ValueError, match="no es fiable"):
        worm.restaurar_evento(
            conn, exportado["manifiesto"], exportado["ids"][1], "f" * 64
        )  # hash de manifiesto que no es el registrado
    assert not _integra(conn, exportado["tenant"])  # no se tocó nada


def test_restaurar_se_niega_si_el_export_fue_manipulado(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    archivo = (
        exportado["manifiesto"].parent
        / json.loads(exportado["manifiesto"].read_text(encoding="utf-8"))["archivo"]
    )
    archivo.chmod(stat.S_IRUSR | stat.S_IWUSR)
    archivo.write_text(
        archivo.read_text(encoding="utf-8").replace("evento 1", "evento X"), encoding="utf-8"
    )
    _alterar(conn, exportado["ids"][1])
    with pytest.raises(ValueError, match="no es fiable"):
        worm.restaurar_evento(conn, exportado["manifiesto"], exportado["ids"][1], exportado["sha"])


def test_restaurar_se_niega_y_deshace_si_el_dano_no_era_solo_ese_evento(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    _alterar(conn, exportado["ids"][1], "ALTERADO 1")
    _alterar(conn, exportado["ids"][3], "ALTERADO 3")
    with pytest.raises(ValueError, match="sigue rota"):
        worm.restaurar_evento(conn, exportado["manifiesto"], exportado["ids"][1], exportado["sha"])


def test_restaurar_se_niega_con_un_evento_que_no_esta_en_el_export(
    conn: psycopg.Connection, exportado: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="no está en el export"):
        worm.restaurar_evento(conn, exportado["manifiesto"], 999_999_999, exportado["sha"])


# ---------------------------------------------------------------------------------------------------------
# El ensayo completo (Docker)
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("KC_INCIDENTES_ENSAYO") != "1",
    reason="ensayo con Docker: exportar KC_INCIDENTES_ENSAYO=1",
)
def test_el_ensayo_completo_de_los_seis_incidentes_pasa() -> None:
    import subprocess

    r = subprocess.run(
        [sys.executable, str(RAIZ / "db" / "tools" / "ensayo_incidentes.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        timeout=1500,
    )
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]
    assert "LOS SEIS INCIDENTES SE ENSAYAN Y VERIFICAN" in r.stdout
