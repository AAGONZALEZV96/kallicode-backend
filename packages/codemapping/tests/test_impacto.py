"""Recorrido del grafo contra PostgreSQL 16 real (KC-CM-09-1).

Requiere `make up && make db`. Las consultas corren como kallicode_app con el tenant fijado por
GraphLoader.fase(), igual que el pipeline; el administrador solo siembra datos (y, en la prueba
de aislamiento, consulta saltándose RLS para demostrar el filtro explícito de cada nivel).

Criterios:
- Un grafo con ciclos no cuelga ni desborda: ciclo de longitud tres y autorreferencia.
- Toda consulta filtra por tenant en cada nivel del recorrido: dos organizaciones con los MISMOS
  ids y topologías distintas no se ven, ni siquiera sin RLS (superusuario).
- Equivalencia con una implementación de referencia en Python (BFS por niveles) sobre un grafo
  sintético determinista, con 200 nodos semilla.
- p95 del recorrido a tres saltos < 500 ms sobre ese grafo de referencia (bloquea el merge).
"""

from __future__ import annotations

import os
import random
import statistics
import time
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import cm_queries
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_core import cm_impacto
from kallicode_core.cm_impacto import ConsultaGrafoIn, Direccion, TipoArista

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT_A = "org_test_imp_a"
TENANT_B = "org_test_imp_b"
TENANT_SINT = "org_test_imp_sint"
TENANT_SINT_OTRO = "org_test_imp_sint_b"
TODOS = (TENANT_A, TENANT_B, TENANT_SINT, TENANT_SINT_OTRO)
SNAPSHOT = "snap_imp"

PRESUPUESTO_P95_MS = 500
N_SEMILLAS = 200

Fichero = tuple[str, str, list[str]]  # (id, path, símbolos)
AristaT = tuple[str, str, str]  # (tipo, src, dst)


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute("SELECT to_regclass('cm.nodes') IS NOT NULL").fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o esquema cm sin aplicar (make db)"
)


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        for tabla in (
            "cm.edges",
            "cm.curation_queue",
            "cm.symbols",
            "cm.files",
            "cm.db_tables",
            "cm.nodes",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _sembrar(
    conn: psycopg.Connection[Any],
    tenant: str,
    ficheros: Sequence[Fichero],
    tablas: Sequence[str],
    aristas: Iterable[AristaT],
) -> None:
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
        "VALUES (%s, %s, 'sha', 'main')",
        (SNAPSHOT, tenant),
    )
    kinds: dict[str, str] = {}
    with conn.cursor() as cur:
        with cur.copy(
            "COPY cm.files (id, tenant_id, path, language, sha256, snapshot_id) FROM STDIN"
        ) as cp:
            for fid, path, _ in ficheros:
                cp.write_row((fid, tenant, path, "python", "h", SNAPSHOT))
                kinds[fid] = "file"
        with cur.copy(
            "COPY cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) FROM STDIN"
        ) as cp:
            for fid, _, simbolos in ficheros:
                for i, sid in enumerate(simbolos):
                    cp.write_row((sid, tenant, fid, "function", sid, i + 1, i + 1, f"def {sid}()"))
                    kinds[sid] = "symbol"
        with cur.copy("COPY cm.db_tables (id, tenant_id, esquema, nombre) FROM STDIN") as cp:
            for tid in tablas:
                cp.write_row((tid, tenant, "public", tid))
                kinds[tid] = "db_table"
        with cur.copy(
            "COPY cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id) FROM STDIN"
        ) as cp:
            for tipo, src, dst in aristas:
                cp.write_row((tenant, tipo, kinds[src], src, kinds[dst], dst))


# Grafo pequeño, mismos ids en A y B y topologías distintas:
#   A: a -> b -> c -> a (ciclo de 3), d -> a, e -> d, s -> s (autorreferencia),
#      c USES_TABLE t1, h USES_TABLE t1 (h acoplado a a por la tabla, sin llamada)
#   B: a -> b -> z -> w, w USES_TABLE t1
FICHEROS_PEQUENO: list[Fichero] = [
    ("fa", "src/a.py", ["a", "b", "c"]),
    ("fd", "src/d.py", ["d", "e", "h", "s", "w", "z"]),
]
ARISTAS_A: list[AristaT] = [
    ("CALLS", "a", "b"),
    ("CALLS", "b", "c"),
    ("CALLS", "c", "a"),
    ("CALLS", "d", "a"),
    ("CALLS", "e", "d"),
    ("CALLS", "s", "s"),
    ("USES_TABLE", "c", "t1"),
    ("USES_TABLE", "h", "t1"),
]
ARISTAS_B: list[AristaT] = [
    ("CALLS", "a", "b"),
    ("CALLS", "b", "z"),
    ("CALLS", "z", "w"),
    ("USES_TABLE", "w", "t1"),
]


