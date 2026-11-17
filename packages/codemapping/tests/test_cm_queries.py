"""Resolución de {{nuggets}} contra PostgreSQL 16 + pgvector reales (KC-CM-08-3).

Requiere `make up && make db` (migraciones 040 y 041). Las consultas corren como kallicode_app con
el tenant fijado por GraphLoader.fase(), igual que el pipeline; el administrador solo siembra datos.

Criterios:
- Solo nuggets 'aprobado' del tenant activo: propuestos y rechazados nunca salen, y dos
  organizaciones con los MISMOS ids de nodo, chunk y nugget no se ven entre sí.
- Orden determinista y recorte por reglas enteras; las respuestas validan contra el contrato
  congelado contratos/cm-consulta-v1.schema.json.
- p95 < 200 ms sobre el grafo de referencia (tests/fixtures/repo_ref).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import psycopg
import pytest
from kallicode_codemapping.cm_queries import (
    SQL_NUGGETS_APLICABLES,
    contexto_plantilla,
    resolver_nuggets,
)
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.ingest_repo import LANG_BY_EXT, SourceFile
from kallicode_codemapping.parser_treesitter import parse_file
from kallicode_core import cm_consulta, cm_grafo
from kallicode_core.cm_consulta import ConsultaNuggetsIn, ConsultaNuggetsOut

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT_A = "org_test_cmq_a"
TENANT_B = "org_test_cmq_b"
TENANT_VACIO = "org_test_cmq_vacio"
TENANT_BENCH = "org_test_cmq_bench"
TENANT_BENCH_OTRO = "org_test_cmq_bench_b"
TODOS = (TENANT_A, TENANT_B, TENANT_VACIO, TENANT_BENCH, TENANT_BENCH_OTRO)
VECTOR = "[" + ",".join(["0.001"] * 1024) + "]"
REPO_REF = Path(__file__).resolve().parent / "fixtures" / "repo_ref"
SALIDA = json.loads(cm_consulta.RUTA_CONTRATO.read_text(encoding="utf-8"))["salida"]

INVOICE = "src/billing/invoice.py"
TAX = "src/billing/tax.py"
LOGIN = "src/auth/login.py"


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                """SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'cm' AND p.proname = 'resolver_nugget'"""
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 041 sin aplicar (make up && make db)"
)


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        for tabla in (
            "cm.nuggets",
            "cm.curation_queue",
            "cm.doc_chunk_links",
            "vec.doc_chunks",
            "core.onboarding_documents",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.db_columns",
            "cm.db_tables",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
            "core.users",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _doc(tenant: str) -> str:
    """core.onboarding_documents.id es único en toda la base, no por tenant."""
    return f"doc_cmq_{tenant}"


def _base_tenant(conn: psycopg.Connection[Any], tenant: str) -> str:
    """Organización, curadora, snapshot y un documento procesado. Devuelve el id de la curadora."""
    usuario = f"usr_{tenant}"
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    conn.execute(
        "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'Curadora')",
        (usuario, tenant, f"{usuario}@prueba.dev"),
    )
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
        "VALUES ('snap_cmq', %s, 'sha', 'main')",
        (tenant,),
    )
    conn.execute(
        """INSERT INTO core.onboarding_documents
               (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref, subido_por,
                estado, chunks_count, procesado_en)
           VALUES (%s, %s, 'reglas.md', 'text/markdown', 10, %s, %s, 'procesado', 1, now())""",
        (_doc(tenant), tenant, f"{tenant}/docs/{_doc(tenant)}/reglas.md", usuario),
    )
    return usuario


def _chunk(conn: psycopg.Connection[Any], tenant: str, chunk_id: str, idx: int) -> None:
    conn.execute(
        """INSERT INTO vec.doc_chunks
               (id, tenant_id, document_id, chunk_index, content, embedding, seccion, texto_hash)
           VALUES (%s, %s, %s, %s, %s, %s::vector, %s, 'h')""",
        (
            chunk_id,
            tenant,
            _doc(tenant),
            idx,
            f"Contenido del chunk {chunk_id}.",
            VECTOR,
            f"Sección {idx}",
        ),
    )


def _nugget(
    conn: psycopg.Connection[Any],
    tenant: str,
    nugget_id: str,
    chunk_id: str,
    tipo: str,
    scope: list[str] | None = None,
    regla: str | None = None,
) -> None:
    conn.execute(
        """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, scope_refs, evidencia_ref, seccion)
           VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'Sección')""",
        (
            nugget_id,
            tenant,
            regla or f"[{tenant}] regla {nugget_id}",
            tipo,
            json.dumps(scope or []),
            chunk_id,
        ),
    )


def _resolver(conn: psycopg.Connection[Any], tenant: str, usuario: str, ids: list[str],
              decision: str = "aprobado") -> None:  # fmt: skip
    """Resolución humana registrada (camino soportado), en el orden dado."""
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
    for nugget_id in ids:
        conn.execute(
            "SELECT id FROM cm.resolver_nugget(%s, %s, %s)", (nugget_id, usuario, decision)
        )
    conn.execute("SELECT set_config('app.tenant_id', '', false)")


def _sembrar_grafo_pequeno(conn: psycopg.Connection[Any], tenant: str) -> None:
    """Mismo grafo y mismos ids en cada tenant; solo cambia el texto de las reglas."""
    usuario = _base_tenant(conn, tenant)
    for file_id, path in (("file_inv", INVOICE), ("file_tax", TAX), ("file_login", LOGIN)):
        conn.execute(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES (%s, %s, %s, 'python', 'x', 'snap_cmq')",
            (file_id, tenant, path),
        )
    for sym_id, file_id, nombre in (
        ("sym_invoice", "file_inv", "Invoice"),
        ("sym_iva", "file_tax", "calcular_iva"),
        ("sym_login", "file_login", "login"),
    ):
        conn.execute(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line,"
            " signature) VALUES (%s, %s, %s, 'function', %s, 1, 2, %s)",
            (sym_id, tenant, file_id, nombre, f"def {nombre}()"),
        )
    conn.execute(
        "INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre) "
        "VALUES ('dbt_facturas', %s, 'core', 'facturas')",
        (tenant,),
    )
    for i, chunk in enumerate(("c1", "c2", "c3", "c4", "c5", "c6")):
        _chunk(conn, tenant, chunk, i)
    for chunk, nodo, score in (
        ("c1", "sym_invoice", 0.9),
        ("c6", "sym_invoice", 0.9),
        ("c2", "file_inv", 0.6),
        ("c5", "file_login", 0.8),
    ):
        conn.execute(
            "INSERT INTO cm.doc_chunk_links (tenant_id, chunk_id, nodo_ref, metodo, score) "
            "VALUES (%s, %s, %s, 'lexico', %s)",
            (tenant, chunk, nodo, score),
        )
    _nugget(conn, tenant, "n_dir_sym", "c1", "regla_negocio")
    _nugget(conn, tenant, "n_dir_tie", "c6", "regla_negocio")
    _nugget(conn, tenant, "n_dir_file", "c2", "restriccion_tecnica")
    _nugget(conn, tenant, "n_scope_iva", "c3", "restriccion_tecnica", ["CALCULAR_IVA"])
    _nugget(conn, tenant, "n_scope_tabla", "c3", "regla_negocio", ["core.facturas"])
    _nugget(conn, tenant, "n_general", "c4", "regla_negocio")
    _nugget(conn, tenant, "n_auth", "c5", "restriccion_tecnica")
    _nugget(conn, tenant, "n_pend", "c1", "regla_negocio")  # queda propuesto
    _nugget(conn, tenant, "n_rech", "c1", "regla_negocio")
    _resolver(
        conn,
        tenant,
        usuario,
        ["n_dir_sym", "n_dir_tie", "n_dir_file", "n_scope_iva", "n_scope_tabla", "n_general",
         "n_auth"],
    )  # fmt: skip
    _resolver(conn, tenant, usuario, ["n_rech"], "rechazado")


@pytest.fixture(scope="module")
def grafo() -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        _sembrar_grafo_pequeno(conn, TENANT_A)
        _sembrar_grafo_pequeno(conn, TENANT_B)
        _base_tenant(conn, TENANT_VACIO)
        yield
        _limpiar(conn)


def _consultar(tenant: str, **kw: Any) -> ConsultaNuggetsOut:
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        r = resolver_nuggets(loader, ConsultaNuggetsIn(**kw))
    jsonschema.validate(r.model_dump(mode="json"), SALIDA)  # prueba de contrato con datos reales
    return r


def _ids(r: ConsultaNuggetsOut) -> list[str]:
    return [n.id for n in r.nuggets]


# ---------------------------------------------------------------------------
# Aplicabilidad y orden por variable
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("consulta", "esperado"),
    [
        (
            {"variable": "nuggets", "ficheros": [INVOICE]},
            [
                ("n_dir_sym", "directa"),
                ("n_dir_tie", "directa"),  # mismo score: desempata quien se aprobó antes
                ("n_dir_file", "directa"),
                ("n_general", "general"),
            ],
        ),
        (
            {"variable": "nuggets", "ficheros": [TAX]},
            [("n_scope_iva", "alcance"), ("n_general", "general")],
        ),
        (
            {"variable": "nuggets", "nodos": ["dbt_facturas"]},
            [("n_scope_tabla", "alcance"), ("n_general", "general")],
        ),
        (
            {"variable": "nuggets_for_file", "ficheros": [INVOICE]},
            [("n_dir_sym", "directa"), ("n_dir_tie", "directa"), ("n_dir_file", "directa")],
        ),
        (
            {"variable": "module_conventions", "modulo": "src/billing"},
            [("n_dir_file", "directa"), ("n_scope_iva", "alcance")],
        ),
        ({"variable": "module_conventions", "modulo": "src/bill"}, []),  # prefijo de carpeta
        ({"variable": "nuggets_for_file", "ficheros": ["no/existe.py"]}, []),
        ({"variable": "nuggets"}, [("n_general", "general")]),
    ],
    ids=[
        "fichero-directos",
        "fichero-por-scope-ref",
        "tabla-por-scope-ref",
        "nuggets-for-file",
        "module-conventions",
        "modulo-no-es-prefijo-de-texto",
        "fichero-inexistente",
        "sin-area-solo-generales",
    ],
)
def test_variables_devuelven_los_aprobados_aplicables_en_orden(
    grafo: None, consulta: dict[str, Any], esperado: list[tuple[str, str]]
) -> None:
    r = _consultar(TENANT_A, **consulta)
    assert [(n.id, n.relevancia) for n in r.nuggets] == esperado
    assert all(n.regla.startswith(f"[{TENANT_A}]") for n in r.nuggets)
    assert r.texto == "\n".join(f"- {n.regla}" for n in r.nuggets)


def test_pendientes_y_rechazados_nunca_se_devuelven(grafo: None) -> None:
    """n_pend y n_rech citan el mismo chunk que n_dir_sym: aplicarían, pero no están aprobados."""
    vistos: set[str] = set()
    for consulta in (
        {"variable": "nuggets", "ficheros": [INVOICE, TAX, LOGIN]},
        {"variable": "nuggets", "nodos": ["sym_invoice", "file_inv", "dbt_facturas"]},
        {"variable": "nuggets_for_file", "ficheros": [INVOICE]},
        {"variable": "module_conventions", "modulo": "src"},
    ):
        vistos |= set(_ids(_consultar(TENANT_A, **consulta)))
    assert "n_dir_sym" in vistos
    assert not vistos & {"n_pend", "n_rech"}
    with psycopg.connect(ADMIN_DSN) as conn:
        estados: dict[str, str] = dict(
            conn.execute(
                "SELECT id, estado FROM cm.nuggets WHERE tenant_id = %s AND id IN "
                "('n_pend', 'n_rech')",
                (TENANT_A,),
            ).fetchall()
        )
    assert estados == {"n_pend": "propuesto", "n_rech": "rechazado"}


def test_no_fuga_entre_organizaciones_con_los_mismos_ids(grafo: None) -> None:
    consulta = {"variable": "nuggets", "ficheros": [INVOICE, TAX], "nodos": ["dbt_facturas"]}
    a = _consultar(TENANT_A, **consulta)
    b = _consultar(TENANT_B, **consulta)

    assert _ids(a) == _ids(b)  # mismo grafo, mismos ids...
    assert all(n.regla.startswith(f"[{TENANT_A}]") for n in a.nuggets)  # ...reglas propias
    assert all(n.regla.startswith(f"[{TENANT_B}]") for n in b.nuggets)
    assert TENANT_B not in a.texto and TENANT_A not in b.texto


def test_sin_tenant_en_la_sesion_no_devuelve_nada(grafo: None) -> None:
    with psycopg.connect(APP_DSN) as conn:
        filas = conn.execute(
            SQL_NUGGETS_APLICABLES,
            cm_consulta.parametros_sql(ConsultaNuggetsIn(variable="nuggets", ficheros=[INVOICE])),
        ).fetchall()
    assert filas == []


def test_tenant_sin_reglas_rellena_las_variables_vacias_sin_error(grafo: None) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_VACIO) as loader:
        valores = contexto_plantilla(
            loader, ficheros=[INVOICE], modulo="src/billing", fichero=INVOICE
        )
    assert valores == {"nuggets": "", "module_conventions": "", "nuggets_for_file": ""}


def test_contexto_plantilla_rellena_las_tres_variables(grafo: None) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_A) as loader:
        valores = contexto_plantilla(loader, ficheros=[TAX], modulo="src/billing", fichero=INVOICE)
        sin_modulo = contexto_plantilla(loader, ficheros=[TAX])
    assert valores["nuggets"].splitlines() == [
        f"- [{TENANT_A}] regla n_scope_iva",
        f"- [{TENANT_A}] regla n_general",
    ]
    assert valores["module_conventions"].splitlines() == [
        f"- [{TENANT_A}] regla n_dir_file",
        f"- [{TENANT_A}] regla n_scope_iva",
    ]
    assert len(valores["nuggets_for_file"].splitlines()) == 3
    assert sin_modulo["module_conventions"] == "" and sin_modulo["nuggets_for_file"] == ""


# ---------------------------------------------------------------------------
# Determinismo y recorte
# ---------------------------------------------------------------------------
def test_orden_determinista_en_consultas_repetidas(grafo: None) -> None:
    consulta = {"variable": "nuggets", "ficheros": [INVOICE, TAX], "nodos": ["dbt_facturas"]}
    primera = _consultar(TENANT_A, **consulta)
    for _ in range(10):
        assert _consultar(TENANT_A, **consulta) == primera
    claves = [({"directa": 1, "alcance": 2, "general": 3}[n.relevancia], -n.score)
              for n in primera.nuggets]  # fmt: skip
    assert claves == sorted(claves)


def test_recorte_entrega_reglas_enteras_del_principio(grafo: None) -> None:
    completo = _consultar(TENANT_A, variable="nuggets", ficheros=[INVOICE])
    primera = len(f"- {completo.nuggets[0].regla}")
    segunda = len(f"- {completo.nuggets[1].regla}")

    justo = _consultar(
        TENANT_A, variable="nuggets", ficheros=[INVOICE], presupuesto_caracteres=primera + segunda
    )  # le falta el salto de línea para la segunda
    assert _ids(justo) == _ids(completo)[:1]
    assert justo.omitidos == len(completo.nuggets) - 1
    assert justo.texto == f"- {completo.nuggets[0].regla}"

    nada = _consultar(TENANT_A, variable="nuggets", ficheros=[INVOICE], presupuesto_caracteres=0)
    assert nada.texto == "" and nada.nuggets == [] and nada.omitidos == len(completo.nuggets)


# ---------------------------------------------------------------------------
# Latencia sobre el grafo de referencia
# ---------------------------------------------------------------------------
def _repo_ref() -> list[SourceFile]:
    archivos: list[SourceFile] = []
    for ruta in sorted(REPO_REF.rglob("*")):
        if ruta.is_file() and ruta.suffix in LANG_BY_EXT:
            archivos.append(
                SourceFile(
                    path=ruta.relative_to(REPO_REF).as_posix(),
                    language=LANG_BY_EXT[ruta.suffix],
                    sha256="fixture",
                    content=ruta.read_text(encoding="utf-8", errors="replace"),
                )
            )
    return archivos


N_CHUNKS = 300
N_APROBADOS = 1500
N_PROPUESTOS = 500


def _sembrar_benchmark(conn: psycopg.Connection[Any], tenant: str) -> list[str]:
    """Grafo de referencia + 300 chunks enlazados + 2000 nuggets (1500 aprobados)."""
    usuario = _base_tenant(conn, tenant)
    archivos = _repo_ref()
    with GraphLoader(dsn=ADMIN_DSN, tenant_id=tenant) as loader:
        snap = loader.start_snapshot(commit_sha="sha_bench", rama="main")
        file_ids = loader.load_files(snap, archivos)
        loader.load_symbols([s for f in archivos for s in parse_file(f)], file_ids)
    nodos = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM cm.nodes WHERE tenant_id = %s AND kind IN ('file', 'symbol') "
            "ORDER BY id",
            (tenant,),
        ).fetchall()
    ]
    nombres = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM cm.symbols WHERE tenant_id = %s ORDER BY id", (tenant,)
        ).fetchall()
    ]
    rnd = random.Random(803)
    with conn.cursor() as cur:
        for i in range(N_CHUNKS):
            _chunk(conn, tenant, f"cb_{i:04d}", i)
        cur.executemany(
            "INSERT INTO cm.doc_chunk_links (tenant_id, chunk_id, nodo_ref, metodo, score) "
            "VALUES (%s, %s, %s, 'semantico', %s) ON CONFLICT DO NOTHING",
            [
                (tenant, f"cb_{i:04d}", rnd.choice(nodos), round(rnd.uniform(0.6, 1.0), 3))
                for i in range(N_CHUNKS)
                for _ in range(2)
            ],
        )
        cur.executemany(
            """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, scope_refs, evidencia_ref)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
            [
                (
                    f"nb_{i:05d}",
                    tenant,
                    f"[{tenant}] regla sintética {i} " + "x" * rnd.randint(20, 200),
                    rnd.choice(("regla_negocio", "restriccion_tecnica")),
                    json.dumps(rnd.sample(nombres, k=min(len(nombres), rnd.randint(0, 2)))),
                    f"cb_{rnd.randrange(N_CHUNKS):04d}",
                )
                for i in range(N_APROBADOS + N_PROPUESTOS)
            ],
        )
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
    conn.execute(
        "SELECT count(cm.resolver_nugget(id, %s, 'aprobado')) FROM ("
        "  SELECT id FROM cm.nuggets WHERE tenant_id = %s ORDER BY id LIMIT %s) a",
        (usuario, tenant, N_APROBADOS),
    )
    conn.execute("SELECT set_config('app.tenant_id', '', false)")
    conn.execute("ANALYZE cm.nuggets")
    conn.execute("ANALYZE cm.doc_chunk_links")
    return [a.path for a in archivos]


