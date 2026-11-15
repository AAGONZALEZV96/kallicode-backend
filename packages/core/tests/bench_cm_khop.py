"""Benchmark del grafo: las ocho consultas del ADR-020 a 17.323 y a 200.000 nodos (KC-DB-11-3).

Mide las ocho consultas críticas del grafo (`kallicode_core.cm_impacto.CONSULTAS`, ADR-020) por la
ruta de producción: como kallicode_app, con RLS y el tenant en la sesión, con la SQL de
`kallicode_codemapping.cm_queries`. Para `impacto` y `dependencias` usa `cm.impacto()`, igual que
producción desde KC-CM-11-2, y mide sus dos rutas: el CTE recursivo y `cm.reachability`.

Dos perfiles, con la misma forma de grafo:

  cliente_medio    17.323 nodos y 23.309 aristas: la forma del cliente medio que cita el ticket.
                   Es sintético y calibrado a esas cifras mientras CodeMapping no entregue el dump
                   real. Con KC_BENCH_GRAFO_DIR se carga el dump real en su lugar (ver `leer_csv`).
  proyeccion_200k  200.000 nodos y 300.000 aristas: la escala declarada de un despliegue completo.

Forma: ficheros -> símbolos (DEFINES), CALLS con grado de entrada de cola pesada (Zipf, hay nodos
centrales), USES_TABLE desde los símbolos de acceso a datos, tablas -> columnas (HAS_COLUMN) y
REFERENCES entre tablas, con ciclos. Un segundo tenant con la forma del cliente medio hace de ruido,
para que el filtro por tenant y la RLS cuenten.

Umbrales (falla si no se cumplen):
- impacto y dependencias a k = 3: p95 < 120 ms en la ruta elegida. Es la recursiva si cumple; si
  no, la materializada si está vigente. La decisión queda en el informe (criterio 2 del ticket).
- Las otras seis consultas: p95 por debajo del presupuesto del contrato
  (`kallicode_core.cm_grafo.presupuesto_p95_ms`).
- El plan del CTE de impacto y de dependencias usa edges_in_ix / edges_out_ix y nunca recorre
  cm.edges con un Seq Scan.

No es un test unitario (carga unos 235.000 nodos) y por eso el archivo no se llama test_*. Lo
ejecuta el CI nocturno (.github/workflows/grafo_nocturno.yml):

    uv run pytest packages/core/tests/bench_cm_khop.py -v -s     # criterios
    uv run python packages/core/tests/bench_cm_khop.py --json docs/benchmarks/grafo_khop_v1.json
    uv run python packages/core/tests/bench_cm_khop.py --exportar <dir>   # perfil a CSV

Requiere KC_DATABASE_URL_MIGRATIONS (superusuario: siembra) y KC_DATABASE_URL (kallicode_app:
mide), con las migraciones aplicadas. Variables: KC_BENCH_KHOP_PERFILES (por defecto los dos),
KC_BENCH_KHOP_SEMILLAS (200 consultas por operación), KC_BENCH_GRAFO_DIR y KC_BENCH_RESULTADOS
(informe Markdown para el resumen del job).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest
from kallicode_codemapping.cm_queries import CONSULTAS_IMPACTO, SQL_IMPACTO_AUTO
from kallicode_core import cm_grafo, cm_impacto
from kallicode_core.cm_impacto import ConsultaGrafoIn, Direccion

pytestmark = pytest.mark.benchmark

SEMILLA = 20261115
# Presupuesto del ticket para el recorrido a tres saltos sobre 200.000 nodos.
PRESUPUESTO_KHOP_MS = 120.0
K_KHOP = 3
# El subgrafo se mide con el mismo k que el benchmark del contrato (test_contrato_consulta.py).
K_SUBGRAFO = 2
N_CONSULTAS = int(os.getenv("KC_BENCH_KHOP_SEMILLAS", "200"))
# Un 5 % de las semillas son los nodos con más dependientes: el peor caso del impacto pesa en el p95.
FRACCION_CENTRALES = 0.05
N_CALENTAMIENTO = 20
# Exponente de Zipf del grado de entrada de CALLS y USES_TABLE. Con 0,8 el nodo más llamado de la
# proyección tiene unos 1.900 llamadores y el del cliente medio unos 200.
ALFA_ZIPF = 0.8
TENANT_RUIDO = "bench_khop_ruido"
RUTAS_KHOP = ("recursiva", "materializada")
OPERACIONES = (
    "resolver_nodos",
    "semillas",
    "impacto",
    "dependencias",
    "subgrafo_nodos",
    "aristas_entre",
    "vecinos",
    "dependencias_ocultas",
)
# Nombre de cada consulta en el contrato cm-grafo-v1 (de ahí sale su presupuesto).
OPERACION_CONTRATO = {
    "resolver_nodos": "resolver_nodos",
    "semillas": "semillas",
    "impacto": "impacto",
    "dependencias": "dependencias",
    "subgrafo_nodos": "subgrafo",
    "aristas_entre": "subgrafo",
    "vecinos": "vecinos",
    "dependencias_ocultas": "dependencias_ocultas",
}
KHOP = ("impacto", "dependencias")
DIRECCION: dict[str, Direccion] = {"impacto": "entrante", "dependencias": "saliente"}


# ---------------------------------------------------------------------------
# Perfiles y generador
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Perfil:
    nombre: str
    ficheros: int
    simbolos: int
    tablas: int
    columnas: int
    calls: int
    uses_table: int
    references: int

    @property
    def nodos(self) -> int:
        return self.ficheros + self.simbolos + self.tablas + self.columnas

    @property
    def aristas(self) -> int:
        # DEFINES: una por símbolo. HAS_COLUMN: una por columna.
        return self.simbolos + self.columnas + self.calls + self.uses_table + self.references


PERFILES: dict[str, Perfil] = {
    # 17.323 nodos / 23.309 aristas: ~11,5 símbolos por fichero, ~9,4 columnas por tabla y
    # 0,40 llamadas resueltas por símbolo.
    "cliente_medio": Perfil("cliente_medio", 1_200, 13_800, 223, 2_100, 5_500, 1_600, 309),
    # 200.000 nodos / 300.000 aristas: la misma proporción de nodos, 0,59 llamadas por símbolo.
    "proyeccion_200k": Perfil(
        "proyeccion_200k", 14_000, 160_000, 2_600, 23_400, 94_000, 19_000, 3_600
    ),
}

Arista = tuple[str, str, str]  # (tipo, src_id, dst_id)


@dataclass
class Grafo:
    nombre: str
    origen: str  # "sintético calibrado" o la ruta del dump real
    ficheros: list[tuple[str, str]] = field(default_factory=list)  # (id, path)
    simbolos: list[tuple[str, str]] = field(default_factory=list)  # (id, file_id)
    tablas: list[str] = field(default_factory=list)
    columnas: list[tuple[str, str]] = field(default_factory=list)  # (id, table_id)
    aristas: list[Arista] = field(default_factory=list)

    @property
    def kinds(self) -> dict[str, str]:
        k = {f: "file" for f, _ in self.ficheros}
        k.update({s: "symbol" for s, _ in self.simbolos})
        k.update({t: "db_table" for t in self.tablas})
        k.update({c: "db_column" for c, _ in self.columnas})
        return k

    @property
    def n_nodos(self) -> int:
        return len(self.ficheros) + len(self.simbolos) + len(self.tablas) + len(self.columnas)


def _zipf(rnd: random.Random, ids: list[str]) -> Callable[[int], list[str]]:
    """Muestreador con pesos 1/rango^ALFA sobre una permutación aleatoria de `ids`."""
    orden = ids[:]
    rnd.shuffle(orden)
    acumulado: list[float] = []
    total = 0.0
    for r in range(len(orden)):
        total += 1.0 / (r + 1) ** ALFA_ZIPF
        acumulado.append(total)
    return lambda n: rnd.choices(orden, cum_weights=acumulado, k=n)


def _distintas(
    objetivo: int,
    tipo: str,
    origenes: Callable[[int], list[str]],
    destinos: Callable[[int], list[str]],
) -> set[Arista]:
    """`objetivo` aristas distintas (la PK de cm.edges no admite repetidas)."""
    aristas: set[Arista] = set()
    while len(aristas) < objetivo:
        falta = objetivo - len(aristas)
        lote = max(falta * 2, 64)
        for src, dst in zip(origenes(lote), destinos(lote), strict=True):
            # La autollamada (recursión) existe; una tabla que se referencia a sí misma, no.
            if src != dst or tipo == "CALLS":
                aristas.add((tipo, src, dst))
                if len(aristas) == objetivo:
                    break
    return aristas


def generar(perfil: Perfil, semilla: int = SEMILLA) -> Grafo:
    """Grafo determinista con exactamente `perfil.nodos` nodos y `perfil.aristas` aristas."""
    rnd = random.Random(semilla)
    g = Grafo(perfil.nombre, "sintético calibrado")
    g.ficheros = [(f"f{i:06d}", f"src/m{i % 40:02d}/f{i:06d}.py") for i in range(perfil.ficheros)]
    g.simbolos = [
        (f"s{i:07d}", g.ficheros[i * perfil.ficheros // perfil.simbolos][0])
        for i in range(perfil.simbolos)
    ]
    g.tablas = [f"t{i:05d}" for i in range(perfil.tablas)]
    g.columnas = [
        (f"c{i:06d}", g.tablas[i * perfil.tablas // perfil.columnas])
        for i in range(perfil.columnas)
    ]
    ids_simbolos = [s for s, _ in g.simbolos]
    # Un 30 % de los símbolos accede a datos: de ellos salen los USES_TABLE.
    acceso_datos = rnd.sample(ids_simbolos, max(1, int(len(ids_simbolos) * 0.3)))

    aristas: set[Arista] = {("DEFINES", f, s) for s, f in g.simbolos}
    aristas |= {("HAS_COLUMN", t, c) for c, t in g.columnas}
    aristas |= _distintas(
        perfil.calls,
        "CALLS",
        lambda n: rnd.choices(ids_simbolos, k=n),
        _zipf(rnd, ids_simbolos),
    )
    aristas |= _distintas(
        perfil.uses_table,
        "USES_TABLE",
        lambda n: rnd.choices(acceso_datos, k=n),
        _zipf(rnd, g.tablas),
    )
    aristas |= _distintas(
        perfil.references,
        "REFERENCES",
        lambda n: rnd.choices(g.tablas, k=n),
        lambda n: rnd.choices(g.tablas, k=n),
    )
    g.aristas = sorted(aristas)
    return g


# ---------------------------------------------------------------------------
# Dump real: nodos.csv (id,kind) y aristas.csv (tipo,src_id,dst_id)
# ---------------------------------------------------------------------------
def exportar_csv(g: Grafo, directorio: Path) -> None:
    directorio.mkdir(parents=True, exist_ok=True)
    with (directorio / "nodos.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "kind"])
        w.writerows(sorted(g.kinds.items()))
    with (directorio / "aristas.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tipo", "src_id", "dst_id"])
        w.writerows(g.aristas)


def leer_csv(directorio: Path) -> Grafo:
    """Grafo desde un dump de CodeMapping. Solo admite la capa estructural.

    Los símbolos cuelgan del fichero que los define (DEFINES) y las columnas de su tabla
    (HAS_COLUMN). Si un símbolo no tiene DEFINES o una columna no tiene HAS_COLUMN, el dump está
    incompleto y la carga falla: no se inventa el padre.
    """
    with (directorio / "nodos.csv").open(encoding="utf-8") as fh:
        kinds = {f["id"]: f["kind"] for f in csv.DictReader(fh)}
    with (directorio / "aristas.csv").open(encoding="utf-8") as fh:
        aristas = sorted({(f["tipo"], f["src_id"], f["dst_id"]) for f in csv.DictReader(fh)})
    no_admitidos = Counter(
        k for k in kinds.values() if k not in ("file", "symbol", "db_table", "db_column")
    )
    if no_admitidos:
        raise ValueError(f"kinds fuera de la capa estructural: {dict(no_admitidos)}")
    padre = {dst: src for tipo, src, dst in aristas if tipo in ("DEFINES", "HAS_COLUMN")}
    sin_padre = [i for i, k in kinds.items() if k in ("symbol", "db_column") and i not in padre]
    if sin_padre:
        raise ValueError(
            f"{len(sin_padre)} símbolos o columnas sin DEFINES/HAS_COLUMN: {sin_padre[:5]}"
        )
    g = Grafo("dataset_real", str(directorio))
    g.ficheros = sorted((i, f"dump/{i}") for i, k in kinds.items() if k == "file")
    g.simbolos = sorted((i, padre[i]) for i, k in kinds.items() if k == "symbol")
    g.tablas = sorted(i for i, k in kinds.items() if k == "db_table")
    g.columnas = sorted((i, padre[i]) for i, k in kinds.items() if k == "db_column")
    g.aristas = aristas
    return g


def grafo_de(perfil: str) -> Grafo:
    directorio = os.getenv("KC_BENCH_GRAFO_DIR")
    if perfil == "cliente_medio" and directorio:
        return leer_csv(Path(directorio))
    return generar(PERFILES[perfil])


# ---------------------------------------------------------------------------
# Conexiones, carga y limpieza
# ---------------------------------------------------------------------------
def _dsn(variable: str) -> str:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no está definida")
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


_TABLAS_TENANT = (
    "cm.reachability",
    "cm.reachability_estado",
    "cm.edges",
    "cm.symbols",
    "cm.db_columns",
    "cm.db_tables",
    "cm.files",
    "cm.nodes",
    "cm.snapshot_vigente",
    "cm.repo_snapshots",
)


def limpiar(admin: psycopg.Connection[Any], tenant: str) -> None:
    for tabla in _TABLAS_TENANT:
        admin.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (tenant,))
    admin.execute("DELETE FROM core.organizations WHERE id = %s", (tenant,))


@dataclass
class Carga:
    perfil: str
    origen: str
    tenant: str
    nodos: int
    aristas: int
    aristas_por_tipo: dict[str, int]
    max_grado_entrada: int
    carga_s: float
    publicar_s: float
    reachability_estado: str
    reachability_filas: int
    reachability_caminos: int
    reachability_presupuesto: int
    reachability_ms: int


def cargar(admin: psycopg.Connection[Any], tenant: str, g: Grafo) -> Carga:
    """COPY del grafo en las tablas reales y publicación del snapshot (refresca cm.reachability)."""
    limpiar(admin, tenant)
    snap = f"snap_{tenant}"
    kinds = g.kinds
    inicio = time.perf_counter()
    with admin.transaction(), admin.cursor() as cur:
        cur.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
        cur.execute(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
            "VALUES (%s, %s, 'bench', 'main')",
            (snap, tenant),
        )
        with cur.copy(
            "COPY cm.files (id, tenant_id, path, language, sha256, snapshot_id) FROM STDIN"
        ) as cp:
            for fid, path in g.ficheros:
                cp.write_row((fid, tenant, path, "python", "h", snap))
        with cur.copy(
            "COPY cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) FROM STDIN"
        ) as cp:
            for i, (sid, fid) in enumerate(g.simbolos):
                cp.write_row((sid, tenant, fid, "function", sid, i + 1, i + 1, "def"))
        with cur.copy(
            "COPY cm.db_tables (id, tenant_id, esquema, nombre, snapshot_id) FROM STDIN"
        ) as cp:
            for tid in g.tablas:
                cp.write_row((tid, tenant, "public", tid, snap))
        with cur.copy(
            "COPY cm.db_columns (id, tenant_id, table_id, nombre, tipo, nullable) FROM STDIN"
        ) as cp:
            for cid, tid in g.columnas:
                cp.write_row((cid, tenant, tid, cid, "text", True))
        with cur.copy(
            "COPY cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, snapshot_id) "
            "FROM STDIN"
        ) as cp:
            for tipo, src, dst in g.aristas:
                cp.write_row((tenant, tipo, kinds[src], src, kinds[dst], dst, snap))
    admin.execute("ANALYZE cm.nodes")
    admin.execute("ANALYZE cm.edges")
    carga_s = time.perf_counter() - inicio

    inicio = time.perf_counter()
    with admin.transaction():
        admin.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        admin.execute("SELECT cm.publicar_snapshot(%s, %s, %s)", (snap, g.n_nodos, len(g.aristas)))
    publicar_s = time.perf_counter() - inicio
    admin.execute("ANALYZE cm.reachability")
    estado = admin.execute(
        "SELECT estado, filas, caminos, presupuesto, duracion_ms FROM cm.reachability_estado "
        "WHERE tenant_id = %s",
        (tenant,),
    ).fetchone()
    assert estado is not None, "publicar_snapshot no dejó estado de cm.reachability"
    entrada = Counter(dst for tipo, _, dst in g.aristas if tipo in cm_impacto.TIPOS_DEPENDENCIA)
    return Carga(
        perfil=g.nombre,
        origen=g.origen,
        tenant=tenant,
        nodos=g.n_nodos,
        aristas=len(g.aristas),
        aristas_por_tipo=dict(sorted(Counter(t for t, _, _ in g.aristas).items())),
        max_grado_entrada=max(entrada.values()),
        carga_s=round(carga_s, 2),
        publicar_s=round(publicar_s, 2),
        reachability_estado=estado[0],
        reachability_filas=estado[1],
        reachability_caminos=estado[2],
        reachability_presupuesto=estado[3],
        reachability_ms=estado[4],
    )


# ---------------------------------------------------------------------------
# Medición
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Semilla:
    nodo: str
    fichero: str  # path del fichero del nodo (o de uno cualquiera si el nodo no es un símbolo)
    estrato: str  # "azar" o "central"


def semillas(g: Grafo, n: int = N_CONSULTAS, semilla: int = SEMILLA) -> list[Semilla]:
    """Nodos de partida: un 95 % al azar entre símbolos, tablas y columnas, y un 5 % entre los
    de mayor grado de entrada por aristas de dependencia (los nodos centrales)."""
    rnd = random.Random(semilla + 1)
    path = dict(g.ficheros)
    fichero_de = {s: path[f] for s, f in g.simbolos}
    entrada = Counter(dst for tipo, _, dst in g.aristas if tipo in cm_impacto.TIPOS_DEPENDENCIA)
    n_centrales = max(1, round(n * FRACCION_CENTRALES))
    centrales = [nodo for nodo, _ in entrada.most_common(n_centrales)]
    candidatos = [s for s, _ in g.simbolos] + g.tablas + [c for c, _ in g.columnas]
    azar = rnd.sample([c for c in candidatos if c not in set(centrales)], n - n_centrales)
    cualquiera = g.ficheros[0][1]
    return [
        Semilla(x, fichero_de.get(x, cualquiera), estrato)
        for estrato, lista in (("azar", azar), ("central", centrales))
        for x in lista
    ]


def _parametros(operacion: str, s: Semilla, ruta: str, ids_subgrafo: list[str]) -> dict[str, Any]:
    if operacion == "resolver_nodos":
        return {"nodos": [s.nodo, "no_existe"]}
    if operacion == "semillas":
        return {"nodos": [s.nodo], "ficheros": [s.fichero]}
    if operacion in KHOP:
        p = cm_impacto.parametros_recorrido(
            ConsultaGrafoIn(nodos=[s.nodo], k=K_KHOP), DIRECCION[operacion]
        )
        return {
            "nodos": p["nodos"],
            "ficheros": p["ficheros"],
            "k": p["k"],
            "tipos": p["tipos"],
            "direccion": DIRECCION[operacion],
            "max_visitados": cm_impacto.MAX_NODOS,
            "ruta": ruta,
        }
    if operacion == "subgrafo_nodos":
        return cm_impacto.parametros_recorrido(
            ConsultaGrafoIn(nodos=[s.nodo], k=K_SUBGRAFO), "ambas"
        )
    if operacion == "aristas_entre":
        return {
            "ids": ids_subgrafo,
            "tipos": [t.value for t in sorted(cm_impacto.TipoArista)],
            "limite": cm_impacto.MAX_ARISTAS + 1,
        }
    if operacion == "vecinos":
        return cm_impacto.parametros_vecinos(s.nodo, "ambas")
    if operacion == "dependencias_ocultas":
        return cm_impacto.parametros_recorrido(
            ConsultaGrafoIn(nodos=[s.nodo], k=K_KHOP), "saliente"
        )
    raise ValueError(operacion)


def _sql(operacion: str) -> str:
    return SQL_IMPACTO_AUTO if operacion in KHOP else CONSULTAS_IMPACTO[operacion]


def _ejecutar(
    app: psycopg.Connection[Any], tenant: str, sql: str, params: dict[str, Any]
) -> tuple[float, list[tuple[Any, ...]] | None]:
    """Una consulta en su transacción, como la hace cm_queries: tenant y statement_timeout locales.

    Si la consulta agota el statement_timeout de producción (TIEMPO_MAXIMO_MS), devuelve ese tiempo y
    `None`: en producción esa llamada falla, y aquí cuenta como una medición en el tope.
    """
    try:
        with app.transaction(), app.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
            cur.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(cm_impacto.TIEMPO_MAXIMO_MS),),
            )
            inicio = time.perf_counter()
            cur.execute(sql, params)
            filas = cur.fetchall()
            return (time.perf_counter() - inicio) * 1000, filas
    except psycopg.errors.QueryCanceled:
        return float(cm_impacto.TIEMPO_MAXIMO_MS), None


def _percentil(valores: list[float], p: int) -> float:
    if len(valores) == 1:
        return valores[0]
    return statistics.quantiles(valores, n=100, method="inclusive")[p - 1]


@dataclass
class Medicion:
    perfil: str
    operacion: str
    ruta: str  # "recursiva" / "materializada" para k-hop; "-" para el resto
    n: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    p95_azar_ms: float
    p95_central_ms: float
    filas_medias: float
    truncados: int
    timeouts: int  # llamadas que agotaron el statement_timeout de producción (fallan)
    presupuesto_ms: float

    @property
    def cumple(self) -> bool:
        # Una llamada que agota el statement_timeout falla en producción: no cumple aunque el p95
        # quede por debajo del presupuesto.
        return self.p95_ms < self.presupuesto_ms and self.timeouts == 0


def medir(
    app: psycopg.Connection[Any], tenant: str, perfil: str, g: Grafo, estado_reach: str
) -> list[Medicion]:
    lista = semillas(g)
    resultados: list[Medicion] = []
    # aristas_entre parte de los nodos del subgrafo de cada semilla (como cm_queries.subgrafo).
    ids_subgrafo: dict[str, list[str]] = {}
    for s in lista:
        _, filas = _ejecutar(
            app, tenant, _sql("subgrafo_nodos"), _parametros("subgrafo_nodos", s, "-", [])
        )
        ids_subgrafo[s.nodo] = [str(f[0]) for f in (filas or [])[: cm_impacto.MAX_NODOS]]

    for operacion in OPERACIONES:
        rutas: tuple[str, ...] = ("-",)
        if operacion in KHOP:
            rutas = RUTAS_KHOP if estado_reach == "vigente" else ("recursiva",)
        for ruta in rutas:
            sql = _sql(operacion)
            for s in lista[:N_CALENTAMIENTO]:
                _ejecutar(app, tenant, sql, _parametros(operacion, s, ruta, ids_subgrafo[s.nodo]))
            tiempos: dict[str, list[float]] = {"azar": [], "central": []}
            filas_total = 0
            truncados = 0
            timeouts = 0
            for s in lista:
                ms, filas = _ejecutar(
                    app, tenant, sql, _parametros(operacion, s, ruta, ids_subgrafo[s.nodo])
                )
                tiempos[s.estrato].append(ms)
                if filas is None:
                    timeouts += 1
                    continue
                filas_total += len(filas)
                if operacion in KHOP:
                    assert all(f[4] == ruta for f in filas), f"{operacion} no usó la ruta {ruta}"
                    truncados += int(bool(filas) and bool(filas[0][5]))
            todos = tiempos["azar"] + tiempos["central"]
            presupuesto = (
                PRESUPUESTO_KHOP_MS
                if operacion in KHOP
                else float(cm_grafo.presupuesto_p95_ms(OPERACION_CONTRATO[operacion]))
            )
            resultados.append(
                Medicion(
                    perfil=perfil,
                    operacion=operacion,
                    ruta=ruta,
                    n=len(todos),
                    p50_ms=round(statistics.median(todos), 2),
                    p95_ms=round(_percentil(todos, 95), 2),
                    p99_ms=round(_percentil(todos, 99), 2),
                    max_ms=round(max(todos), 2),
                    p95_azar_ms=round(_percentil(tiempos["azar"], 95), 2),
                    p95_central_ms=round(_percentil(tiempos["central"], 95), 2),
                    filas_medias=round(filas_total / len(todos), 1),
                    truncados=truncados,
                    timeouts=timeouts,
                    presupuesto_ms=presupuesto,
                )
            )
    return resultados


def ruta_elegida(mediciones: list[Medicion], operacion: str) -> Medicion | None:
    """Criterio 2: la recursiva si cumple; si no, la materializada si cumple; si no, ninguna."""
    por_ruta = {m.ruta: m for m in mediciones if m.operacion == operacion}
    for ruta in RUTAS_KHOP:
        if ruta in por_ruta and por_ruta[ruta].cumple:
            return por_ruta[ruta]
    return None


def _nodos_plan(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for hijo in plan.get("Plans", []):
        yield from _nodos_plan(hijo)


def plan_khop(app: psycopg.Connection[Any], tenant: str, operacion: str, nodo: str) -> list[str]:
    """Índices de cm.edges que usa el CTE de `operacion` y los Seq Scan sobre cm.edges."""
    params = cm_impacto.parametros_recorrido(
        ConsultaGrafoIn(nodos=[nodo], k=K_KHOP), DIRECCION[operacion]
    )
    with app.transaction(), app.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        cur.execute("EXPLAIN (FORMAT JSON) " + CONSULTAS_IMPACTO[operacion], params)
        fila = cur.fetchone()
    assert fila is not None
    plan = fila[0][0]["Plan"]
    hallados: list[str] = []
    for n in _nodos_plan(plan):
        if n.get("Relation Name") == "edges":
            hallados.append(n.get("Index Name") or n["Node Type"])
    return hallados


# ---------------------------------------------------------------------------
# Ejecución completa e informe
# ---------------------------------------------------------------------------
@dataclass
class Informe:
    fecha: str
    entorno: dict[str, str]
    cargas: list[Carga] = field(default_factory=list)
    mediciones: list[Medicion] = field(default_factory=list)
    planes: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def decision(self, perfil: str) -> dict[str, str]:
        ms = [m for m in self.mediciones if m.perfil == perfil]
        salida = {}
        for op in KHOP:
            elegida = ruta_elegida(ms, op)
            salida[op] = elegida.ruta if elegida else "ninguna cumple"
        return salida

    def fallos(self) -> list[str]:
        problemas: list[str] = []
        for c in self.cargas:
            for op in KHOP:
                if self.decision(c.perfil)[op] == "ninguna cumple":
                    problemas.append(
                        f"{c.perfil}/{op}: ninguna ruta tiene p95 < {PRESUPUESTO_KHOP_MS:.0f} ms"
                    )
        for m in self.mediciones:
            if m.operacion not in KHOP and not m.cumple:
                problemas.append(
                    f"{m.perfil}/{m.operacion}: p95 = {m.p95_ms} ms supera {m.presupuesto_ms:.0f} ms"
                    f" o hay {m.timeouts} timeouts de {m.n}"
                )
        for perfil, planes in self.planes.items():
            for op, nodos in planes.items():
                indice = "edges_in_ix" if op == "impacto" else "edges_out_ix"
                if indice not in nodos or any("Seq Scan" in x for x in nodos):
                    problemas.append(f"{perfil}/{op}: el plan del CTE no usa {indice}: {nodos}")
        return problemas

    def markdown(self) -> str:
        lineas = [
            f"### Benchmark del grafo (KC-DB-11-3) · {self.fecha}",
            "",
            f"PostgreSQL {self.entorno['postgres']} · {self.entorno['maquina']} · "
            f"{N_CONSULTAS} consultas por operación ({FRACCION_CENTRALES:.0%} nodos centrales) · "
            f"k = {K_KHOP} (subgrafo k = {K_SUBGRAFO})",
            "",
            "| Perfil | Origen | Nodos | Aristas | Máx. grado de entrada | Carga | Publicar + "
            "reachability | cm.reachability |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
        for c in self.cargas:
            lineas.append(
                f"| {c.perfil} | {c.origen} | {c.nodos:,} | {c.aristas:,} | {c.max_grado_entrada:,} "
                f"| {c.carga_s} s | {c.publicar_s} s | {c.reachability_estado}: "
                f"{c.reachability_filas:,} filas, {c.reachability_caminos:,} caminos "
                f"(presupuesto {c.reachability_presupuesto:,}) |"
            )
        lineas += [
            "",
            "| Perfil | Consulta | Ruta | p50 | p95 | p99 | máx. | p95 azar | p95 centrales | "
            "Filas medias | Truncados | Timeouts | Presupuesto | Cumple |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for m in self.mediciones:
            lineas.append(
                f"| {m.perfil} | {m.operacion} | {m.ruta} | {m.p50_ms} | {m.p95_ms} | {m.p99_ms} "
                f"| {m.max_ms} | {m.p95_azar_ms} | {m.p95_central_ms} | {m.filas_medias} "
                f"| {m.truncados} | {m.timeouts} | {m.presupuesto_ms:.0f} "
                f"| {'sí' if m.cumple else '**no**'} |"
            )
        lineas += ["", "Ruta elegida a tres saltos (criterio 2):", ""]
        for c in self.cargas:
            d = self.decision(c.perfil)
            lineas.append(
                f"- {c.perfil}: impacto → {d['impacto']}, dependencias → {d['dependencias']}"
            )
        fallos = self.fallos()
        lineas += ["", "Resultado: " + ("**FALLA**" if fallos else "cumple todos los umbrales")]
        lineas += [f"- {f}" for f in fallos]
        return "\n".join(lineas) + "\n"

    def a_json(self) -> dict[str, Any]:
        return {
            "ticket": "KC-DB-11-3",
            "fecha": self.fecha,
            "entorno": self.entorno,
            "parametros": {
                "consultas_por_operacion": N_CONSULTAS,
                "fraccion_centrales": FRACCION_CENTRALES,
                "k": K_KHOP,
                "k_subgrafo": K_SUBGRAFO,
                "alfa_zipf": ALFA_ZIPF,
                "presupuesto_khop_ms": PRESUPUESTO_KHOP_MS,
                "semilla": SEMILLA,
            },
            "cargas": [asdict(c) for c in self.cargas],
            "mediciones": [asdict(m) | {"cumple": m.cumple} for m in self.mediciones],
            "planes": self.planes,
            "decision": {c.perfil: self.decision(c.perfil) for c in self.cargas},
            "fallos": self.fallos(),
        }


def perfiles_pedidos() -> list[str]:
    pedidos = os.getenv("KC_BENCH_KHOP_PERFILES", ",".join(PERFILES))
    return [p.strip() for p in pedidos.split(",") if p.strip()]


def ejecutar(perfiles: list[str]) -> Informe:
    admin_dsn = _dsn("KC_DATABASE_URL_MIGRATIONS")
    app_dsn = _dsn("KC_DATABASE_URL")
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        version = admin.execute("SHOW server_version").fetchone()
        informe = Informe(
            fecha=time.strftime("%Y-%m-%d"),
            entorno={
                "postgres": str(version[0] if version else "?"),
                "maquina": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPU",
            },
        )
        tenants = [f"bench_khop_{p}" for p in perfiles]
        try:
            # Ruido: otro tenant con la forma del cliente medio en las mismas tablas. Basta para que
            # el filtro por tenant cuente sin duplicar el disco de la proyección.
            cargar(admin, TENANT_RUIDO, generar(PERFILES["cliente_medio"], SEMILLA + 7))
            with psycopg.connect(app_dsn, autocommit=True) as app:
                for perfil, tenant in zip(perfiles, tenants, strict=True):
                    g = grafo_de(perfil)
                    carga = cargar(admin, tenant, g)
                    carga.perfil = perfil
                    informe.cargas.append(carga)
                    central = semillas(g)[-1].nodo
                    informe.planes[perfil] = {
                        op: plan_khop(app, tenant, op, central) for op in KHOP
                    }
                    informe.mediciones += medir(app, tenant, perfil, g, carga.reachability_estado)
        finally:
            for t in [*tenants, TENANT_RUIDO]:
                limpiar(admin, t)
    return informe


# ---------------------------------------------------------------------------
# Criterios (pytest)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def informe() -> Informe:
    resultado = ejecutar(perfiles_pedidos())
    texto = resultado.markdown()
    destino = os.getenv("KC_BENCH_RESULTADOS")
    if destino:
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
        Path(destino).write_text(texto, encoding="utf-8")
        Path(destino).with_suffix(".json").write_text(
            json.dumps(resultado.a_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    # Después de escribir el archivo, y tolerante a una consola que no sea UTF-8 (Windows, cp1252):
    # imprimir no puede hacer perder el resultado.
    try:
        print("\n" + texto)
    except UnicodeEncodeError:
        print("\n" + texto.encode("ascii", "replace").decode("ascii"))
    return resultado


def test_los_perfiles_tienen_el_tamano_declarado() -> None:
    """El generador produce exactamente los nodos y aristas de cada perfil (sin base de datos)."""
    for perfil in PERFILES.values():
        g = generar(perfil)
        assert (g.n_nodos, len(g.aristas)) == (perfil.nodos, perfil.aristas), perfil.nombre
        assert len(set(g.kinds)) == g.n_nodos, "ids repetidos entre kinds"
    assert (PERFILES["cliente_medio"].nodos, PERFILES["cliente_medio"].aristas) == (17_323, 23_309)
    assert (PERFILES["proyeccion_200k"].nodos, PERFILES["proyeccion_200k"].aristas) == (
        200_000,
        300_000,
    )


def test_el_dump_csv_reproduce_el_grafo(tmp_path: Path) -> None:
    """El formato del dump real ida y vuelta: exportar_csv -> leer_csv conserva nodos y aristas."""
    g = generar(PERFILES["cliente_medio"])
    exportar_csv(g, tmp_path)
    leido = leer_csv(tmp_path)
    assert leido.kinds == g.kinds
    assert leido.aristas == g.aristas
    assert sorted(leido.simbolos) == sorted(g.simbolos)


def test_la_carga_publica_el_snapshot(informe: Informe) -> None:
    for c in informe.cargas:
        assert c.reachability_estado in ("vigente", "excedida"), c


def test_el_plan_del_recorrido_usa_los_indices_de_edges(informe: Informe) -> None:
    fallos = [f for f in informe.fallos() if "el plan" in f]
    assert not fallos, fallos


def test_p95_a_tres_saltos_bajo_120_ms(informe: Informe) -> None:
    """Criterio 2: impacto y dependencias a k = 3 con p95 < 120 ms en la ruta elegida."""
    fallos = [f for f in informe.fallos() if "ninguna ruta" in f]
    assert not fallos, fallos


def test_p95_de_las_demas_consultas_bajo_el_presupuesto_del_contrato(informe: Informe) -> None:
    fallos = [f for f in informe.fallos() if "supera" in f]
    assert not fallos, fallos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", type=Path, help="escribe el resultado en JSON")
    parser.add_argument(
        "--exportar", type=Path, help="exporta el perfil cliente_medio a CSV y sale"
    )
    args = parser.parse_args()
    if args.exportar:
        exportar_csv(generar(PERFILES["cliente_medio"]), args.exportar)
        return
    resultado = ejecutar(perfiles_pedidos())
    print(resultado.markdown())
    if args.json:
        args.json.write_text(
            json.dumps(resultado.a_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
