"""Aceptación de GA: ensayo de migración en staging y smoke de release (KC-DB-12-5).

Criterios del ticket:
  1. Las migraciones aplican en orden sobre staging dentro del presupuesto, y el smoke verifica versión de esquema, los 26
     chequeos en cero, la cadena íntegra y cero tablas sin política (core.smoke_release, db/tools/smoke_release.sql).
  2. Segundo ensayo PITR con RTO dentro del objetivo y acta de aceptación firmada por el Product Owner, el Architecture Board
     y el Tech Lead de Testing (db/tools/ensayo_despliegue.py; las firmas son humanas y quedan PENDIENTES en el acta).

Aquí se prueban las piezas deterministas: la función del smoke con cada tipo de fallo provocado (siempre en una transacción
que se revierte), el presupuesto, los datos del artefacto y la coherencia de los documentos. El ensayo completo con Docker
(minutos) se ejecuta solo con KC_GA_ENSAYO=1.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
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
ACTA = RAIZ / "docs" / "aceptacion_ga_acta.md"
PROCEDIMIENTO = RAIZ / "docs" / "aceptacion_ga_esquema.md"
EXPEDIENTE = RAIZ / "docs" / "release" / "expediente.md"


def _cargar(nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / "db" / "tools" / f"{nombre}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


despliegue = _cargar("ensayo_despliegue")


def _normalizar(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _dsn(variable: str = "KC_DATABASE_URL_MIGRATIONS") -> str:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")
    return _normalizar(url)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        c = psycopg.connect(_dsn(), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    if c.execute("SELECT to_regproc('core.smoke_release')").fetchone() == (None,):
        c.close()
        pytest.skip("migración 070 sin aplicar")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _smoke(c: psycopg.Connection, **kw: Any) -> dict[str, tuple[bool, str]]:
    a = despliegue.datos_del_artefacto()
    p = {
        "version": a["version_esperada"],
        "ultima": a["ultima_migracion"],
        "n": a["migraciones"],
        "chequeos": 26,
    }
    p.update(kw)
    filas = c.execute(
        "SELECT chequeo, ok, detalle FROM core.smoke_release(%s, %s, %s, %s)",
        (p["version"], p["ultima"], p["n"], p["chequeos"]),
    ).fetchall()
    return {f[0]: (f[1], f[2]) for f in filas}


# ---------------------------------------------------------------------------------------------------------
# core.smoke_release: el camino feliz y cada tipo de fallo
# ---------------------------------------------------------------------------------------------------------
def test_el_smoke_pasa_en_una_base_con_todas_las_migraciones(conn: psycopg.Connection) -> None:
    r = _smoke(conn)
    assert set(r) == {
        "version_esquema", "migraciones_aplicadas", "ledger_en_orden", "integridad_26_chequeos", "cadena_auditoria",
        "tablas_sin_politica_retencion", "tablas_sin_politica_recuperacion", "aislamiento_sin_incumplimientos", "particiones_default_vacias",
    }  # fmt: skip
    # Las comprobaciones que NO dependen de los datos que otras pruebas hayan dejado pasan siempre.
    estaticas = (
        "version_esquema", "migraciones_aplicadas", "ledger_en_orden", "tablas_sin_politica_retencion",
        "tablas_sin_politica_recuperacion", "aislamiento_sin_incumplimientos",
    )  # fmt: skip
    assert [k for k in estaticas if not r[k][0]] == [], {k: r[k][1] for k in estaticas}
    # Las que sí dependen de los datos (el job de backend corre las pruebas de la API antes y deja organizaciones sin
    # suscripción) dan EXACTAMENTE lo que dan las consultas directas: el smoke ni inventa ni oculta nada.
    hallazgos = conn.execute(
        "SELECT count(*), coalesce(sum(problemas), 0) FROM core.validar_integridad()"
    ).fetchone()
    assert r["integridad_26_chequeos"][0] is (hallazgos == (26, 0))
    assert r["cadena_auditoria"][0] is (
        conn.execute(
            "SELECT count(*) FROM audit.chain_heads h WHERE NOT EXISTS (SELECT 1 FROM audit.verify_chain(h.tenant_id) c WHERE c.integra)"
        ).fetchone()
        == (0,)
    )


def test_una_version_equivocada_o_una_migracion_distinta_falla(conn: psycopg.Connection) -> None:
    assert _smoke(conn, version="v9.9.9")["version_esquema"][0] is False
    assert _smoke(conn, ultima="999_otra.sql")["version_esquema"][0] is False
    assert (
        "v9.9.9" in _smoke(conn, version="v9.9.9")["version_esquema"][1]
    )  # y dice qué se esperaba


def test_un_numero_de_migraciones_distinto_del_del_artefacto_falla(
    conn: psycopg.Connection,
) -> None:
    n = despliegue.datos_del_artefacto()["migraciones"]
    assert _smoke(conn, n=n + 1)["migraciones_aplicadas"][0] is False
    assert (
        _smoke(conn, n=None)["migraciones_aplicadas"][0] is True
    )  # sin número esperado no se exige


def test_una_migracion_aplicada_antes_que_otra_anterior_se_detecta(
    conn: psycopg.Connection,
) -> None:
    conn.execute(
        "UPDATE core.schema_migrations SET aplicado_en = now() + interval '1 day' WHERE archivo = (SELECT min(archivo) FROM core.schema_migrations)"
    )
    ok, detalle = _smoke(conn)["ledger_en_orden"]
    assert ok is False and "aplicada(s) antes" in detalle


def test_un_hallazgo_de_integridad_se_detecta_y_se_nombra(conn: psycopg.Connection) -> None:
    previos = conn.execute(
        "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V01_suscripcion_vigente'"
    ).fetchone()
    assert previos is not None
    conn.execute(
        "INSERT INTO core.organizations (id, nombre) VALUES ('org_ga_sin_suscripcion', 'x')"
    )  # V01, uno más que los que ya hubiera
    ok, detalle = _smoke(conn)["integridad_26_chequeos"]
    assert ok is False and f"V01_suscripcion_vigente={previos[0] + 1}" in detalle


def test_un_numero_de_chequeos_distinto_del_esperado_falla(conn: psycopg.Connection) -> None:
    assert (
        _smoke(conn, chequeos=27)["integridad_27_chequeos"][0] is False
    )  # un chequeo desaparecido no puede pasar inadvertido


def test_una_cadena_de_auditoria_rota_se_detecta_en_cualquier_tenant(
    conn: psycopg.Connection,
) -> None:
    s = uuid.uuid4().hex[:8]
    t = f"org_ga_{s}"
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
    conn.execute(
        "INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el) VALUES (%s, %s, 'growth', current_date - 5, current_date + 25)",
        (f"sub_{t}", t),
    )
    ids = []
    for i in range(4):
        fila = conn.execute(
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, ip, user_agent, request_id) "
            "VALUES (%s, 'sistema', 'prueba_ga', %s, repeat('0', 64), repeat('a', 64), '10.0.0.7', 'ga/1', %s) RETURNING id",
            (t, f"evento {i}", f"req-ga-{s}-{i}"),
        ).fetchone()
        assert fila is not None
        ids.append(fila[0])
    antes = _smoke(conn)["cadena_auditoria"][
        1
    ]  # íntegra mientras nadie la toca: ninguna rota nueva
    rotas_antes = int(antes.split(" rota(s)")[0].rsplit(" ", 1)[1])
    assert t not in antes
    conn.execute("SET LOCAL session_replication_role = replica")
    conn.execute("UPDATE audit.audit_events SET resumen = 'ALTERADO' WHERE id = %s", (ids[1],))
    conn.execute("SET LOCAL session_replication_role = origin")
    ok, detalle = _smoke(conn)["cadena_auditoria"]
    assert ok is False and t in detalle and f"{rotas_antes + 1} rota(s)" in detalle


def test_una_tabla_nueva_sin_politica_se_detecta_en_las_tres_listas(
    conn: psycopg.Connection,
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute(
        "CREATE TABLE cm.zz_sin_politica (tenant_id text)"
    )  # sin RLS, sin excepción, sin retención ni recuperación
    r = _smoke(conn)
    assert r["tablas_sin_politica_retencion"][0] is False
    assert r["tablas_sin_politica_recuperacion"][0] is False
    assert r["aislamiento_sin_incumplimientos"][0] is False


def test_una_fila_en_una_particion_default_se_detecta(conn: psycopg.Connection) -> None:
    s = uuid.uuid4().hex[:8]
    t = f"org_ga_d_{s}"
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
    conn.execute(
        "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
        (f"u_{s}", t, f"{s}@ga.dev"),
    )
    conn.execute(
        "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, creada_en) VALUES (%s, %s, %s, 'sistema', 'x', %s)",
        (f"nt_{s}", t, f"u_{s}", dt.datetime(2046, 1, 1, tzinfo=dt.UTC)),
    )
    ok, detalle = _smoke(conn)["particiones_default_vacias"]
    assert ok is False and "notifications_default" in detalle


def test_solo_el_administrador_puede_ejecutar_el_smoke(conn: psycopg.Connection) -> None:
    for rol in ("kallicode_app", "kallicode_worker"):
        conn.execute(f"SET LOCAL ROLE {rol}")  # type: ignore[call-overload]
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT * FROM core.smoke_release('v2.0.0', 'x.sql')")
        conn.execute("RESET ROLE")


# ---------------------------------------------------------------------------------------------------------
# El script de smoke
# ---------------------------------------------------------------------------------------------------------
def test_el_script_de_smoke_sale_con_error_si_algo_falla() -> None:
    psql = shutil.which("psql")
    if psql is None:
        pytest.skip("no hay psql")
    a = despliegue.datos_del_artefacto()
    r = subprocess.run(
        [psql, _dsn(), "-v", "ON_ERROR_STOP=1", "-v", "version_esperada=v9.9.9", "-v", f"ultima_migracion={a['ultima_migracion']}", "-v", f"migraciones={a['migraciones']}", "-f", str(RAIZ / "db" / "tools" / "smoke_release.sql")],
        capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip
    assert r.returncode != 0 and "comprobación(es) fallan" in r.stderr


def test_el_script_de_smoke_llama_a_la_funcion_y_documenta_sus_parametros() -> None:
    texto = (RAIZ / "db" / "tools" / "smoke_release.sql").read_text(encoding="utf-8")
    assert (
        "core.smoke_release(:'version_esperada', :'ultima_migracion', :migraciones, :chequeos)"
        in texto
    )
    for parametro in ("version_esperada", "ultima_migracion", "migraciones"):
        assert f"-v {parametro}=" in texto


def test_el_ci_ejecuta_el_smoke_de_release() -> None:
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "db/tools/smoke_release.sql" in ci and "test_aceptacion_ga.py" in ci


# ---------------------------------------------------------------------------------------------------------
# Presupuesto y datos del artefacto (puros)
# ---------------------------------------------------------------------------------------------------------
def test_los_datos_del_artefacto_salen_del_repositorio() -> None:
    a = despliegue.datos_del_artefacto()
    archivos = sorted(p.name for p in (RAIZ / "db" / "migrations").glob("*.sql"))
    assert a["migraciones"] == len(archivos) and a["ultima_migracion"] == archivos[-1]
    assert (
        a["version_esperada"]
        == json.loads((RAIZ / "db" / "esquema_v2.snapshot.json").read_text(encoding="utf-8"))[
            "etiqueta"
        ]
    )


def test_el_presupuesto_acepta_lo_que_cabe_y_senala_lo_que_no() -> None:
    bien = despliegue.evaluar_presupuesto({"001_a.sql": 500, "002_b.sql": 2_300}, total_pasos_s=3.0)
    assert (
        bien["dentro_del_presupuesto"]
        and bien["mas_lenta"] == "002_b.sql"
        and bien["suma_s"] == 2.8
    )
    lenta = despliegue.evaluar_presupuesto(
        {"001_a.sql": 500, "002_lenta.sql": 45_000}, total_pasos_s=46.0
    )
    assert not lenta["dentro_del_presupuesto"] and lenta["migraciones_fuera_de_presupuesto"] == {
        "002_lenta.sql": 45.0
    }
    larga = despliegue.evaluar_presupuesto(
        {f"{i:03d}_x.sql": 20_000 for i in range(8)}, total_pasos_s=160.0
    )  # ninguna lenta, pero la suma sí
    assert not larga["dentro_del_presupuesto"] and larga["migraciones_fuera_de_presupuesto"] == {}


def test_el_presupuesto_cubre_con_holgura_lo_que_cuesta_hoy(conn: psycopg.Connection) -> None:
    """El presupuesto propuesto no es una cifra caprichosa: la base de pruebas, con todas las migraciones, cabe de sobra."""
    filas = conn.execute("SELECT archivo, duracion_ms FROM core.schema_migrations").fetchall()
    r = despliegue.evaluar_presupuesto({f[0]: f[1] for f in filas}, total_pasos_s=0.0)
    assert r["dentro_del_presupuesto"], r
    assert r["suma_s"] < despliegue.PRESUPUESTO_TOTAL_S / 3  # al menos tres veces de margen


# ---------------------------------------------------------------------------------------------------------
# Documentos: el procedimiento, el acta y el expediente de release
# ---------------------------------------------------------------------------------------------------------
def test_el_procedimiento_cubre_los_pasos_del_ticket_y_declara_sus_limites() -> None:
    texto = PROCEDIMIENTO.read_text(encoding="utf-8")
    for paso in (
        "Backup previo",
        "Migraciones como paso separado",
        "Smoke de release",
        "Vuelta atrás",
        "Segundo ensayo de restauración",
        "Firmas",
    ):
        assert paso in texto, paso
    for dato in (
        "smoke_release.sql",
        "smoke_post_deploy.sql",
        "migrate.py",
        "ensayo_despliegue.py",
        "recuperacion_pitr.md",
    ):
        assert dato in texto, dato
    for limite in ("no es el staging real", "propuesta", "no firma"):
        assert limite in texto.lower(), limite


def test_el_acta_recoge_el_ensayo_y_deja_las_firmas_pendientes() -> None:
    acta = ACTA.read_text(encoding="utf-8")
    assert "Criterio 1" in acta and "Criterio 2" in acta and "RTO medido" in acta
    assert "PENDIENTE DE FIRMAS" in acta
    for rol in ("Product Owner", "Architecture Board", "Tech Lead de Testing"):
        assert rol in acta
    assert (
        acta.count("_pendiente_") >= 12
    )  # nadie ha firmado: ni este ensayo ni quien lo preparó puede hacerlo por otros
    assert "No es el staging real" in acta


def test_el_expediente_de_release_enlaza_las_evidencias_de_base_de_datos() -> None:
    texto = EXPEDIENTE.read_text(encoding="utf-8")
    assert "Base de datos y seguridad" in texto
    for enlace in (
        "aceptacion_ga_acta.md",
        "auditoria_rls.md",
        "recuperacion_pitr_acta.md",
        "politica-cambios-esquema.md",
        "modelo_datos_v2.md",
    ):
        assert enlace in texto, enlace
    assert "pendiente" in texto.lower()  # el estado de las firmas es visible


def test_el_markdown_del_acta_no_da_nada_por_firmado() -> None:
    acta: dict[str, Any] = {
        "ejecutado_en": "2026-11-18T00:00:00+00:00", "version_postgres": "16.x", "duracion_total_s": 1.0, "desde": "060_x.sql",
        "artefacto": {"version_esperada": "v2.0.0", "ultima_migracion": "070_x.sql", "migraciones": 68},
        "cumple_criterios_tecnicos": True, "ok_migraciones": True,
        "pasos": {"migraciones": {"en_el_ledger": 68, "las_del_artefacto": 68, "aplicadas_en_este_paso": 8, "ledger_coincide_con_el_artefacto": True},
                  "base_persistente": {"ok": True, "eventos_antes_del_reinicio": 10, "eventos_tras_el_reinicio": 10}},
        "presupuesto": {"dentro_del_presupuesto": True, "suma_s": 1.0, "presupuesto_total_s": 120, "mas_lenta": "a.sql", "mas_lenta_s": 0.5, "presupuesto_por_migracion_s": 30, "paso_de_despliegue_s": 2.0},
        "smoke": {"admin_ok": True, "aplicacion_ok": True, "control_negativo_falla_con_version_equivocada": True, "comprobaciones": ["x | OK | y"]},
        "objetivos": {"rto_minutos": 60, "rpo_minutos": 5, "estado": "propuesto"},
        "restauracion_posterior_al_despliegue": {"rto_segundos": 3.0, "rto_dentro_del_objetivo": True, "smoke": True, "ultima_migracion": "070_x.sql", "eventos_restaurados": 5, "eventos_esperados": 5, "verificacion": {"ok": True, "tenants_con_cadena": 3, "cadenas_rotas": 0, "hallazgos": 0}},
        "restauracion_al_instante_previo": {"rto_segundos": 3.0, "rto_dentro_del_objetivo": True, "ultima_migracion": "060_x.sql", "eventos_restaurados": 4, "eventos_esperados": 4, "verificacion": {"ok": True, "tenants_con_cadena": 3, "cadenas_rotas": 0, "hallazgos": 0}},
    }  # fmt: skip
    md = despliegue.markdown(acta)
    assert "PENDIENTE DE FIRMAS" in md and "CUMPLE los criterios técnicos" in md
    assert md.count("_pendiente_") >= 12 and "Anderson González" in md
    assert "propuesto" in md  # el objetivo de RTO sin aprobar se dice


# ---------------------------------------------------------------------------------------------------------
# El ensayo completo (Docker)
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("KC_GA_ENSAYO") != "1", reason="ensayo con Docker: exportar KC_GA_ENSAYO=1"
)
def test_el_ensayo_completo_de_despliegue_cumple_los_criterios_tecnicos() -> None:
    acta = despliegue.ejecutar(eventos=600)
    assert acta["ok_migraciones"], acta["pasos"]["migraciones"]
    assert acta["ok_smoke"], acta["smoke"]
    assert acta["ok_restauracion"], (
        acta["restauracion_posterior_al_despliegue"],
        acta["restauracion_al_instante_previo"],
    )
