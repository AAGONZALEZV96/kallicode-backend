"""Recuperación del esquema cm y reconstrucción concurrente de los índices HNSW (KC-DB-11-4).

Criterios del ticket:
  1. Tiempo de reconstrucción medido (db/tools/ensayo_reconstruccion_cm.py) y HNSW reconstruido con carga concurrente
     sin errores de consulta (db/tools/ensayo_hnsw_reconstruccion.py).
  2. Política escrita de qué se restaura y qué se reconstruye (core.politica_recuperacion); prueba que borra cm de un
     tenant, lo reconstruye y cuadra los recuentos por kind.

Las pruebas de la política y del vaciado corren en una transacción que siempre se revierte. Las dos herramientas de
ensayo usan sus propias conexiones y limpian lo que crean (en tamaño pequeño aquí; los tamaños de verdad, en el CI).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]


def _cargar(nombre: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / "db" / "tools" / f"{nombre}.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


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
    if c.execute("SELECT to_regproc('cm.vaciar_grafo_tenant')").fetchone() == (None,):
        c.close()
        pytest.skip("migración 064 sin aplicar")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _grafo(c: psycopg.Connection, tenant: str) -> None:
    """Un tenant con capa determinista, capa semántica e historia operativa."""
    c.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    snap = f"snap_{tenant}"
    c.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado) VALUES (%s, %s, 'abc', 'main', 'completo')",
        (snap, tenant),
    )
    for i in (1, 2):
        c.execute(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) VALUES (%s, %s, %s, 'python', 'h', %s)",
            (f"f{i}_{tenant}", tenant, f"a{i}.py", snap),
        )
        c.execute(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) "
            "VALUES (%s, %s, %s, 'function', %s, 1, 2, 'sig')",
            (f"s{i}_{tenant}", tenant, f"f{i}_{tenant}", f"fn{i}"),
        )
    c.execute(
        "INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre) VALUES (%s, %s, 'public', 't')",
        (f"t_{tenant}", tenant),
    )
    c.execute(
        "INSERT INTO cm.db_columns (id, tenant_id, table_id, nombre, tipo, nullable) VALUES (%s, %s, %s, 'c', 'text', true)",
        (f"c_{tenant}", tenant, f"t_{tenant}"),
    )
    c.execute(
        "INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion, snapshot_id) VALUES (%s, %s, 'm', 'n', 'function', 'd', %s)",
        (f"d_{tenant}", tenant, snap),
    )
    c.execute(
        "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion, snapshot_id) VALUES (%s, %s, 'x', 'x', 'd', %s)",
        (f"k_{tenant}", tenant, snap),
    )
    aristas = [
        ("CALLS", "symbol", f"s1_{tenant}", "symbol", f"s2_{tenant}"),
        ("USES_TABLE", "file", f"f1_{tenant}", "db_table", f"t_{tenant}"),
        ("HAS_COLUMN", "db_table", f"t_{tenant}", "db_column", f"c_{tenant}"),
        ("REALIZED_BY", "concept", f"k_{tenant}", "symbol", f"s1_{tenant}"),
        ("EXPRESSES", "definition", f"d_{tenant}", "concept", f"k_{tenant}"),
    ]
    for tipo, sk, si, dk, di in aristas:
        c.execute(
            "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, snapshot_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (tenant, tipo, sk, si, dk, di, snap),
        )
    c.execute(
        "INSERT INTO cm.incidents (id, tenant_id, resumen, origen) VALUES (%s, %s, 'incidente', 'manual')",
        (f"i_{tenant}", tenant),
    )
    c.execute(
        "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash) "
        "VALUES (%s, %s, %s, 'symbol', 'n', 'd', array_fill(0.1::real, ARRAY[1024])::vector, 'h')",
        (f"e_{tenant}", tenant, f"s1_{tenant}"),
    )
    c.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
    c.execute("SELECT * FROM cm.refrescar_reachability()")


def _n(c: psycopg.Connection, sql: str, *params: object) -> int:
    fila = c.execute(sql, params).fetchone()  # type: ignore[call-overload]
    assert fila is not None
    return int(fila[0])


def _suf() -> str:
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------------------------------------
# Política escrita
# ---------------------------------------------------------------------------------------------------------
def test_toda_tabla_de_cm_y_vec_esta_clasificada(conn: psycopg.Connection) -> None:
    sin = [
        r[0]
        for r in conn.execute("SELECT * FROM core.tablas_sin_politica_recuperacion()").fetchall()
    ]
    assert not sin, f"tablas de cm/vec sin clasificar en core.politica_recuperacion: {sin}"


def test_la_politica_no_nombra_objetos_que_ya_no_existen(conn: psycopg.Connection) -> None:
    fantasmas = [
        r[0]
        for r in conn.execute(
            "SELECT p.entidad FROM core.politica_recuperacion p WHERE "
            "(p.tipo = 'tabla' AND to_regclass(p.entidad) IS NULL) "
            "OR (p.tipo = 'indice' AND to_regclass(p.entidad) IS NULL)"
        ).fetchall()
    ]
    assert not fantasmas, f"entradas de la política sin objeto en la base: {fantasmas}"


def test_la_politica_distingue_lo_derivado_de_lo_que_costo_tokens_o_decisiones(
    conn: psycopg.Connection,
) -> None:
    pol: dict[str, str] = dict(
        conn.execute("SELECT entidad, estrategia FROM core.politica_recuperacion").fetchall()
    )
    for derivada in (
        "cm.files",
        "cm.symbols",
        "cm.db_tables",
        "cm.db_columns",
        "cm.nodes",
        "cm.reachability",
    ):
        assert pol[derivada] == "reconstruir", derivada
    for historia in (
        "cm.definitions", "cm.concepts", "cm.nuggets", "cm.curation_queue", "cm.incidents",
        "cm.repo_snapshots", "cm.test_flows", "cm.realimentacion_tickets", "vec.definition_embeddings",
    ):  # fmt: skip
        assert pol[historia] == "restaurar", historia
    assert pol["cm.edges"] == "mixta"  # deterministas se reconstruyen; semánticas, del backup
    indices = [
        e
        for (e,) in conn.execute(
            "SELECT entidad FROM core.politica_recuperacion WHERE tipo = 'indice'"
        ).fetchall()
    ]
    assert sorted(indices) == [
        "vec.definition_embeddings_hnsw_ix", "vec.doc_chunks_hnsw_ix", "vec.ticket_embeddings_hnsw_ix",
    ]  # fmt: skip
    assert all(pol[i] == "reconstruir" for i in indices)  # un índice nunca se restaura por separado


def test_una_tabla_nueva_sin_clasificar_se_detecta(conn: psycopg.Connection) -> None:
    conn.execute("CREATE TABLE cm.zz_tabla_nueva_prueba (tenant_id text)")
    sin = [
        r[0]
        for r in conn.execute("SELECT * FROM core.tablas_sin_politica_recuperacion()").fetchall()
    ]
    assert sin == ["cm.zz_tabla_nueva_prueba"]


def test_la_politica_exige_estrategia_valida_y_textos_no_vacios(conn: psycopg.Connection) -> None:
    for estrategia, fuente in (("copiar", "x"), ("restaurar", "  ")):
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO core.politica_recuperacion (entidad, tipo, estrategia, fuente, coste, detalle) "
                "VALUES ('cm.zz', 'tabla', %s, %s, 'c', 'd')",
                (estrategia, fuente),
            )


# ---------------------------------------------------------------------------------------------------------
# Vaciar el grafo de un tenant
# ---------------------------------------------------------------------------------------------------------
def test_vaciar_exige_confirmar_con_el_mismo_tenant_y_un_alcance_valido(
    conn: psycopg.Connection,
) -> None:
    t = f"org_vac_{_suf()}"
    _grafo(conn, t)
    antes = _n(conn, "SELECT count(*) FROM cm.nodes WHERE tenant_id = %s", t)
    for args in (
        (t, "determinista", None),
        (t, "determinista", "otro"),
        (t, "borrar_todo", t),
        ("org_no_existe", "determinista", "org_no_existe"),
    ):
        with pytest.raises(errors.InvalidParameterValue), conn.transaction():
            conn.execute("SELECT * FROM cm.vaciar_grafo_tenant(%s, %s, %s)", args)
    assert (
        _n(conn, "SELECT count(*) FROM cm.nodes WHERE tenant_id = %s", t) == antes
    )  # nada se borró


def test_vaciar_determinista_borra_lo_derivado_y_conserva_lo_semantico_y_la_historia(
    conn: psycopg.Connection,
) -> None:
    t = f"org_vac_{_suf()}"
    _grafo(conn, t)
    antes: dict[str, int] = dict(
        conn.execute("SELECT objeto, filas FROM cm.recuentos_grafo(%s)", (t,)).fetchall()
    )
    assert antes["reachability"] > 0 and antes["nodo:symbol"] == 2 and antes["nodo:file"] == 2

    conn.execute("SELECT * FROM cm.vaciar_grafo_tenant(%s, 'determinista', %s)", (t, t))
    despues: dict[str, int] = dict(
        conn.execute("SELECT objeto, filas FROM cm.recuentos_grafo(%s)", (t,)).fetchall()
    )
    # lo determinista desaparece, con sus nodos (los triggers retiran cm.nodes) y la alcanzabilidad
    assert not any(
        despues.get(k)
        for k in ("nodo:file", "nodo:symbol", "nodo:db_table", "nodo:db_column", "reachability")
    )
    assert not any(
        despues.get(k) for k in ("arista:CALLS", "arista:USES_TABLE", "arista:HAS_COLUMN")
    )
    # lo semántico y la historia se quedan
    assert despues["nodo:definition"] == 1 and despues["nodo:concept"] == 1
    assert despues["arista:REALIZED_BY"] == 1 and despues["arista:EXPRESSES"] == 1
    assert _n(conn, "SELECT count(*) FROM cm.incidents WHERE tenant_id = %s", t) == 1
    assert _n(conn, "SELECT count(*) FROM cm.repo_snapshots WHERE tenant_id = %s", t) == 1


def test_vaciar_completo_ademas_borra_la_capa_semantica_pero_no_la_historia(
    conn: psycopg.Connection,
) -> None:
    t = f"org_vac_{_suf()}"
    _grafo(conn, t)
    conn.execute("SELECT * FROM cm.vaciar_grafo_tenant(%s, 'completo', %s)", (t, t))
    assert _n(conn, "SELECT count(*) FROM cm.nodes WHERE tenant_id = %s", t) == 0
    assert _n(conn, "SELECT count(*) FROM cm.edges WHERE tenant_id = %s", t) == 0
    assert _n(conn, "SELECT count(*) FROM cm.definitions WHERE tenant_id = %s", t) == 0
    assert _n(conn, "SELECT count(*) FROM cm.concepts WHERE tenant_id = %s", t) == 0
    assert (
        _n(conn, "SELECT count(*) FROM cm.incidents WHERE tenant_id = %s", t) == 1
    )  # historia: nunca se vacía
    assert _n(conn, "SELECT count(*) FROM cm.repo_snapshots WHERE tenant_id = %s", t) == 1


def test_vaciar_un_tenant_no_toca_a_otro(conn: psycopg.Connection) -> None:
    a, b = f"org_vac_a_{_suf()}", f"org_vac_b_{_suf()}"
    _grafo(conn, a)
    _grafo(conn, b)
    antes_b: dict[str, int] = dict(
        conn.execute("SELECT objeto, filas FROM cm.recuentos_grafo(%s)", (b,)).fetchall()
    )
    conn.execute("SELECT * FROM cm.vaciar_grafo_tenant(%s, 'completo', %s)", (a, a))
    assert (
        dict(conn.execute("SELECT objeto, filas FROM cm.recuentos_grafo(%s)", (b,)).fetchall())
        == antes_b
    )


def test_vaciar_los_simbolos_borra_en_cascada_sus_embeddings(conn: psycopg.Connection) -> None:
    """El aviso de la política: reconstruir la capa determinista obliga a restaurar o recalcular los embeddings."""
    t = f"org_vac_{_suf()}"
    _grafo(conn, t)
    assert _n(conn, "SELECT count(*) FROM vec.definition_embeddings WHERE tenant_id = %s", t) == 1
    conn.execute("SELECT * FROM cm.vaciar_grafo_tenant(%s, 'determinista', %s)", (t, t))
    assert _n(conn, "SELECT count(*) FROM vec.definition_embeddings WHERE tenant_id = %s", t) == 0


def test_la_aplicacion_no_puede_vaciar_ni_contar(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE kallicode_app")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT * FROM cm.vaciar_grafo_tenant('x', 'determinista', 'x')")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT * FROM cm.recuentos_grafo('x')")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT count(*) FROM core.politica_recuperacion")


# ---------------------------------------------------------------------------------------------------------
# Reconstrucción de punta a punta (criterio 2) con el cargador real
# ---------------------------------------------------------------------------------------------------------
def test_borrar_reconstruir_y_cuadrar_los_recuentos_por_kind() -> None:
    ensayo = _cargar("ensayo_reconstruccion_cm")
    informe = ensayo.ensayar(_dsn(), ensayo.grafo_sintetico(1_500, 2_200))
    assert informe["cuadra"], informe["diferencias"]
    assert informe["vaciado_completo"]
    antes = informe["antes"]
    assert antes["nodo:file"] > 0 and antes["nodo:symbol"] > 0 and antes["nodo:db_table"] > 0
    assert antes["arista:CALLS"] > 0 and antes["reachability"] > 0
    assert (
        sum(v for k, v in antes.items() if k.startswith("nodo:")) == 1_500
    )  # el generador respeta el tamaño pedido
    assert informe["reconstruccion"]["total_s"] > 0
    # el ensayo no deja nada del tenant de prueba
    with psycopg.connect(_dsn()) as c:
        assert _n(c, "SELECT count(*) FROM core.organizations WHERE id LIKE 'org_recon_%%'") == 0


def test_el_perfil_cliente_medio_del_ticket_se_reconstruye_y_cuadra() -> None:
    """El «dataset real» del ticket (17.323 nodos y 23.309 aristas) es el perfil calibrado de KC-DB-11-3."""
    ensayo = _cargar("ensayo_reconstruccion_cm")
    grafo = ensayo.grafo_perfil("cliente_medio")
    assert grafo.perfil.n_nodos == 17_323 and len(grafo.perfil.aristas) == 23_309
    informe = ensayo.ensayar(_dsn(), grafo)
    assert informe["cuadra"], informe["diferencias"]
    antes = informe["antes"]
    assert sum(v for k, v in antes.items() if k.startswith("nodo:")) == 17_323
    assert sum(v for k, v in antes.items() if k.startswith("arista:")) == 23_309
    assert (
        informe["reconstruccion"]["fases"]["ANALYZE por fase"] >= 0
    )  # cm.analizar_tablas, de KC-CM-11-1
    with psycopg.connect(_dsn()) as c:
        assert _n(c, "SELECT count(*) FROM core.organizations WHERE id LIKE 'org_recon_%%'") == 0


def test_el_informe_de_reconstruccion_declara_lo_que_no_mide() -> None:
    ensayo = _cargar("ensayo_reconstruccion_cm")
    informe = ensayo.ensayar(_dsn(), ensayo.grafo_sintetico(600, 800))
    md = ensayo.markdown([informe])
    assert "CUADRA" in md and "NO CUADRA" not in md
    assert "No** se incluye la fase de LLM" in md and "sintéticos calibrados" in md


# ---------------------------------------------------------------------------------------------------------
# HNSW: reconstrucción concurrente de los tres índices bajo carga (criterio 1)
# ---------------------------------------------------------------------------------------------------------
def test_los_tres_indices_hnsw_se_reconstruyen_con_carga_concurrente_sin_errores() -> None:
    hnsw = _cargar("ensayo_hnsw_reconstruccion")
    informe = hnsw.ensayar(_dsn(), filas=400, mwm="64MB")
    assert informe["errores_de_la_carga"] == 0, informe["primeros_errores"]
    assert set(informe["indices"]) == {
        "ticket_embeddings_hnsw_ix",
        "doc_chunks_hnsw_ix",
        "definition_embeddings_hnsw_ix",
    }
    for nombre, v in informe["indices"].items():
        assert v["valido"] and v["restos_de_reconstruccion"] == 0, nombre
    assert all(informe["las_busquedas_usan_el_indice"].values())
    assert set(informe["recall_antes"]) == set(
        informe["recall_despues"]
    )  # el recall se mide (informativo con vectores aleatorios)
    assert all(
        v["tamano_despues_mb"] > 0 for v in informe["indices"].values()
    )  # también los particionados
    assert informe["ok"]
    assert (
        informe["escrituras_totales"] > 0 and informe["lecturas_totales"] > 0
    )  # hubo carga de verdad
    with psycopg.connect(_dsn()) as c:  # y no quedan filas del ensayo
        for tabla in ("vec.ticket_embeddings", "vec.doc_chunks", "vec.definition_embeddings"):
            assert _n(c, f"SELECT count(*) FROM {tabla} WHERE tenant_id = 'org_hnsw_ens'") == 0  # type: ignore[call-overload]