# ---------------------------------------------------------------------------
# Grafo sintético determinista e implementación de referencia
# ---------------------------------------------------------------------------
@dataclass
class GrafoRef:
    """El mismo grafo en memoria, con un BFS por niveles como semántica de referencia."""

    ficheros: list[Fichero]
    tablas: list[str]
    aristas: list[AristaT]
    kinds: dict[str, str] = field(default_factory=dict)
    salientes: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    entrantes: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for fid, _, simbolos in self.ficheros:
            self.kinds[fid] = "file"
            self.kinds.update(dict.fromkeys(simbolos, "symbol"))
        self.kinds.update(dict.fromkeys(self.tablas, "db_table"))
        sal: dict[str, list[tuple[str, str]]] = defaultdict(list)
        ent: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for tipo, src, dst in self.aristas:
            sal[src].append((tipo, dst))
            ent[dst].append((tipo, src))
        self.salientes, self.entrantes = sal, ent

    def semillas(self, nodos: Sequence[str] = (), ficheros: Sequence[str] = ()) -> set[str]:
        out = {n for n in nodos if n in self.kinds}
        for fid, path, simbolos in self.ficheros:
            if path in ficheros:
                out |= {fid, *simbolos}
        return out

    def _vecinos(self, nodo: str, direccion: Direccion) -> list[tuple[str, str]]:
        res: list[tuple[str, str]] = []
        if direccion in ("entrante", "ambas"):
            res += self.entrantes.get(nodo, [])
        if direccion in ("saliente", "ambas"):
            res += self.salientes.get(nodo, [])
        return res

    def recorrer(
        self, semillas: set[str], k: int, tipos: Iterable[str], direccion: Direccion
    ) -> list[tuple[str, str, int, str | None]]:
        permitidos = set(tipos)
        distancia = dict.fromkeys(semillas, 0)
        via: dict[str, str | None] = dict.fromkeys(semillas)
        frontera = list(semillas)
        for d in range(1, k + 1):
            nuevos: dict[str, str] = {}
            for n in frontera:
                for tipo, m in self._vecinos(n, direccion):
                    if tipo in permitidos and m not in distancia:
                        nuevos[m] = min(nuevos.get(m, tipo), tipo)
            for m, tipo in nuevos.items():
                distancia[m], via[m] = d, tipo
            frontera = list(nuevos)
        filas = [(n, self.kinds[n], distancia[n], via[n]) for n in distancia]
        return sorted(filas, key=lambda f: (f[2], f[0]))

    def ocultas(self, semillas: set[str], k: int) -> list[tuple[str, list[str], int]]:
        alcance = {n: d for n, _, d, _ in self.recorrer(semillas, k, ["CALLS"], "saliente")}
        tablas: dict[str, int] = {}
        for n, d in alcance.items():
            for tipo, t in self.salientes.get(n, []):
                if tipo == "USES_TABLE":
                    tablas[t] = min(tablas.get(t, d), d)
        acoplados: dict[str, tuple[set[str], int]] = {}
        for t, d in tablas.items():
            for tipo, src in self.entrantes.get(t, []):
                if tipo == "USES_TABLE" and src not in alcance:
                    ts, dmin = acoplados.get(src, (set(), d))
                    acoplados[src] = (ts | {t}, min(dmin, d))
        filas = [(n, sorted(ts), d) for n, (ts, d) in acoplados.items()]
        return sorted(filas, key=lambda f: (f[2], f[0]))

    def aristas_entre(self, ids: set[str], tipos: Iterable[str]) -> list[AristaT]:
        permitidos = set(tipos)
        filas = [a for a in self.aristas if a[0] in permitidos and a[1] in ids and a[2] in ids]
        return sorted(filas, key=lambda a: (a[1], a[0], a[2]))


