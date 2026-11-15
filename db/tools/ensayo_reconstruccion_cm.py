#!/usr/bin/env python3
"""Ensayo de reconstrucción del grafo `cm` de un tenant desde una ingesta completa (KC-DB-11-4).

Para cada escenario (un repositorio REAL o un grafo SINTÉTICO de un tamaño dado):

  1. Se carga el grafo de un tenant de prueba con el cargador real (GraphLoader: COPY por fase) y se calcula
     cm.reachability.  -> «antes»: recuentos por kind de nodo y por tipo de arista (cm.recuentos_grafo).
  2. cm.vaciar_grafo_tenant(..., 'determinista') borra la capa determinista del tenant.  (se mide)
  3. Se reconstruye: nueva instantánea, archivos, símbolos, esquema, cm.refrescar_reachability y ANALYZE. (se mide)
  4. Se comparan los recuentos «antes» y «después»: deben ser IDÉNTICOS por kind y por tipo de arista.
  5. Se borra todo lo del tenant de prueba.

Qué mide y qué NO:
  * Mide cargar el grafo ya extraído (COPY), alcanzabilidad y ANALYZE: lo que cuesta reconstruir `cm` en la base.
  * El tiempo de parsear un repositorio real (tree-sitter) se mide aparte, en el escenario «repo».
  * NO mide la fase de LLM ni los embeddings (TEI): la capa semántica y los vectores se restauran del backup
    (docs/recuperacion_cm_hnsw.md). Reconstruir la capa determinista borra en cascada los embeddings de los símbolos.
  * Los escenarios sintéticos no son el dataset real de CodeMapping: son grafos del mismo tamaño y forma aproximada.

Uso:  KC_DATABASE_URL_MIGRATIONS=postgresql://... python db/tools/ensayo_reconstruccion_cm.py --salida ./informe_cm
Código de salida: 0 si todos los escenarios cuadran, 1 si alguno no.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
ESCENARIOS_POR_DEFECTO = [
    ("repo", {"ruta": str(RAIZ)}),
    ("sintetico", {"nodos": 17_323, "aristas": 23_309}),
    ("sintetico", {"nodos": 200_000, "aristas": 300_000}),
]


# ---------------------------------------------------------------------------------------------------------
# Grafos
# ---------------------------------------------------------------------------------------------------------
@dataclasses.dataclass
class Grafo:
    etiqueta: str
    files: list[Any]
    symbols: list[Any]
    tables: list[dict[str, Any]]
    fks: list[dict[str, Any]]
    usage: list[dict[str, str]]
    parseo_s: float = 0.0


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
# Ensayo de un escenario
# ---------------------------------------------------------------------------------------------------------
def _cargar(loader: Any, grafo: Grafo, commit: str) -> dict[str, float]:
    t: dict[str, float] = {}
    t0 = time.perf_counter()
    snap = loader.start_snapshot(commit, "ensayo")
    file_ids = loader.load_files(snap, grafo.files)
    t["archivos_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    loader.load_symbols(grafo.symbols, file_ids)
    t["simbolos_y_llamadas_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    if grafo.tables:
        loader.load_schema(grafo.tables, grafo.fks, grafo.usage, file_ids)
    t["esquema_s"] = time.perf_counter() - t0
    loader.marcar_fase(snap, "ingesta")
    loader.complete_snapshot(snap)
    return t


def _analyze(conn: Any) -> float:
    """ANALYZE de las tablas del grafo tras una carga masiva (devuelve los segundos que tarda)."""
    t0 = time.perf_counter()
    for tabla in (
        "cm.files",
        "cm.symbols",
        "cm.db_tables",
        "cm.db_columns",
        "cm.nodes",
        "cm.edges",
    ):
        conn.execute(f"ANALYZE {tabla}")
    return time.perf_counter() - t0


def _reachability(conn: Any, tenant: str, timeout_s: int = 900) -> tuple[tuple[Any, ...], float]:
    """Refresca cm.reachability con un tope de tiempo: si no termina, se informa «timeout» y el ensayo sigue."""
    import psycopg

    t0 = time.perf_counter()
    try:
        with conn.transaction():
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
            conn.execute(f"SET LOCAL statement_timeout = '{int(timeout_s)}s'")
            fila = conn.execute(
                "SELECT estado, filas, caminos FROM cm.refrescar_reachability()"
            ).fetchone()
    except psycopg.errors.QueryCanceled:
        return ("timeout", timeout_s), time.perf_counter() - t0
    return tuple(fila) if fila else (), time.perf_counter() - t0


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
            t0 = time.perf_counter()
            _cargar(loader, grafo, "a" * 40)
            _analyze(admin)
            informe["carga_inicial_s"] = round(time.perf_counter() - t0, 2)
            estado_i, t_i = _reachability(admin, tenant)
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
            fases = _cargar(loader, grafo, "b" * 40)
            # ANALYZE ANTES de la alcanzabilidad: sin estadísticas de las tablas recién cargadas el planificador elige
            # planes malos y cm.refrescar_reachability() puede tardar minutos en vez de segundos.
            t_analyze = _analyze(admin)
            estado_r, t_reach = _reachability(admin, tenant)
            t0 = time.perf_counter()
            admin.execute("ANALYZE cm.reachability")
            t_analyze += time.perf_counter() - t0
            informe["reconstruccion"] = {
                **{k: round(v, 2) for k, v in fases.items()},
                "reachability_s": round(t_reach, 2),
                "reachability_estado": list(estado_r),
                "analyze_s": round(t_analyze, 2),
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
        out += [
            "",
            f"## {i['escenario']}",
            "",
            f"* Fases de la reconstrucción: archivos {r['archivos_s']} s · símbolos y llamadas {r['simbolos_y_llamadas_s']} s · "
            f"esquema {r['esquema_s']} s · cm.reachability {r['reachability_s']} s (estado, filas, caminos: {r['reachability_estado']}) · ANALYZE {r['analyze_s']} s.",
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
        "* Se cronometra cargar el grafo ya extraído, la alcanzabilidad y ANALYZE. **No** se incluye la fase de LLM ni los embeddings (TEI).",
        "* Reconstruir la capa determinista borra en cascada los embeddings de los símbolos: hay que restaurarlos del backup o recalcularlos.",
        "* Los escenarios sintéticos NO son el dataset real de CodeMapping (17.323 nodos y 23.309 aristas): son grafos del mismo tamaño y forma aproximada.",
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
        "--sintetico",
        action="append",
        metavar="NODOS:ARISTAS",
        help="escenario sintético (repetible)",
    )
    args = ap.parse_args(argv)
    logging.disable(
        logging.INFO
    )  # el cargador registra cada fase en JSON; el informe ya las resume

    escenarios: list[tuple[str, dict[str, Any]]]
    if args.repo or args.sintetico:
        escenarios = []
        if args.repo:
            escenarios.append(("repo", {"ruta": args.repo}))
        for s in args.sintetico or []:
            n, a = s.split(":")
            escenarios.append(("sintetico", {"nodos": int(n), "aristas": int(a)}))
    else:
        escenarios = ESCENARIOS_POR_DEFECTO

    dsn = _dsn()
    informes = []
    for tipo, p in escenarios:
        grafo = (
            grafo_repo(p["ruta"]) if tipo == "repo" else grafo_sintetico(p["nodos"], p["aristas"])
        )
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
