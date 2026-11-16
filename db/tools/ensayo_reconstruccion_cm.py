#!/usr/bin/env python3
"""Ensayo de reconstrucción del grafo `cm` de un tenant desde una ingesta completa (KC-DB-11-4).

Cada escenario es un grafo de uno de estos orígenes:

  * ``perfil``: los perfiles oficiales de KC-DB-11-3 (packages/core/tests/bench_cm_khop.py): ``cliente_medio``
    (17.323 nodos / 23.309 aristas, el «dataset real» del ticket: ese dump nunca existió y el perfil lo reproduce
    exactamente con un generador determinista) y ``proyeccion_200k`` (200.000 nodos / 300.000 aristas). Con
    KC_BENCH_GRAFO_DIR apuntando a un dump real (nodos.csv y aristas.csv), ``cliente_medio`` lo carga en su lugar.
  * ``repo``: una ingesta determinista REAL de un repositorio local (tree-sitter + el cargador GraphLoader).
  * ``sintetico``: un grafo pequeño o de tamaño libre generado aquí (sirve a las pruebas).

Para cada uno:

  1. Se carga el grafo de un tenant de prueba y se publica el snapshot (cm.reachability).
     -> «antes»: recuentos por kind de nodo y por tipo de arista (cm.recuentos_grafo).
  2. cm.vaciar_grafo_tenant(..., 'determinista') borra la capa determinista del tenant.  (se mide)
  3. Se reconstruye: COPY del grafo, ANALYZE por fase (cm.analizar_tablas, KC-CM-11-1) y alcanzabilidad. (se mide)
  4. Se comparan los recuentos «antes» y «después»: deben ser IDÉNTICOS por kind y por tipo de arista.
  5. Se borra todo lo del tenant de prueba.

Qué mide y qué NO:
  * Mide cargar el grafo ya extraído (COPY), ANALYZE y alcanzabilidad: lo que cuesta reconstruir `cm` en la base.
  * NO mide parsear el repositorio de 200.000 nodos (el parseo medido es el del escenario «repo»), ni la fase de
    LLM ni los embeddings (TEI): la capa semántica y los vectores se restauran del backup
    (docs/recuperacion_cm_hnsw.md). Reconstruir la capa determinista borra en cascada los embeddings de los símbolos.

Uso:  KC_DATABASE_URL_MIGRATIONS=postgresql://... python db/tools/ensayo_reconstruccion_cm.py --salida ./informe_cm
Código de salida: 0 si todos los escenarios cuadran, 1 si alguno no.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
ESCENARIOS_POR_DEFECTO: list[tuple[str, dict[str, Any]]] = [
    ("repo", {"ruta": str(RAIZ)}),
    ("perfil", {"nombre": "cliente_medio"}),
    ("perfil", {"nombre": "proyeccion_200k"}),
]


# ---------------------------------------------------------------------------------------------------------
# Grafos
# ---------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Grafo:
    etiqueta: str
    files: list[Any]
    symbols: list[Any]
    tables: list[Any]
    fks: list[dict[str, Any]]
    usage: list[dict[str, str]]
    parseo_s: float = 0.0
    perfil: Any = (
        None  # el Grafo de bench_cm_khop, si el origen es un perfil (carga masiva por COPY)
    )


def _bench() -> ModuleType:
    """El benchmark de KC-DB-11-3: de él salen los perfiles oficiales."""
    ruta = RAIZ / "packages" / "core" / "tests" / "bench_cm_khop.py"
    spec = importlib.util.spec_from_file_location("bench_cm_khop", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["bench_cm_khop"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def grafo_perfil(nombre: str) -> Grafo:
    g = _bench().grafo_de(nombre)
    etiqueta = f"perfil {nombre} ({g.origen}): {g.n_nodos} nodos / {len(g.aristas)} aristas"
    return Grafo(etiqueta, g.ficheros, g.simbolos, g.tablas, [], [], perfil=g)


def grafo_sintetico(nodos: int, aristas: int, semilla: int = 7) -> Grafo:
    """Grafo del tamaño pedido: archivos (~4 %), tablas y columnas (~1 %) y el resto símbolos con llamadas."""
    from kallicode_codemapping.ingest_repo import SourceFile
    from kallicode_codemapping.parser_treesitter import Symbol

    rng = random.Random(semilla)
    n_files = max(nodos // 25, 4)
    n_tablas = max(nodos // 1000, 3)
    cols = 8
    n_simbolos = max(nodos - n_files - n_tablas * (1 + cols), 1)
    files = [
        SourceFile(
            path=f"src/m{i // 100}/f{i}.py", language="python", sha256=f"{i:064x}", content=""
        )
        for i in range(n_files)
    ]
    tablas = [
        {
            "esquema": "public",
            "name": f"t{i}",
            "columns": [{"name": f"c{j}", "type": "text", "nullable": True} for j in range(cols)],
        }
        for i in range(n_tablas)
    ]
    fks = [
        {
            "from_schema": "public",
            "from_table": f"t{i}",
            "to_schema": "public",
            "to_table": f"t{i // 2}",
        }
        for i in range(1, n_tablas)
    ]
    usage = [
        {
            "file": files[rng.randrange(n_files)].path,
            "esquema": "public",
            "table": f"t{rng.randrange(n_tablas)}",
        }
        for _ in range(max(aristas // 20, 1))
    ]
    llamadas_por_simbolo = max((aristas - len(fks) - len(usage)) / n_simbolos, 0.0)
    symbols = []
    for i in range(n_simbolos):
        k = int(llamadas_por_simbolo) + (
            1 if rng.random() < llamadas_por_simbolo - int(llamadas_por_simbolo) else 0
        )
        symbols.append(
            Symbol(
                id=f"sym_{semilla}_{i:08d}",
                file=files[i % n_files].path,
                language="python",
                kind="function",
                name=f"fn_{i}",
                start_line=1,
                end_line=5,
                signature=f"def fn_{i}()",
                snippet="",
                calls=[f"fn_{rng.randrange(n_simbolos)}" for _ in range(k)],
            )
        )
    return Grafo(f"sintético {nodos} nodos / {aristas} aristas", files, symbols, tablas, fks, usage)


def grafo_repo(ruta: str) -> Grafo:
    """Ingesta determinista REAL de un repositorio local (archivos y símbolos con tree-sitter)."""
    from kallicode_codemapping.config import Settings
    from kallicode_codemapping.ingest_repo import parsear_fuentes_en_streaming, walk_sources

    os.environ.setdefault("CM_TENANT_ID", "ensayo")
    os.environ.setdefault("CM_REPO_URL", ruta)
    settings = Settings()
    t0 = time.perf_counter()
    files = list(walk_sources(ruta, settings))
    symbols: list[Any] = []
    for lote in parsear_fuentes_en_streaming(files, tamano_lote=250):
        symbols.extend(lote)
    return Grafo(
        f"repositorio real {Path(ruta).name}",
        files,
        symbols,
        [],
        [],
        [],
        parseo_s=time.perf_counter() - t0,
    )


# ---------------------------------------------------------------------------------------------------------
# Fases de la carga (dos caminos: el cargador GraphLoader y el COPY masivo de un perfil)
# ---------------------------------------------------------------------------------------------------------
def _copiar_perfil(admin: Any, tenant: str, g: Any, snap: str) -> None:
    """COPY del grafo de un perfil en las tablas reales (como la carga masiva de bench_cm_khop.cargar)."""
    kinds = g.kinds
    with admin.transaction(), admin.cursor() as cur:
        cur.execute(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) VALUES (%s, %s, 'ensayo', 'main')",
            (snap, tenant),
        )
        with cur.copy(
            "COPY cm.files (id, tenant_id, path, language, sha256, snapshot_id) FROM STDIN"
        ) as cp:
            for fid, path in g.ficheros:
                cp.write_row((fid, tenant, path, "python", "h", snap))
        with cur.copy(
            "COPY cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) FROM STDIN"
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
            "COPY cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, snapshot_id) FROM STDIN"
        ) as cp:
            for tipo, src, dst in g.aristas:
                cp.write_row((tenant, tipo, kinds[src], src, kinds[dst], dst, snap))


def _copia(
    grafo: Grafo, admin: Any, loader: Any, tenant: str, tag: str
) -> tuple[dict[str, float], str]:
    """Carga el grafo y devuelve (segundos por fase, id del snapshot)."""
    t: dict[str, float] = {}
    if grafo.perfil is not None:
        snap = f"snap_{tenant}_{tag}"
        t0 = time.perf_counter()
        _copiar_perfil(admin, tenant, grafo.perfil, snap)
        t["COPY del grafo"] = time.perf_counter() - t0
        return t, snap
    t0 = time.perf_counter()
    snap = loader.start_snapshot(tag * 40, "ensayo")
    file_ids = loader.load_files(snap, grafo.files)
    t["archivos"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    loader.load_symbols(grafo.symbols, file_ids)
    t["símbolos y llamadas"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    if grafo.tables:
        loader.load_schema(grafo.tables, grafo.fks, grafo.usage, file_ids)
    t["esquema"] = time.perf_counter() - t0
    loader.marcar_fase(snap, "ingesta")
    loader.complete_snapshot(snap)
    return t, snap


def _analizar(admin: Any, tablas: list[str] | None = None) -> float:
    """ANALYZE por fase con cm.analizar_tablas (KC-CM-11-1): sin estadísticas tras la carga, el planificador va a ciegas."""
    t0 = time.perf_counter()
    admin.execute("SELECT * FROM cm.analizar_tablas(%s)", (tablas,)).fetchall()
    return time.perf_counter() - t0


def _alcanzabilidad(
    grafo: Grafo, admin: Any, tenant: str, snap: str, timeout_s: int = 900
) -> tuple[tuple[Any, ...], float]:
    """Calcula cm.reachability con un tope de tiempo: si no termina, se informa «timeout» y el ensayo sigue.

    Un perfil la calcula como la carga masiva oficial (cm.publicar_snapshot, que además publica el snapshot);
    el resto, con cm.refrescar_reachability.
    """
    import psycopg

    t0 = time.perf_counter()
    try:
        with admin.transaction():
            admin.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
            admin.execute(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'")
            if grafo.perfil is not None:
                admin.execute(
                    "SELECT cm.publicar_snapshot(%s, %s, %s)",
                    (snap, grafo.perfil.n_nodos, len(grafo.perfil.aristas)),
                )
            else:
                admin.execute(
                    "SELECT estado, filas, caminos FROM cm.refrescar_reachability()"
                ).fetchone()
    except psycopg.errors.QueryCanceled:
        return ("timeout", timeout_s), time.perf_counter() - t0
    seg = time.perf_counter() - t0
    fila = admin.execute(
        "SELECT estado, filas, caminos FROM cm.reachability_estado WHERE tenant_id = %s", (tenant,)
    ).fetchone()
    return (tuple(fila) if fila else ()), seg


def _recuentos(conn: Any, tenant: str) -> dict[str, int]:
    return {
        o: int(n)
        for o, n in conn.execute(
            "SELECT objeto, filas FROM cm.recuentos_grafo(%s)", (tenant,)
        ).fetchall()
    }


def ensayar(dsn: str, grafo: Grafo) -> dict[str, Any]:
    import psycopg
    from kallicode_codemapping.graph_loader import GraphLoader

    tenant = f"org_recon_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(dsn, autocommit=True)
    informe: dict[str, Any] = {
        "escenario": grafo.etiqueta,
        "archivos": len(grafo.files),
        "simbolos": len(grafo.symbols),
        "tablas": len(grafo.tables),
        "parseo_repositorio_s": round(grafo.parseo_s, 2),
    }
    try:
        admin.execute("SET TIME ZONE 'UTC'")
        admin.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant)
        )
        with GraphLoader(dsn=dsn, tenant_id=tenant) as loader:
            # --- 1. carga inicial y «antes»
            t0 = time.perf_counter()
            _, snap = _copia(grafo, admin, loader, tenant, "a")
            _analizar(admin)
            informe["carga_inicial_s"] = round(time.perf_counter() - t0, 2)
            estado_i, t_i = _alcanzabilidad(grafo, admin, tenant, snap)
            informe["reachability_inicial"] = [list(estado_i), round(t_i, 2)]
            antes = _recuentos(admin, tenant)
            informe["antes"] = antes

            # --- 2. borrar la capa determinista del tenant
            t0 = time.perf_counter()
            borrado = admin.execute(
                "SELECT objeto, filas FROM cm.vaciar_grafo_tenant(%s, 'determinista', %s)",
                (tenant, tenant),
            ).fetchall()
            informe["vaciado_s"] = round(time.perf_counter() - t0, 2)
            informe["borrado"] = {str(o): int(n) for o, n in borrado}
            tras_vaciar = _recuentos(admin, tenant)
            informe["tras_vaciar"] = tras_vaciar
            informe["vaciado_completo"] = not any(
                k.startswith(("nodo:", "arista:", "reachability")) and v
                for k, v in tras_vaciar.items()
            )

            # --- 3. reconstruir (lo que se cronometra como «reconstrucción»)
            t_total = time.perf_counter()
            fases, snap = _copia(grafo, admin, loader, tenant, "b")
            fases["ANALYZE por fase"] = _analizar(admin)
            estado_r, t_reach = _alcanzabilidad(grafo, admin, tenant, snap)
            fases["cm.reachability"] = t_reach
            fases["ANALYZE de cm.reachability"] = _analizar(admin, ["reachability"])
            informe["reconstruccion"] = {
                "fases": {k: round(v, 2) for k, v in fases.items()},
                "reachability_estado": list(estado_r),
                "total_s": round(time.perf_counter() - t_total, 2),
            }

            # --- 4. comparar por kind y por tipo de arista
            despues = _recuentos(admin, tenant)
            informe["despues"] = despues
            diferencias = {
                k: {"antes": antes.get(k, 0), "despues": despues.get(k, 0)}
                for k in sorted(set(antes) | set(despues))
                if antes.get(k, 0) != despues.get(k, 0)
            }
            informe["diferencias"] = diferencias
            informe["reachability_vigente"] = estado_i[0] == "vigente" and estado_r[0] == "vigente"
            informe["cuadra"] = (
                not diferencias and informe["vaciado_completo"] and informe["reachability_vigente"]
            )
    finally:
        admin.close()
        with psycopg.connect(
            dsn, autocommit=True
        ) as limpio:  # conexión nueva: la del ensayo pudo cortarse
            if limpio.execute(
                "SELECT 1 FROM core.organizations WHERE id = %s", (tenant,)
            ).fetchone():
                limpio.execute(
                    "SELECT objeto FROM cm.vaciar_grafo_tenant(%s, 'completo', %s)",
                    (tenant, tenant),
                )
                limpio.execute("DELETE FROM cm.snapshot_vigente WHERE tenant_id = %s", (tenant,))
                limpio.execute("DELETE FROM cm.repo_snapshots WHERE tenant_id = %s", (tenant,))
                limpio.execute("DELETE FROM core.organizations WHERE id = %s", (tenant,))
    return informe


def markdown(informes: list[dict[str, Any]]) -> str:
    out = ["# Ensayo de reconstrucción del grafo cm (KC-DB-11-4)", ""]
    out.append(
        f"Resultado: **{'CUADRA' if all(i['cuadra'] for i in informes) else 'NO CUADRA'}** en {len(informes)} escenario(s)."
    )
    out.append("")
    out.append(
        "| Escenario | Archivos | Símbolos | Carga inicial | Vaciado | Reconstrucción total | Cuadra |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for i in informes:
        out.append(
            f"| {i['escenario']} | {i['archivos']} | {i['simbolos']} | {i['carga_inicial_s']} s | {i['vaciado_s']} s "
            f"| **{i['reconstruccion']['total_s']} s** | {'SÍ' if i['cuadra'] else 'NO'} |"
        )
    for i in informes:
        r = i["reconstruccion"]
        fases = " · ".join(f"{k} {v} s" for k, v in r["fases"].items())
        out += [
            "",
            f"## {i['escenario']}",
            "",
            f"* Fases de la reconstrucción: {fases}.",
            f"* cm.reachability (estado, filas, caminos): {r['reachability_estado']}.",
        ]
        if i["parseo_repositorio_s"]:
            out.append(
                f"* Parseo del repositorio con tree-sitter (no incluido en lo anterior): {i['parseo_repositorio_s']} s."
            )
        out += ["", "| Objeto | Antes | Después |", "|---|---|---|"]
        for k in sorted(set(i["antes"]) | set(i["despues"])):
            out.append(f"| {k} | {i['antes'].get(k, 0)} | {i['despues'].get(k, 0)} |")
        if i["diferencias"]:
            out.append("")
            out.append(f"**DIFERENCIAS:** {json.dumps(i['diferencias'])}")
    out += [
        "",
        "## Límites",
        "",
        "* Se cronometra cargar el grafo ya extraído, ANALYZE y alcanzabilidad. **No** se incluye la fase de LLM ni los embeddings (TEI).",
        "* Reconstruir la capa determinista borra en cascada los embeddings de los símbolos: hay que restaurarlos del backup o recalcularlos.",
        "* Los perfiles `cliente_medio` y `proyeccion_200k` son sintéticos calibrados (KC-DB-11-3): el dump real del cliente medio nunca existió; con KC_BENCH_GRAFO_DIR se carga uno real. NO incluyen el tiempo de parsear un repositorio de ese tamaño.",
        "* Docker local, un solo tenant y sin carga concurrente: los tiempos son del procedimiento, no una promesa de producción.",
        "",
    ]
    return "\n".join(out)


def _dsn() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        raise SystemExit("KC_DATABASE_URL_MIGRATIONS (rol administrador) no está definida")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--salida", type=Path, help="carpeta del informe (informe.md e informe.json)")
    ap.add_argument(
        "--repo", help="ruta de un repositorio real a ingerir (por defecto, este repositorio)"
    )
    ap.add_argument(
        "--perfil",
        action="append",
        choices=["cliente_medio", "proyeccion_200k"],
        help="perfil oficial de KC-DB-11-3 (repetible)",
    )
    ap.add_argument(
        "--sintetico",
        action="append",
        metavar="NODOS:ARISTAS",
        help="grafo sintético de tamaño libre (repetible)",
    )
    args = ap.parse_args(argv)
    logging.disable(
        logging.INFO
    )  # el cargador registra cada fase en JSON; el informe ya las resume

    escenarios: list[tuple[str, dict[str, Any]]]
    if args.repo or args.perfil or args.sintetico:
        escenarios = []
        if args.repo:
            escenarios.append(("repo", {"ruta": args.repo}))
        for nombre in args.perfil or []:
            escenarios.append(("perfil", {"nombre": nombre}))
        for s in args.sintetico or []:
            n, a = s.split(":")
            escenarios.append(("sintetico", {"nodos": int(n), "aristas": int(a)}))
    else:
        escenarios = ESCENARIOS_POR_DEFECTO

    dsn = _dsn()
    informes = []
    for tipo, p in escenarios:
        if tipo == "repo":
            grafo = grafo_repo(p["ruta"])
        elif tipo == "perfil":
            grafo = grafo_perfil(p["nombre"])
        else:
            grafo = grafo_sintetico(p["nodos"], p["aristas"])
        print(f">> {grafo.etiqueta}", flush=True)
        informes.append(ensayar(dsn, grafo))
    texto = markdown(informes)
    print(texto)
    if args.salida:
        args.salida.mkdir(parents=True, exist_ok=True)
        (args.salida / "informe.md").write_text(texto, encoding="utf-8")
        (args.salida / "informe.json").write_text(
            json.dumps(informes, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return 0 if all(i["cuadra"] for i in informes) else 1


if __name__ == "__main__":
    sys.exit(main())