def _grafo_sintetico(semilla: int) -> GrafoRef:
    """~4.350 nodos y ~21.000 aristas, con ciclos, autorreferencias y tablas compartidas."""
    rnd = random.Random(semilla)
    ficheros: list[Fichero] = [
        (f"f{i:03d}", f"src/m{i % 10}/f{i:03d}.py", [f"s{i:03d}_{j:02d}" for j in range(20)])
        for i in range(200)
    ]
    simbolos = [s for _, _, ss in ficheros for s in ss]
    tablas = [f"t{i:03d}" for i in range(150)]
    aristas: set[AristaT] = set()
    for fid, _, ss in ficheros:
        aristas |= {("DEFINES", fid, s) for s in ss}
    for s in simbolos:
        aristas |= {("CALLS", s, rnd.choice(simbolos)) for _ in range(rnd.randint(1, 7))}
        if rnd.random() < 0.3:
            aristas |= {("USES_TABLE", s, rnd.choice(tablas)) for _ in range(rnd.randint(1, 2))}
    aristas |= {("REFERENCES", rnd.choice(tablas), rnd.choice(tablas)) for _ in range(200)}
    return GrafoRef(ficheros, tablas, sorted(aristas))


GRAFO_SINT = _grafo_sintetico(20260928)
# Mismos ids, otra topología: si un nivel del recorrido no filtrara por tenant, la equivalencia
# con GRAFO_SINT fallaría.
GRAFO_SINT_OTRO = _grafo_sintetico(7)


@pytest.fixture(scope="module")
def grafos() -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        _sembrar(conn, TENANT_A, FICHEROS_PEQUENO, ["t1"], ARISTAS_A)
        _sembrar(conn, TENANT_B, FICHEROS_PEQUENO, ["t1"], ARISTAS_B)
        for tenant, g in ((TENANT_SINT, GRAFO_SINT), (TENANT_SINT_OTRO, GRAFO_SINT_OTRO)):
            _sembrar(conn, tenant, g.ficheros, g.tablas, g.aristas)
            conn.execute("ANALYZE cm.edges")
        yield
        _limpiar(conn)


def _nodos(r: cm_impacto.RecorridoOut | cm_impacto.SubgrafoOut) -> list[tuple[Any, ...]]:
    return [(n.id, n.distancia, n.via_tipo) for n in r.nodos]


def _loader(tenant: str, dsn: str = APP_DSN) -> GraphLoader:
    return GraphLoader(dsn=dsn, tenant_id=tenant)


# ---------------------------------------------------------------------------
# Ciclos y semántica sobre el grafo pequeño
# ---------------------------------------------------------------------------
def test_ciclo_de_longitud_tres_no_cuelga_y_da_distancias_minimas(grafos: None) -> None:
    with _loader(TENANT_A) as loader:
        imp = cm_queries.impacto(loader, ConsultaGrafoIn(nodos=["a"], k=cm_impacto.K_MAX))
        dep = cm_queries.dependencias(loader, ConsultaGrafoIn(nodos=["a"], k=cm_impacto.K_MAX))
    calls, uses = TipoArista.CALLS, TipoArista.USES_TABLE
    assert _nodos(imp) == [
        ("a", 0, None),
        ("c", 1, calls),
        ("d", 1, calls),
        ("b", 2, calls),
        ("e", 2, calls),
    ]
    assert _nodos(dep) == [("a", 0, None), ("b", 1, calls), ("c", 2, calls), ("t1", 3, uses)]
    assert imp.semillas == ["a"] and not imp.truncado


def test_autorreferencia_no_cuelga(grafos: None) -> None:
    consulta = ConsultaGrafoIn(nodos=["s"], k=cm_impacto.K_MAX)
    with _loader(TENANT_A) as loader:
        assert _nodos(cm_queries.impacto(loader, consulta)) == [("s", 0, None)]
        assert _nodos(cm_queries.dependencias(loader, consulta)) == [("s", 0, None)]
        sub = cm_queries.subgrafo(loader, consulta)
    assert _nodos(sub) == [("s", 0, None)]
    assert [(a.src_id, a.dst_id) for a in sub.aristas] == [("s", "s")]


def test_k_tipos_y_ficheros(grafos: None) -> None:
    with _loader(TENANT_A) as loader:
        k1 = cm_queries.impacto(loader, ConsultaGrafoIn(nodos=["a"], k=1))
        solo_calls = cm_queries.dependencias(
            loader,
            ConsultaGrafoIn(nodos=["c"], k=5, tipos=[TipoArista.CALLS]),
        )
        tabla = cm_queries.impacto(loader, ConsultaGrafoIn(nodos=["t1"], k=2))
        fichero = cm_queries.impacto(loader, ConsultaGrafoIn(ficheros=["src/a.py"], k=1))
    assert [n.id for n in k1.nodos] == ["a", "c", "d"]
    assert [n.id for n in solo_calls.nodos] == ["c", "a", "b"]  # sin USES_TABLE no llega a t1
    assert _nodos(tabla) == [
        ("t1", 0, None),
        ("c", 1, TipoArista.USES_TABLE),
        ("h", 1, TipoArista.USES_TABLE),
        ("b", 2, TipoArista.CALLS),
    ]
    assert fichero.semillas == ["a", "b", "c", "fa"]
    assert [(n.id, n.kind, n.distancia) for n in fichero.nodos][-1] == ("d", "symbol", 1)


