"""Auditoría final de RLS, privilegios y roles (KC-DB-12-3), criterio 1.

Informe de las tablas con política, FORCE, privilegios por rol y cláusula TO sin excepciones injustificadas
(core.informe_aislamiento, core.informe_politicas_rls, docs/auditoria_rls.md) y una suite de aislamiento con cobertura
del 100 % de las tablas con política, obligatoria en el pipeline de CI.

Las invariantes salen del CATÁLOGO, no de una lista escrita a mano: una tabla nueva queda cubierta sola, y una sin RLS o
sin justificar rompe la prueba. Las pruebas que provocan un incumplimiento lo hacen dentro de una transacción que SIEMPRE
se revierte.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
ESQUEMAS = ("core", "audit", "vec", "cm")
ROLES = ("kallicode_app", "kallicode_worker")


def _normalizar(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _dsn(variable: str) -> str:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")
    return _normalizar(url)


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    try:
        c = psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    if c.execute("SELECT to_regproc('core.informe_aislamiento')").fetchone() == (None,):
        c.close()
        pytest.skip("migración 069 sin aplicar")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _informe(c: psycopg.Connection) -> list[dict[str, Any]]:
    cols = (
        "tabla",
        "tiene_tenant_id",
        "rls",
        "forzada",
        "politicas",
        "politicas_sin_to",
        "priv_app",
        "priv_worker",
        "particiones_con_acceso",
        "probatoria",
        "estado",
        "justificacion",
    )
    filas = c.execute(
        f"SELECT {', '.join(cols)} FROM core.informe_aislamiento() ORDER BY tabla"
    ).fetchall()  # type: ignore[call-overload]
    return [dict(zip(cols, f, strict=True)) for f in filas]


def _como_admin(c: psycopg.Connection) -> None:
    """Las migraciones crean objetos como kallicode_admin: así reciben los DEFAULT PRIVILEGES que reciben en la realidad."""
    c.execute("SET LOCAL ROLE kallicode_admin")


def _incumplen(c: psycopg.Connection) -> list[str]:
    return [
        f"{t['tabla']}: {t['estado']}"
        for t in _informe(c)
        if str(t["estado"]).startswith("INCUMPLE")
    ]


# ---------------------------------------------------------------------------------------------------------
# El informe: cero incumplimientos
# ---------------------------------------------------------------------------------------------------------
def test_ninguna_tabla_incumple_el_aislamiento(admin: psycopg.Connection) -> None:
    assert _incumplen(admin) == []


def test_ninguna_politica_incumple_y_todas_tienen_clausula_to(admin: psycopg.Connection) -> None:
    filas = admin.execute(
        "SELECT tabla, politica, roles, estado FROM core.informe_politicas_rls()"
    ).fetchall()
    assert filas, "no hay políticas: algo va mal"
    assert [f for f in filas if str(f[3]).startswith("INCUMPLE")] == []
    assert [f for f in filas if f[2] == "PUBLIC"] == []  # ninguna política vale para PUBLIC
    # y directamente en el catálogo, sin pasar por el informe
    assert admin.execute(
        "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE p.polroles = '{0}'::oid[] AND n.nspname IN ('core', 'audit', 'vec', 'cm')"
    ).fetchone() == (0,)


def test_toda_tabla_con_tenant_id_tiene_rls_force_y_politica(admin: psycopg.Connection) -> None:
    con_tenant = [t for t in _informe(admin) if t["tiene_tenant_id"]]
    assert (
        len(con_tenant) >= 60
    )  # el modelo v2 tiene unas 65: si baja a casi nada, el informe está roto
    assert [
        t["tabla"] for t in con_tenant if not (t["rls"] and t["forzada"] and t["politicas"] > 0)
    ] == []


def test_toda_tabla_con_rls_tiene_force(admin: psycopg.Connection) -> None:
    assert admin.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') "
        "AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND c.relrowsecurity AND NOT c.relforcerowsecurity"
    ).fetchone() == (0,)


def test_las_tablas_sin_rls_son_exactamente_las_excepciones_justificadas(
    admin: psycopg.Connection,
) -> None:
    sin_rls = {t["tabla"] for t in _informe(admin) if not t["rls"]}
    justificadas = {
        r[0]
        for r in admin.execute(
            "SELECT entidad FROM core.excepciones_aislamiento WHERE tipo = 'tabla_sin_rls'"
        ).fetchall()
    }
    assert sin_rls == justificadas, (
        f"sin justificar: {sorted(sin_rls - justificadas)}; excepciones obsoletas: {sorted(justificadas - sin_rls)}"
    )


def test_las_politicas_que_no_filtran_por_tenant_son_exactamente_las_justificadas(
    admin: psycopg.Connection,
) -> None:
    no_filtran = {
        f"{f[0]}#{f[1]}"
        for f in admin.execute(
            "SELECT tabla, politica FROM core.informe_politicas_rls() WHERE NOT filtra_por_tenant"
        ).fetchall()
    }
    justificadas = {
        r[0]
        for r in admin.execute(
            "SELECT entidad FROM core.excepciones_aislamiento WHERE tipo = 'politica_sin_filtro_de_tenant'"
        ).fetchall()
    }
    assert no_filtran == justificadas, (
        f"sin justificar: {sorted(no_filtran - justificadas)}; obsoletas: {sorted(justificadas - no_filtran)}"
    )


def test_solo_el_worker_ve_todos_los_tenants_y_solo_en_las_cinco_colas(
    admin: psycopg.Connection,
) -> None:
    filas = admin.execute(
        "SELECT tabla, roles FROM core.informe_politicas_rls() WHERE NOT filtra_por_tenant AND politica = 'worker_colas'"
    ).fetchall()
    assert sorted(f[0] for f in filas) == [
        "core.idempotency_keys",
        "core.jobs",
        "core.refresh_tokens",
        "core.ticket_attachments",
        "core.webhook_inbox",
    ]
    assert {f[1] for f in filas} == {"kallicode_worker"}  # ni la aplicación ni nadie más
    # el resto de políticas sin filtro (la lista de precios) no es de escritura: solo lectura
    assert admin.execute(
        "SELECT comando FROM core.informe_politicas_rls() WHERE tabla = 'core.plan_prices'"
    ).fetchall() == [("SELECT",)]


# ---------------------------------------------------------------------------------------------------------
# Roles y privilegios
# ---------------------------------------------------------------------------------------------------------
def test_solo_kallicode_admin_tiene_bypassrls_fuera_del_superusuario(
    admin: psycopg.Connection,
) -> None:
    con_bypass = {
        r[0]
        for r in admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolbypassrls AND NOT rolsuper"
        ).fetchall()
    }
    assert con_bypass == {"kallicode_admin"}


def test_la_aplicacion_y_el_worker_no_son_privilegiados(admin: psycopg.Connection) -> None:
    for rol in ROLES:
        fila = admin.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles WHERE rolname = %s",
            (rol,),
        ).fetchone()
        assert fila == (False, False, False, False, False), rol
        assert admin.execute(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid WHERE m.member = %s::regrole AND r.rolname LIKE 'pg\\_%%'",
            (rol,),
        ).fetchone() == (0,), f"{rol} es miembro de un rol predefinido de PostgreSQL"
        for esquema in (*ESQUEMAS, "public"):
            assert admin.execute(
                "SELECT has_schema_privilege(%s, %s, 'CREATE')", (rol, esquema)
            ).fetchone() == (False,), f"{rol} puede crear en {esquema}"


def test_la_aplicacion_y_el_worker_no_son_duenos_de_nada(admin: psycopg.Connection) -> None:
    assert admin.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('core', 'audit', 'vec', 'cm', 'public') "
        "AND c.relowner IN ('kallicode_app'::regrole, 'kallicode_worker'::regrole)"
    ).fetchone() == (0,)


def test_ninguna_tabla_da_truncate_trigger_ni_references_a_la_aplicacion_o_el_worker(
    admin: psycopg.Connection,
) -> None:
    malas = admin.execute(
        "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND ("
        " has_table_privilege('kallicode_app', c.oid, 'TRUNCATE') OR has_table_privilege('kallicode_worker', c.oid, 'TRUNCATE')"
        " OR has_table_privilege('kallicode_app', c.oid, 'TRIGGER') OR has_table_privilege('kallicode_worker', c.oid, 'TRIGGER')"
        " OR has_table_privilege('kallicode_app', c.oid, 'REFERENCES') OR has_table_privilege('kallicode_worker', c.oid, 'REFERENCES'))"
    ).fetchall()
    assert malas == []


def test_las_tablas_probatorias_no_se_modifican_ni_se_borran(admin: psycopg.Connection) -> None:
    prob = [t for t in _informe(admin) if t["probatoria"]]
    assert {t["tabla"] for t in prob} == {
        "audit.audit_events",
        "core.gate_signatures",
        "core.llm_steps",
        "core.job_transitions",
        "core.qa_runs",
        "core.qa_results",
        "core.usage_loc",
        "core.security_findings",
    }
    for t in prob:
        for campo in ("priv_app", "priv_worker"):
            assert not re.search(r"UPDATE|DELETE|TRUNCATE", str(t[campo])), (
                f"{t['tabla']}: {campo} = {t[campo]}"
            )


def test_las_particiones_hijas_no_son_accesibles_ni_para_la_aplicacion_ni_para_el_worker(
    admin: psycopg.Connection,
) -> None:
    """La fuga real que encontró esta auditoría: una partición no hereda las políticas RLS del padre."""
    accesibles = admin.execute(
        "SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relispartition AND c.relkind IN ('r', 'p') AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND ("
        " has_table_privilege('kallicode_app', c.oid, 'SELECT,INSERT,UPDATE,DELETE') OR has_table_privilege('kallicode_worker', c.oid, 'SELECT,INSERT,UPDATE,DELETE'))"
    ).fetchall()
    assert accesibles == []
    assert admin.execute(
        "SELECT has_table_privilege('kallicode_app', 'vec.definition_embeddings_default', 'SELECT')"
    ).fetchone() == (False,)


def test_los_catalogos_globales_son_de_solo_lectura_para_la_aplicacion(
    admin: psycopg.Connection,
) -> None:
    for t in _informe(admin):
        if not t["rls"]:
            assert not re.search(
                r"INSERT|UPDATE|DELETE|TRUNCATE", f"{t['priv_app']} {t['priv_worker']}"
            ), f"{t['tabla']} sin RLS con escritura: {t['priv_app']} / {t['priv_worker']}"


# ---------------------------------------------------------------------------------------------------------
# El informe detecta los incumplimientos (provocados y revertidos)
# ---------------------------------------------------------------------------------------------------------
def test_una_tabla_nueva_con_tenant_id_y_sin_rls_incumple(admin: psycopg.Connection) -> None:
    _como_admin(admin)
    admin.execute("CREATE TABLE core.zz_sin_rls (tenant_id text, dato text)")
    estado = {t["tabla"]: t["estado"] for t in _informe(admin)}["core.zz_sin_rls"]
    assert estado.startswith("INCUMPLE") and "tenant_id y no tiene RLS" in estado


def test_una_tabla_nueva_sin_rls_y_sin_excepcion_incumple_y_con_excepcion_se_acepta(
    admin: psycopg.Connection,
) -> None:
    _como_admin(admin)
    admin.execute("CREATE TABLE core.zz_catalogo (codigo text PRIMARY KEY)")
    admin.execute("REVOKE ALL ON core.zz_catalogo FROM kallicode_app, kallicode_worker")
    assert (
        "sin RLS y sin excepción justificada"
        in {t["tabla"]: t["estado"] for t in _informe(admin)}["core.zz_catalogo"]
    )
    admin.execute(
        "INSERT INTO core.excepciones_aislamiento (entidad, tipo, motivo) VALUES ('core.zz_catalogo', 'tabla_sin_rls', 'Catálogo de prueba sin datos de tenants, solo kallicode_admin')"
    )
    assert {t["tabla"]: t["estado"] for t in _informe(admin)}[
        "core.zz_catalogo"
    ] == "excepcion_justificada"


def test_una_excepcion_no_ampara_la_escritura_de_la_aplicacion(admin: psycopg.Connection) -> None:
    _como_admin(admin)
    """El DEFAULT PRIVILEGES da escritura a la aplicación en toda tabla nueva de core y vec."""
    admin.execute(
        "CREATE TABLE core.zz_catalogo2 (codigo text PRIMARY KEY)"
    )  # recibe arwd para kallicode_app por defecto
    admin.execute(
        "INSERT INTO core.excepciones_aislamiento (entidad, tipo, motivo) VALUES ('core.zz_catalogo2', 'tabla_sin_rls', 'Catálogo de prueba sin datos de tenants, lectura')"
    )
    estado = {t["tabla"]: t["estado"] for t in _informe(admin)}["core.zz_catalogo2"]
    assert "escritura sobre una tabla sin RLS sin justificar" in estado


def test_una_politica_sin_clausula_to_incumple(admin: psycopg.Connection) -> None:
    _como_admin(admin)
    admin.execute("CREATE TABLE core.zz_pol (tenant_id text)")
    admin.execute("ALTER TABLE core.zz_pol ENABLE ROW LEVEL SECURITY")
    admin.execute("ALTER TABLE core.zz_pol FORCE ROW LEVEL SECURITY")
    admin.execute(
        "CREATE POLICY tenant_isolation ON core.zz_pol USING (tenant_id = core.current_tenant())"
    )  # sin TO
    estado = {t["tabla"]: t["estado"] for t in _informe(admin)}["core.zz_pol"]
    assert "sin cláusula TO" in estado
    assert admin.execute(
        "SELECT estado FROM core.informe_politicas_rls() WHERE tabla = 'core.zz_pol'"
    ).fetchone() == ("INCUMPLE: sin cláusula TO",)


def test_una_politica_que_no_filtra_por_tenant_y_no_esta_justificada_incumple(
    admin: psycopg.Connection,
) -> None:
    _como_admin(admin)
    admin.execute("CREATE TABLE core.zz_abierta (tenant_id text)")
    admin.execute("ALTER TABLE core.zz_abierta ENABLE ROW LEVEL SECURITY")
    admin.execute("ALTER TABLE core.zz_abierta FORCE ROW LEVEL SECURITY")
    admin.execute("CREATE POLICY todo_abierto ON core.zz_abierta TO kallicode_app USING (true)")
    assert admin.execute(
        "SELECT estado FROM core.informe_politicas_rls() WHERE tabla = 'core.zz_abierta'"
    ).fetchone() == ("INCUMPLE: no filtra por tenant y no está justificada",)


def test_rls_sin_force_incumple(admin: psycopg.Connection) -> None:
    _como_admin(admin)
    admin.execute("CREATE TABLE core.zz_sin_force (tenant_id text)")
    admin.execute("ALTER TABLE core.zz_sin_force ENABLE ROW LEVEL SECURITY")
    admin.execute(
        "CREATE POLICY tenant_isolation ON core.zz_sin_force TO kallicode_app USING (tenant_id = core.current_tenant())"
    )
    assert (
        "RLS sin FORCE" in {t["tabla"]: t["estado"] for t in _informe(admin)}["core.zz_sin_force"]
    )


def test_una_particion_accesible_incumple(admin: psycopg.Connection) -> None:
    admin.execute(
        "GRANT SELECT ON vec.definition_embeddings_default TO kallicode_app"
    )  # lo que hacía la migración 037
    estado = {t["tabla"]: t["estado"] for t in _informe(admin)}["vec.definition_embeddings"]
    assert estado.startswith("INCUMPLE") and "partición hija" in estado


def test_escritura_destructiva_sobre_una_tabla_probatoria_incumple(
    admin: psycopg.Connection,
) -> None:
    admin.execute("GRANT UPDATE ON core.gate_signatures TO kallicode_app")
    estado = {t["tabla"]: t["estado"] for t in _informe(admin)}["core.gate_signatures"]
    assert "tabla probatoria con escritura destructiva" in estado


def test_las_excepciones_exigen_una_explicacion_de_verdad(admin: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
        admin.execute(
            "INSERT INTO core.excepciones_aislamiento (entidad, tipo, motivo) VALUES ('core.zz', 'tabla_sin_rls', 'porque sí')"
        )
    with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
        admin.execute(
            "INSERT INTO core.excepciones_aislamiento (entidad, tipo, motivo) VALUES ('core.zz', 'otra_cosa', 'Una explicación suficientemente larga de verdad')"
        )


def test_solo_el_administrador_ve_las_excepciones_y_el_informe(admin: psycopg.Connection) -> None:
    for rol in ROLES:
        admin.execute(f"SET LOCAL ROLE {rol}")  # type: ignore[call-overload]
        with pytest.raises(psycopg.errors.InsufficientPrivilege), admin.transaction():
            admin.execute("SELECT * FROM core.excepciones_aislamiento")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), admin.transaction():
            admin.execute("SELECT * FROM core.informe_aislamiento()")
        admin.execute("RESET ROLE")


# ---------------------------------------------------------------------------------------------------------
# Cobertura del 100 % de las tablas con política (data-independiente: el plan de la consulta)
# ---------------------------------------------------------------------------------------------------------
def _plan(dsn: str, tabla: str, rol: str) -> str | None:
    """El plan de `SELECT * FROM tabla` con el rol y un tenant de sesión; None si el rol no tiene privilegios."""
    with psycopg.connect(dsn, autocommit=False) as c:
        c.execute("SELECT set_config('app.tenant_id', 'org_cobertura', true)")
        try:
            filas = c.execute(f"EXPLAIN (COSTS OFF) SELECT * FROM {tabla}").fetchall()  # type: ignore[call-overload]
        except psycopg.errors.InsufficientPrivilege:
            return None
        return "\n".join(f[0] for f in filas)


def test_la_politica_se_aplica_en_la_consulta_de_el_100_por_100_de_las_tablas_con_rls(
    admin: psycopg.Connection,
) -> None:
    """Cobertura del 100 %: para cada tabla con RLS, el plan de una consulta de la aplicación lleva el filtro de tenant.

    No depende de que haya datos (una tabla vacía pasaría «por vacía» en una prueba de comportamiento): comprueba que la
    política está efectivamente en la consulta. Donde la aplicación no tiene privilegios, la barrera es el privilegio.
    """
    app = _dsn("KC_DATABASE_URL")
    con_rls = [t["tabla"] for t in _informe(admin) if t["rls"]]
    abiertas_justificadas = {
        f[0]
        for f in admin.execute(
            "SELECT tabla FROM core.informe_politicas_rls() WHERE NOT filtra_por_tenant AND roles LIKE '%kallicode_app%' AND estado = 'excepcion_justificada'"
        ).fetchall()
    }  # la lista de precios: el mismo dato para todos, solo lectura
    con_filtro, sin_privilegios, sin_filtro = [], [], []
    for tabla in con_rls:
        plan = _plan(app, tabla, "kallicode_app")
        if plan is None:
            sin_privilegios.append(tabla)
        elif "current_tenant()" in plan or "app.tenant_id" in plan:
            con_filtro.append(tabla)
        else:
            sin_filtro.append(tabla)
    assert sorted(sin_filtro) == sorted(abiertas_justificadas), (
        f"consultas de la aplicación SIN filtro de tenant: {sin_filtro}"
    )
    assert len(con_filtro) + len(sin_privilegios) + len(sin_filtro) == len(
        con_rls
    )  # 100 % cubierto: filtro, sin acceso o excepción justificada
    assert len(con_filtro) >= 55, f"muy pocas tablas con filtro verificado: {len(con_filtro)}"


def test_el_worker_solo_consulta_sin_filtro_de_tenant_en_las_cinco_colas(
    admin: psycopg.Connection,
) -> None:
    worker = _dsn("KC_DATABASE_URL_WORKER")
    sin_filtro = []
    for t in _informe(admin):
        if not t["rls"]:
            continue
        plan = _plan(worker, t["tabla"], "kallicode_worker")
        if plan is not None and "current_tenant()" not in plan and "app.tenant_id" not in plan:
            sin_filtro.append(t["tabla"])
    assert sorted(sin_filtro) == [
        "core.idempotency_keys",
        "core.jobs",
        "core.plan_prices",
        "core.refresh_tokens",
        "core.ticket_attachments",
        "core.webhook_inbox",
    ]


# ---------------------------------------------------------------------------------------------------------
# El documento versionado y el pipeline
# ---------------------------------------------------------------------------------------------------------
def _cargar_herramienta() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "auditoria_rls", RAIZ / "db" / "tools" / "auditoria_rls.py"
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_el_documento_versionado_coincide_con_el_catalogo(admin: psycopg.Connection) -> None:
    herramienta = _cargar_herramienta()
    generado = herramienta.generar(admin)
    versionado = (
        (RAIZ / "docs" / "auditoria_rls.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    )
    assert versionado == generado, "Regenera el informe con: python db/tools/auditoria_rls.py"


def test_el_informe_declara_lo_que_encontro_y_sus_limites() -> None:
    texto = (RAIZ / "docs" / "auditoria_rls.md").read_text(encoding="utf-8")
    for frase in (
        "Fuga entre tenants por una partición hija",
        "core.retention_purgas",
        "sin cláusula `TO`",
        "pg_cron",
        "SQL arbitrario",
        "no una revisión externa",
    ):
        assert frase in texto, frase


def test_la_suite_de_aislamiento_es_obligatoria_en_el_pipeline_de_ci() -> None:
    """El criterio pide la suite «en el pipeline de release»: sus archivos tienen que estar en el job de base de datos."""
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for archivo in (
        "test_aislamiento.py",
        "test_roles.py",
        "test_auditoria_rls.py",
        "test_penetracion_tenants.py",
    ):
        assert f"packages/core/tests/integracion/{archivo}" in ci, (
            f"{archivo} no está en el job de base de datos de ci.yml"
        )