@pytest.fixture(scope="module")
def grafo_referencia() -> Iterator[list[str]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        rutas = _sembrar_benchmark(conn, TENANT_BENCH)
        _sembrar_benchmark(conn, TENANT_BENCH_OTRO)  # vecino con el mismo volumen
        yield rutas
        _limpiar(conn)


def test_latencia_p95_bajo_200ms_en_el_grafo_de_referencia(grafo_referencia: list[str]) -> None:
    rnd = random.Random(8033)
    modulos = sorted({r.rsplit("/", 1)[0] for r in grafo_referencia if "/" in r})
    consultas = []
    for _ in range(200):
        tipo = rnd.random()
        if tipo < 0.5:
            consultas.append(
                ConsultaNuggetsIn(variable="nuggets", ficheros=rnd.sample(grafo_referencia, k=3))
            )
        elif tipo < 0.8:
            consultas.append(
                ConsultaNuggetsIn(
                    variable="nuggets_for_file", ficheros=[rnd.choice(grafo_referencia)]
                )
            )
        else:
            consultas.append(
                ConsultaNuggetsIn(variable="module_conventions", modulo=rnd.choice(modulos))
            )

    tiempos: list[float] = []
    entregados = 0
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_BENCH) as loader:
        for consulta in consultas[:10]:  # calentamiento (conexión y caché de planes)
            resolver_nuggets(loader, consulta)
        for consulta in consultas:
            inicio = time.perf_counter()
            r = resolver_nuggets(loader, consulta)
            tiempos.append(time.perf_counter() - inicio)
            entregados += len(r.nuggets)
            assert all(n.regla.startswith(f"[{TENANT_BENCH}]") for n in r.nuggets)

    p50 = statistics.median(tiempos) * 1000
    p95 = statistics.quantiles(tiempos, n=20, method="inclusive")[18] * 1000
    print(f"cm_queries: p50={p50:.1f} ms p95={p95:.1f} ms ({len(tiempos)} consultas)")
    assert entregados > 0, "el benchmark debe devolver nuggets para ser representativo"
    presupuesto = cm_grafo.presupuesto_p95_ms("resolver_nuggets")  # fuente: contrato cm-grafo-v1
    assert p95 < presupuesto, f"p95 = {p95:.1f} ms (p50 = {p50:.1f} ms) supera {presupuesto} ms"