def test_dependencias_ocultas_por_tabla_compartida(grafos: None) -> None:
    with _loader(TENANT_A) as loader:
        r3 = cm_queries.dependencias_ocultas(loader, ConsultaGrafoIn(nodos=["a"], k=3))
        r1 = cm_queries.dependencias_ocultas(loader, ConsultaGrafoIn(nodos=["a"], k=1))
    # a -> b -> c usa t1; h también usa t1 y no se alcanza por CALLS desde a.
    assert [(d.id, d.tablas, d.distancia) for d in r3.dependencias] == [("h", ["t1"], 2)]
    assert r3.semillas == ["a"]
    assert r1.dependencias == []  # a un salto (a, b) ninguno usa t1


def test_subgrafo_vecinos_y_resolucion(grafos: None) -> None:
    with _loader(TENANT_A) as loader:
        sub = cm_queries.subgrafo(loader, ConsultaGrafoIn(nodos=["a"], k=1))
        entrantes = cm_queries.vecinos(loader, "a", "entrante", [TipoArista.CALLS])
        salientes_c = cm_queries.vecinos(loader, "c", "saliente")
        resueltos = cm_queries.resolver_nodos(loader, ["t1", "a", "no_existe"])
    assert [n.id for n in sub.nodos] == ["a", "b", "c", "d"]
    assert [(a.src_id, a.dst_id) for a in sub.aristas] == [
        ("a", "b"),
        ("b", "c"),
        ("c", "a"),
        ("d", "a"),
    ]
    assert [(a.src_id, a.dst_id) for a in entrantes] == [("c", "a"), ("d", "a")]
    assert [(a.tipo, a.dst_id) for a in salientes_c] == [
        (TipoArista.CALLS, "a"),
        (TipoArista.USES_TABLE, "t1"),
    ]
    assert [(n.id, n.kind) for n in resueltos] == [("a", "symbol"), ("t1", "db_table")]


# ---------------------------------------------------------------------------
# Aislamiento multi-tenant
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dsn", [APP_DSN, ADMIN_DSN], ids=["con-rls", "sin-rls-superusuario"])
def test_mismos_ids_no_se_mezclan_en_ningun_nivel(grafos: None, dsn: str) -> None:
    """Con el superusuario RLS no actúa: solo el filtro explícito de cada nivel separa A de B.
    En B, b -> z -> w; si el paso recursivo no filtrara, A alcanzaría z y w (que existen en A)."""
    consulta = ConsultaGrafoIn(nodos=["a"], k=cm_impacto.K_MAX)
    with _loader(TENANT_A, dsn) as loader:
        dep_a = cm_queries.dependencias(loader, consulta)
        sub_a = cm_queries.subgrafo(loader, consulta)
        ocultas_a = cm_queries.dependencias_ocultas(loader, consulta)
    with _loader(TENANT_B, dsn) as loader:
        dep_b = cm_queries.dependencias(loader, consulta)
        ocultas_b = cm_queries.dependencias_ocultas(loader, consulta)
    assert [n.id for n in dep_a.nodos] == ["a", "b", "c", "t1"]
    assert [n.id for n in dep_b.nodos] == ["a", "b", "z", "w", "t1"]
    assert {n.id for n in sub_a.nodos}.isdisjoint({"z", "w"})
    assert ("b", "z") not in {(a.src_id, a.dst_id) for a in sub_a.aristas}
    assert [d.id for d in ocultas_a.dependencias] == ["h"]
    assert ocultas_b.dependencias == []  # en B nadie más usa t1


def test_sin_tenant_en_la_sesion_no_devuelve_nada(grafos: None) -> None:
    consulta = ConsultaGrafoIn(nodos=["a"], k=3)
    with psycopg.connect(APP_DSN) as conn:
        for nombre in ("impacto", "dependencias", "subgrafo_nodos", "dependencias_ocultas"):
            filas = conn.execute(
                cm_queries.CONSULTAS_IMPACTO[nombre],
                cm_impacto.parametros_recorrido(consulta, "ambas"),
            ).fetchall()
            assert filas == [], nombre


