"""Benchmark de la ingesta del grafo a escala: 200.000 nodos y 260.000 aristas (KC-CM-11-1).

Ejecuta el pipeline REAL (`build_graph.full_build` y `refresh.run_refresh`: recorrido del repositorio,
parseo AST con pool de procesos, checkpoint, carga masiva con COPY, esquema, capa semántica y
reachability) sobre el repositorio sintético de `repo_sintetico`, con la base real y el rol de la
aplicación (kallicode_app, con RLS). Solo se sustituye lo que está fuera del alcance de la carga del
grafo: el LLM, el servicio de embeddings, la base del cliente de la que se extrae el esquema y la
escritura de los vectores (que mide KC-CM-08-4 con 200.000 vectores).

Umbrales (falla si no se cumplen):
- ingesta completa de 200.000 nodos y 260.000 aristas en menos de 90 minutos;
- RSS máximo del proceso y sus hijos (pool de parseo) por debajo de 3 GiB;
- refresco diario de 200 archivos modificados en menos de 5 minutos;
- recuentos por capa y por tipo de arista IDÉNTICOS a los del generador, tras la ingesta y tras el
  refresco (sin pérdidas ni duplicados).

El informe trae el tiempo y el RSS por fase y el plan de índices real de las tablas cm.* (tamaño y uso
durante la carga): es el insumo para Database (docs/rendimiento_ingesta.md).

No es un test unitario: la escala completa tarda decenas de minutos y por eso el archivo no se llama
`test_*`. Se ejecuta a propósito (CI nocturno, ingesta_escala_nocturno.yml):

    uv run pytest packages/codemapping/tests/bench_ingesta_escala.py -v -s
    uv run python packages/codemapping/tests/bench_ingesta_escala.py        # imprime el informe

Variables: KC_DATABASE_URL_MIGRATIONS (superusuario, obligatoria), KC_BENCH_ING_DIVISOR (1 = escala
completa; N = 1/N, para probar en local), KC_BENCH_ING_MODIFICADOS (200), KC_BENCH_RESULTADOS (ruta del
informe Markdown), KC_BENCH_CONSERVAR (no borra los datos al terminar).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from kallicode_codemapping import build_graph, checkpoints, refresh, repo_sintetico
from kallicode_codemapping.config import Settings
from kallicode_codemapping.extract_sql import ResultadoUsoTablas
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn

pytestmark = pytest.mark.benchmark

TENANT = "bench_ing_escala"
UMBRAL_TOTAL_S = 90 * 60
UMBRAL_RSS_MB = 3 * 1024  # 3 GiB
UMBRAL_REFRESCO_S = 5 * 60
MODIFICADOS = int(os.getenv("KC_BENCH_ING_MODIFICADOS", "200"))
_PAGINA = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

TABLAS_NODO = {
    "files": "cm.files",
    "symbols": "cm.symbols",
    "db_tables": "cm.db_tables",
    "db_columns": "cm.db_columns",
    "definitions": "cm.definitions",
    "concepts": "cm.concepts",
}
TABLAS_INDICES = (*TABLAS_NODO.values(), "cm.edges")


def _dsn_admin() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")  # en CI siempre está
    return normalizar_dsn(url)


def _dsn_app(admin: str) -> str:
    return admin + ("&" if "?" in admin else "?") + "options=-c%20role%3Dkallicode_app"


# ---------------------------------------------------------------------------
# Medición: tiempo y RSS por fase
# ---------------------------------------------------------------------------
def _rss_arbol_mb(raiz: int) -> float:
    """RSS (MiB) de `raiz` y de todos sus descendientes, leído de /proc (Linux)."""
    hijos: dict[int, list[int]] = {}
    paginas: dict[int, int] = {}
    for entrada in os.listdir("/proc"):
        if not entrada.isdigit():
            continue
        try:
            with open(f"/proc/{entrada}/stat", encoding="utf-8") as fh:
                resto = fh.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        pid = int(entrada)
        hijos.setdefault(int(resto[1]), []).append(pid)
        paginas[pid] = int(resto[21])
    total, pila = 0, [raiz]
    while pila:
        pid = pila.pop()
        total += paginas.get(pid, 0)
        pila.extend(hijos.get(pid, []))
    return total * _PAGINA / (1024 * 1024)


class Medidor:
    """Muestrea el RSS del árbol de procesos y lo atribuye a la fase en curso."""

    def __init__(self, intervalo: float = 0.25) -> None:
        self.intervalo = intervalo
        self.fases: dict[str, dict[str, float]] = {}
        self.pico_mb = 0.0
        self._actual: list[str] = []
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._muestrear, daemon=True)

    def __enter__(self) -> Medidor:
        self._hilo.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._parar.set()
        self._hilo.join(timeout=5)

    def _muestrear(self) -> None:
        raiz = os.getpid()
        while not self._parar.is_set():
            mb = _rss_arbol_mb(raiz)
            self.pico_mb = max(self.pico_mb, mb)
            for nombre in tuple(self._actual):
                fase = self.fases.get(nombre)
                if fase is not None:
                    fase["rss_pico_mb"] = max(fase["rss_pico_mb"], mb)
            self._parar.wait(self.intervalo)

    @contextmanager
    def fase(self, nombre: str) -> Iterator[None]:
        datos = self.fases.setdefault(nombre, {"duracion_s": 0.0, "rss_pico_mb": 0.0, "veces": 0})
        datos["rss_pico_mb"] = max(datos["rss_pico_mb"], _rss_arbol_mb(os.getpid()))
        self._actual.append(nombre)
        inicio = time.perf_counter()
        try:
            yield
        finally:
            datos["duracion_s"] += time.perf_counter() - inicio
            datos["veces"] += 1
            self._actual.remove(nombre)


# ---------------------------------------------------------------------------
# Dobles de lo que queda fuera del alcance (LLM, embeddings, base del cliente, vectores)
# ---------------------------------------------------------------------------
_VECTOR_CERO = [0.0] * 1024


class _TEIFalso:
    def __init__(self, base_url: str | None = None) -> None:
        pass

    def embed_texts_sync(self, textos: list[str]) -> list[list[float]]:
        return [_VECTOR_CERO for _ in textos]


def _dobles(escala: repo_sintetico.Escala) -> list[Any]:
    tablas, fks = repo_sintetico.esquema(escala)
    usos = repo_sintetico.usos_de_tablas(escala)
    estado: dict[str, dict[str, str]] = {}
    miembros = {
        repo_sintetico.nombre_simbolo(m)
        for d in range(escala.definiciones)
        for m in repo_sintetico.ids_miembros(escala, d)
    }

    def describe_symbols(_settings: Any, symbols: list[Any]) -> list[dict[str, Any]]:
        estado["ids"] = {s.name: s.id for s in symbols}
        return [
            {"id": s.id, "description_en": "d", "role": "domain"}
            for s in symbols
            if s.name in miembros
        ]

    def consolidate_definitions(_s: Any, _modulo: str, _descritos: Any) -> list[dict[str, Any]]:
        return repo_sintetico.definiciones(escala, estado["ids"])

    return [
        patch.object(build_graph, "extract_schema", lambda _s: (tablas, fks)),
        patch.object(
            build_graph, "detectar_uso_tablas", lambda _f, _t: ResultadoUsoTablas(usage=usos)
        ),
        patch.object(build_graph, "describe_symbols", describe_symbols),
        patch.object(build_graph, "consolidate_definitions", consolidate_definitions),
        patch.object(
            build_graph, "induce_ontology", lambda *_a, **_k: repo_sintetico.ontologia(escala)
        ),
        patch.object(
            build_graph, "link_definitions", lambda **_k: (repo_sintetico.enlaces(escala), [])
        ),
        patch.object(build_graph, "TEIEmbeddingClient", _TEIFalso),
        # Presupuesto de tokens del LLM: no es carga del grafo (y consulta core.ciclo_inicio, que no existe).
        patch.object(
            build_graph,
            "verificar_presupuesto_tenant",
            lambda **_k: SimpleNamespace(
                total_tokens_estimados=0, costo_usd_estimado=0.0, cuota_disponible=10**12
            ),
        ),
        patch.object(
            refresh,
            "describe_symbols",
            lambda _s, symbols: [{"id": s.id, "description_en": "d"} for s in symbols],
        ),
        patch.object(refresh, "TEIEmbeddingClient", _TEIFalso),
        patch.object(GraphLoader, "upsert_vectors", lambda self, items, modelo="bge-m3": None),
    ]


def _instrumentar(m: Medidor) -> list[Any]:
    """Envuelve las fases del pipeline real para medir su tiempo y su RSS, sin cambiar su lógica."""
    recorrer = build_graph.walk_sources
    parsear = build_graph.parsear_fuentes_en_streaming
    guardar = checkpoints.guardar
    fase_original = GraphLoader.fase

    def walk_sources(repo: str, settings: Settings) -> Iterator[Any]:
        with m.fase("1 recorrido del repositorio"):
            lista = list(recorrer(repo, settings))
        return iter(lista)

    def parsear_en_streaming(*a: Any, **k: Any) -> Generator[list[Any], None, None]:
        with m.fase("2 parseo AST (pool de procesos)"):
            yield from parsear(*a, **k)

    def checkpoints_guardar(*a: Any, **k: Any) -> Any:
        with m.fase("3 checkpoint local de la ingesta"):
            return guardar(*a, **k)

    @contextmanager
    def fase(self: GraphLoader, nombre: str) -> Iterator[Any]:
        with m.fase(f"carga: {nombre}"), fase_original(self, nombre) as cur:
            yield cur

    def envolver(modulo: Any, nombre: str, etiqueta: str) -> Any:
        original: Callable[..., Any] = getattr(modulo, nombre)

        def f(*a: Any, **k: Any) -> Any:
            with m.fase(etiqueta):
                return original(*a, **k)

        return patch.object(modulo, nombre, f)

    return [
        patch.object(build_graph, "walk_sources", walk_sources),
        patch.object(build_graph, "parsear_fuentes_en_streaming", parsear_en_streaming),
        patch.object(checkpoints, "guardar", checkpoints_guardar),
        patch.object(GraphLoader, "fase", fase),
        envolver(refresh, "refrescar_reachability_del_refresco", "refresco: cm.reachability"),
        envolver(refresh, "reparar_vigencia_del_refresco", "refresco: vigencia de embeddings"),
        envolver(refresh, "refrescar_capa_documental", "refresco: capa documental"),
        envolver(refresh, "changed_paths", "refresco: git diff"),
    ]


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for tabla in (
        "vec.definition_embeddings",
        "cm.reachability",
        "cm.doc_chunk_links",
        "cm.edges",
        "cm.symbols",
        "cm.files",
        "cm.db_columns",
        "cm.db_tables",
        "cm.definitions",
        "cm.concepts",
        "cm.curation_queue",
        "cm.repo_snapshots",
        "core.usage_tokens",
    ):
        if conn.execute("SELECT to_regclass(%s)", (tabla,)).fetchone() == (None,):
            continue
        conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.organizations WHERE id = %s", (TENANT,))


def contar(conn: psycopg.Connection[Any]) -> dict[str, dict[str, int]]:
    """Recuentos reales por capa (nodos) y por tipo de arista, del tenant del benchmark."""
    nodos = {
        capa: int(
            conn.execute(
                f"SELECT count(*) FROM {tabla} WHERE tenant_id = %s", (TENANT,)
            ).fetchone()[  # type: ignore[index]
                0
            ]
        )
        for capa, tabla in TABLAS_NODO.items()
    }
    aristas = {
        tipo: int(n)
        for tipo, n in conn.execute(
            "SELECT tipo, count(*) FROM cm.edges WHERE tenant_id = %s GROUP BY tipo", (TENANT,)
        ).fetchall()
    }
    return {
        "nodos": nodos,
        "aristas": aristas,
        "total": {"nodos": sum(nodos.values()), "aristas": sum(aristas.values())},
    }


def plan_de_indices(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    """Índices reales de las tablas cm.* con su tamaño y su uso acumulado."""
    filas = conn.execute(
        """
        SELECT c.relname, i.relname, pg_relation_size(i.oid), COALESCE(s.idx_scan, 0),
               x.indisunique, x.indisprimary, pg_get_indexdef(i.oid)
          FROM pg_index x
          JOIN pg_class c ON c.oid = x.indrelid
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.oid
         WHERE n.nspname = 'cm' AND 'cm.' || c.relname = ANY(%s)
         ORDER BY c.relname, pg_relation_size(i.oid) DESC
        """,
        (list(TABLAS_INDICES),),
    ).fetchall()
    return [
        {
            "tabla": f[0],
            "indice": f[1],
            "bytes": int(f[2]),
            "idx_scan": int(f[3]),
            "unico": bool(f[4]),
            "pk": bool(f[5]),
            "definicion": f[6],
        }
        for f in filas
    ]


def tamano_de_tablas(conn: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    filas = conn.execute(
        "SELECT relname, pg_table_size(oid), pg_indexes_size(oid), n_live_tup "
        "FROM pg_class c JOIN pg_stat_user_tables s USING (relname) "
        "WHERE s.schemaname = 'cm' AND 'cm.' || relname = ANY(%s) AND c.relnamespace = 'cm'::regnamespace",
        (list(TABLAS_INDICES),),
    ).fetchall()
    return [
        {"tabla": f[0], "bytes_tabla": int(f[1]), "bytes_indices": int(f[2]), "filas": int(f[3])}
        for f in sorted(filas)
    ]


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------
def _escala() -> repo_sintetico.Escala:
    divisor = int(os.getenv("KC_BENCH_ING_DIVISOR", "1"))
    return (
        repo_sintetico.Escala.completa()
        if divisor == 1
        else repo_sintetico.Escala.reducida(divisor)
    )


def _modificados(escala: repo_sintetico.Escala) -> list[int]:
    return repo_sintetico.archivos_a_modificar(escala, MODIFICADOS)


def ejecutar() -> dict[str, Any]:
    admin = _dsn_admin()
    app = _dsn_app(admin)
    escala = _escala()
    indices_modificados = _modificados(escala)
    base = Path(tempfile.mkdtemp(prefix="kc_bench_ingesta_"))
    informe: dict[str, Any] = {"escala": escala.esperado(), "modificados": len(indices_modificados)}
    with psycopg.connect(admin, autocommit=True) as conn:
        _limpiar(conn)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (TENANT, TENANT)
        )
    try:
        origen = base / "origen"
        t0 = time.perf_counter()
        sha_base = repo_sintetico.crear_repositorio_git(origen, escala)
        informe["generacion_repositorio_s"] = time.perf_counter() - t0
        settings = Settings(
            tenant_id=TENANT,
            repo_url=str(origen),
            workdir=str(base / "workdir"),
            store_database_url=app,
            database_url="postgresql://cliente:x@localhost:5432/cliente",  # type: ignore[arg-type]
        )
        with Medidor() as m, _aplicar(_dobles(escala) + _instrumentar(m)):
            inicio = time.perf_counter()
            build_graph.full_build(settings)
            informe["ingesta_s"] = time.perf_counter() - inicio
            informe["ingesta_rss_pico_mb"] = m.pico_mb
            informe["fases_ingesta"] = json.loads(json.dumps(m.fases))
            with psycopg.connect(admin, autocommit=True) as conn:
                informe["recuentos_ingesta"] = contar(conn)
                informe["indices"] = plan_de_indices(conn)
                informe["tablas"] = tamano_de_tablas(conn)

            m.fases.clear()
            m.pico_mb = 0.0
            sha_nuevo = repo_sintetico.modificar_archivos(origen, escala, indices_modificados)
            inicio = time.perf_counter()
            refresh.run_refresh(settings)
            informe["refresco_s"] = time.perf_counter() - inicio
            informe["refresco_rss_pico_mb"] = m.pico_mb
            informe["fases_refresco"] = json.loads(json.dumps(m.fases))
            with psycopg.connect(admin, autocommit=True) as conn:
                informe["recuentos_refresco"] = contar(conn)
                informe["snapshots"] = [
                    {"commit": f[0][:10], "estado": f[1], "fase": f[2]}
                    for f in conn.execute(
                        "SELECT commit_sha, estado, fase_actual FROM cm.repo_snapshots "
                        "WHERE tenant_id = %s ORDER BY iniciado_en",
                        (TENANT,),
                    ).fetchall()
                ]
        informe["commits"] = {"base": sha_base[:10], "refresco": sha_nuevo[:10]}
        informe["esperado_refresco"] = repo_sintetico.esperado_tras_refresco(
            escala, len(indices_modificados)
        )
    finally:
        shutil.rmtree(base, ignore_errors=True)
        if not os.getenv("KC_BENCH_CONSERVAR"):
            with psycopg.connect(admin, autocommit=True) as conn:
                _limpiar(conn)
    return informe


@contextmanager
def _aplicar(parches: list[Any]) -> Iterator[None]:
    for p in parches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(parches):
            p.stop()


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------
def _mb(n: float) -> str:
    return f"{n / (1024 * 1024):.1f}"


def _fases_md(fases: dict[str, dict[str, float]]) -> list[str]:
    lineas = ["| fase | duración (s) | RSS pico (MiB) | veces |", "|---|---:|---:|---:|"]
    for nombre, f in sorted(fases.items()):
        lineas.append(
            f"| {nombre} | {f['duracion_s']:.1f} | {f['rss_pico_mb']:.0f} | {int(f['veces'])} |"
        )
    return lineas


def _diferencias(real: dict[str, dict[str, int]], esperado: dict[str, dict[str, int]]) -> list[str]:
    malas = []
    for grupo in ("nodos", "aristas"):
        for clave in sorted(set(real[grupo]) | set(esperado[grupo])):
            r, e = real[grupo].get(clave, 0), esperado[grupo].get(clave, 0)
            if r != e:
                malas.append(f"{grupo}/{clave}: real {r} != esperado {e}")
    return malas


def informe_markdown(informe: dict[str, Any]) -> str:
    esperado = informe["escala"]
    l: list[str] = [
        "## Ingesta del grafo a escala (KC-CM-11-1)",
        "",
        f"Escala: {esperado['total']['nodos']:,} nodos y {esperado['total']['aristas']:,} aristas "
        f"(generación del repositorio: {informe['generacion_repositorio_s']:.0f} s).",
        "",
        "| medida | valor | umbral |",
        "|---|---:|---:|",
        f"| ingesta completa | {informe['ingesta_s'] / 60:.1f} min | < {UMBRAL_TOTAL_S / 60:.0f} min |",
        f"| RSS pico de la ingesta (proceso + pool) | {informe['ingesta_rss_pico_mb']:.0f} MiB | < {UMBRAL_RSS_MB} MiB |",
        f"| refresco de {informe['modificados']} archivos | {informe['refresco_s'] / 60:.2f} min | < {UMBRAL_REFRESCO_S / 60:.0f} min |",
        f"| RSS pico del refresco | {informe['refresco_rss_pico_mb']:.0f} MiB | < {UMBRAL_RSS_MB} MiB |",
        "",
        "### Recuentos por capa: real frente a generador",
        "",
        "| capa / arista | generador | tras la ingesta | tras el refresco |",
        "|---|---:|---:|---:|",
    ]
    for grupo in ("nodos", "aristas"):
        for clave, e in esperado[grupo].items():
            l.append(
                f"| {clave} | {e:,} | {informe['recuentos_ingesta'][grupo].get(clave, 0):,} | "
                f"{informe['recuentos_refresco'][grupo].get(clave, 0):,} "
                f"(esperado {informe['esperado_refresco'][grupo][clave]:,}) |"
            )
    l += ["", "### Fases de la ingesta", "", *_fases_md(informe["fases_ingesta"])]
    l += ["", "### Fases del refresco", "", *_fases_md(informe["fases_refresco"])]
    l += [
        "",
        "### Plan de índices de cm.* (para Database)",
        "",
        "| tabla | índice | tamaño (MiB) | usos (idx_scan) | clase |",
        "|---|---|---:|---:|---|",
    ]
    for ix in informe["indices"]:
        clase = "PK" if ix["pk"] else ("único" if ix["unico"] else "secundario")
        l.append(
            f"| {ix['tabla']} | {ix['indice']} | {_mb(ix['bytes'])} | {ix['idx_scan']:,} | {clase} |"
        )
    l += [
        "",
        "| tabla | filas (estimadas por estadísticas) | datos (MiB) | índices (MiB) |",
        "|---|---:|---:|---:|",
    ]
    for t in informe["tablas"]:
        l.append(
            f"| {t['tabla']} | {t['filas']:,} | {_mb(t['bytes_tabla'])} | {_mb(t['bytes_indices'])} |"
        )
    return "\n".join(l) + "\n"


def _guardar(texto: str, datos: dict[str, Any]) -> None:
    destino = os.getenv("KC_BENCH_RESULTADOS")
    if destino:
        ruta = Path(destino)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(texto, encoding="utf-8")
        ruta.with_suffix(".json").write_text(json.dumps(datos, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def informe() -> dict[str, Any]:
    datos = ejecutar()
    texto = informe_markdown(datos)
    print("\n" + texto)
    _guardar(texto, datos)
    return datos


def test_ingesta_completa_en_menos_de_90_minutos(informe: dict[str, Any]) -> None:
    assert informe["ingesta_s"] < UMBRAL_TOTAL_S, f"{informe['ingesta_s'] / 60:.1f} min"


def test_rss_maximo_de_la_ingesta_bajo_3_gib(informe: dict[str, Any]) -> None:
    assert informe["ingesta_rss_pico_mb"] < UMBRAL_RSS_MB, (
        f"{informe['ingesta_rss_pico_mb']:.0f} MiB"
    )


def test_refresco_de_200_archivos_en_menos_de_5_minutos(informe: dict[str, Any]) -> None:
    assert informe["refresco_s"] < UMBRAL_REFRESCO_S, f"{informe['refresco_s'] / 60:.2f} min"
    assert informe["refresco_rss_pico_mb"] < UMBRAL_RSS_MB


def test_recuentos_por_capa_identicos_al_generador(informe: dict[str, Any]) -> None:
    assert not _diferencias(informe["recuentos_ingesta"], informe["escala"])
    assert informe["recuentos_ingesta"]["total"] == informe["escala"]["total"]


def test_recuentos_tras_el_refresco_sin_perdidas_ni_duplicados(informe: dict[str, Any]) -> None:
    assert not _diferencias(informe["recuentos_refresco"], informe["esperado_refresco"])


def test_ambas_corridas_quedaron_completas(informe: dict[str, Any]) -> None:
    assert [s["estado"] for s in informe["snapshots"]] == ["completo", "completo"]


if __name__ == "__main__":  # pragma: no cover
    datos = ejecutar()
    texto = informe_markdown(datos)
    _guardar(texto, datos)
    sys.stdout.write(texto)
    sys.exit(0 if not _diferencias(datos["recuentos_ingesta"], datos["escala"]) else 1)