# ---------------------------------------------------------------------------
# Equivalencia con la implementación de referencia (200 semillas)
# ---------------------------------------------------------------------------
def _muestra(n: int, semilla: int) -> list[str]:
    simbolos = sorted(s for _, _, ss in GRAFO_SINT.ficheros for s in ss)
    return random.Random(semilla).sample(simbolos, n)


def _esperado(
    g: GrafoRef, semillas: set[str], k: int, tipos: Iterable[str], d: Direccion
) -> list[tuple[str, int, str | None]]:
    return [(n, dist, via) for n, _, dist, via in g.recorrer(semillas, k, tipos, d)]


def test_equivalencia_con_la_referencia_en_200_semillas(grafos: None) -> None:
    dependencia = [t.value for t in cm_impacto.TIPOS_DEPENDENCIA]
    with _loader(TENANT_SINT) as loader:
        for semilla in _muestra(N_SEMILLAS, 1):
            consulta = ConsultaGrafoIn(nodos=[semilla], k=3)
            for funcion, direccion in (
                (cm_queries.impacto, "entrante"),
                (cm_queries.dependencias, "saliente"),
            ):
                r = funcion(loader, consulta)
                esperado = _esperado(GRAFO_SINT, {semilla}, 3, dependencia, direccion)  # type: ignore[arg-type]
                assert not r.truncado
                assert _nodos(r) == esperado, f"{direccion} desde {semilla}"


def test_equivalencia_de_subgrafo_ocultas_y_ficheros(grafos: None) -> None:
    todos = [t.value for t in TipoArista]
    rutas = [path for _, path, _ in GRAFO_SINT.ficheros]
    with _loader(TENANT_SINT) as loader:
        for semilla in _muestra(50, 2):
            sub = cm_queries.subgrafo(loader, ConsultaGrafoIn(nodos=[semilla], k=2))
            esperado = _esperado(GRAFO_SINT, {semilla}, 2, todos, "ambas")
            assert _nodos(sub) == esperado, f"subgrafo desde {semilla}"
            ids = {n for n, _, _ in esperado}
            assert [(a.tipo, a.src_id, a.dst_id) for a in sub.aristas] == (
                GRAFO_SINT.aristas_entre(ids, todos)
            )

            ocultas = cm_queries.dependencias_ocultas(loader, ConsultaGrafoIn(nodos=[semilla]))
            assert [(d.id, d.tablas, d.distancia) for d in ocultas.dependencias] == (
                GRAFO_SINT.ocultas({semilla}, cm_impacto.K_DEFECTO)
            ), f"ocultas desde {semilla}"

        for path in random.Random(3).sample(rutas, 20):
            r = cm_queries.impacto(loader, ConsultaGrafoIn(ficheros=[path], k=2))
            semillas = GRAFO_SINT.semillas(ficheros=[path])
            dependencia = [t.value for t in cm_impacto.TIPOS_DEPENDENCIA]
            assert _nodos(r) == _esperado(GRAFO_SINT, semillas, 2, dependencia, "entrante")


# ---------------------------------------------------------------------------
# Latencia (presupuesto que bloquea el merge)
# ---------------------------------------------------------------------------
def test_latencia_p95_a_tres_saltos_bajo_presupuesto(grafos: None) -> None:
    semillas = _muestra(N_SEMILLAS, 4)
    tiempos: list[float] = []
    with _loader(TENANT_SINT) as loader:
        for s in semillas[:10]:  # calentamiento: caché de planes y de páginas
            cm_queries.impacto(loader, ConsultaGrafoIn(nodos=[s], k=3))
        for s in semillas:
            consulta = ConsultaGrafoIn(nodos=[s], k=3)
            for funcion in (cm_queries.impacto, cm_queries.dependencias):
                inicio = time.perf_counter()
                funcion(loader, consulta)
                tiempos.append(time.perf_counter() - inicio)
    p50 = statistics.median(tiempos) * 1000
    p95 = statistics.quantiles(tiempos, n=20, method="inclusive")[18] * 1000
    print(f"impacto a 3 saltos: p50={p50:.1f} ms p95={p95:.1f} ms ({len(tiempos)} consultas)")
    assert p95 < PRESUPUESTO_P95_MS, f"p95 = {p95:.1f} ms supera {PRESUPUESTO_P95_MS} ms"
