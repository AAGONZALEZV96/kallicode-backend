"""Ensayo de recuperación ante desastre del grafo: reconstrucción verificada (KC-CM-12-1).

Convierte en procedimiento probado la recomendación de NO exportar el grafo sino reconstruirlo con una ingesta:

  1. Grafo de referencia: ingesta completa del repositorio sintético (repo_sintetico) con el pipeline real.
  2. Desastre simulado de UN tenant con `cm.vaciar_grafo_tenant` (la función de Database, KC-DB-11-4), primero con
     alcance `determinista` y luego con `completo`.
  3. Reconstrucción con `build_graph.full_build(alcance=...)`: `determinista` no llama al LLM ni a TEI y deja intacta la
     capa semántica y humana (que en un desastre real se restaura de la copia de seguridad); `completo` repite la
     ingesta entera.
  4. Validación por recuentos de `cm.recuentos_grafo` (nodos por kind, aristas por tipo, cierre transitivo) frente
     a los de la referencia, con tolerancia del 0,5 %; la capa determinista, además, frente al generador.
  5. Tiempo de recuperación medido frente al RTO vigente de core.objetivos_recuperacion (aprobado o, si no lo hay,
     propuesto).

Imprime el acta del ensayo en Markdown (docs/ensayo_reconstruccion_grafo_acta.md es una ejecución guardada). Solo se
sustituyen el LLM, TEI y la base del cliente (el esquema); el resto es el pipeline real con la base real y el rol
de la aplicación. En el staging real, la base del cliente y el repositorio son los reales (runbook, §4).

    uv run python packages/codemapping/tests/ensayo_reconstruccion_grafo.py --acta acta.md
    uv run pytest packages/codemapping/tests/ensayo_reconstruccion_grafo.py -v     # puerta del ensayo mensual

Variables: KC_DATABASE_URL_MIGRATIONS (superusuario, obligatoria), KC_BENCH_ING_DIVISOR (1 = el grafo de 200.000
nodos; N = 1/N), KC_ENSAYO_ENTORNO (texto del acta: dónde se ejecutó; por defecto «CI»), KC_ENSAYO_RTO_MIN (fija
el RTO en minutos en lugar de leerlo de la base).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import build_graph, repo_sintetico
from kallicode_codemapping.config import Settings
from kallicode_codemapping.graph_loader import normalizar_dsn

pytestmark = pytest.mark.benchmark

TENANT = "ensayo_reconstruccion"
TOLERANCIA = 0.005  # 0,5 %
RTO_POR_DEFECTO_MIN = 60  # la propuesta de KC-DB-11-1, si la base no tiene objetivos

# Lo de la capa determinista: se reconstruye desde el repositorio y la base del cliente.
OBJETOS_DETERMINISTAS = (
    "nodo:file",
    "nodo:symbol",
    "nodo:db_table",
    "nodo:db_column",
    "arista:CALLS",
    "arista:USES_TABLE",
    "arista:REFERENCES",
    "reachability",
)
# Lo de la capa semántica y humana: un desastre determinista no puede tocarla.
OBJETOS_SEMANTICOS = (
    "nodo:definition",
    "nodo:concept",
    "arista:REALIZED_BY",
    "arista:EXPRESSES",
    "arista:RELATES",
)
TABLAS_LIMPIEZA = (
    "vec.definition_embeddings",
    "cm.reachability",
    "cm.reachability_estado",
    "cm.doc_chunk_links",
    "cm.edges",
    "cm.symbols",
    "cm.files",
    "cm.db_columns",
    "cm.db_tables",
    "cm.definitions",
    "cm.concepts",
    "cm.curation_queue",
    "cm.snapshot_vigente",
    "cm.repo_snapshots",
    "core.usage_tokens",
)


def _cargar_bench() -> Any:
    """Reutiliza los dobles del benchmark de ingesta (KC-CM-11-1): mismo pipeline, mismos sustitutos."""
    ruta = Path(__file__).with_name("bench_ingesta_escala.py")
    spec = importlib.util.spec_from_file_location("bench_ingesta_escala_ensayo", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _dsn_admin() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")  # en CI siempre está
    return normalizar_dsn(url)


def _dsn_rol(admin: str, rol: str) -> str:
    return admin + ("&" if "?" in admin else "?") + f"options=-c%20role%3D{rol}"


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for tabla in TABLAS_LIMPIEZA:
        if conn.execute("SELECT to_regclass(%s)", (tabla,)).fetchone() == (None,):
            continue
        conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.organizations WHERE id = %s", (TENANT,))


def recuentos(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Los recuentos de Database: cm.recuentos_grafo(tenant)."""
    filas = conn.execute("SELECT objeto, filas FROM cm.recuentos_grafo(%s)", (TENANT,)).fetchall()
    return {str(o): int(n) for o, n in filas}


def _extra(conn: psycopg.Connection[Any]) -> dict[str, int]:
    """Lo que cm.recuentos_grafo no cuenta: aristas colgantes y descripciones LLM de los símbolos."""
    return {
        "aristas_semanticas_colgantes": int(
            conn.execute(
                """
                SELECT count(*) FROM cm.edges e
                 WHERE e.tenant_id = %s AND e.tipo IN ('REALIZED_BY', 'EXPRESSES', 'RELATES')
                   AND NOT EXISTS (SELECT 1 FROM cm.nodes n WHERE n.tenant_id = e.tenant_id AND n.id = e.src_id)
                """,
                (TENANT,),
            ).fetchone()[0]  # type: ignore[index]
            + conn.execute(
                """
                SELECT count(*) FROM cm.edges e
                 WHERE e.tenant_id = %s AND e.tipo IN ('REALIZED_BY', 'EXPRESSES', 'RELATES')
                   AND NOT EXISTS (SELECT 1 FROM cm.nodes n WHERE n.tenant_id = e.tenant_id AND n.id = e.dst_id)
                """,
                (TENANT,),
            ).fetchone()[0]  # type: ignore[index]
        ),
        "simbolos_con_descripcion": int(
            conn.execute(
                "SELECT count(*) FROM cm.symbols WHERE tenant_id = %s AND descripcion IS NOT NULL",
                (TENANT,),
            ).fetchone()[0]  # type: ignore[index]
        ),
    }


KIND_DE_CAPA = {
    "files": "file",
    "symbols": "symbol",
    "db_tables": "db_table",
    "db_columns": "db_column",
    "definitions": "definition",
    "concepts": "concept",
}


def _esperado(escala: repo_sintetico.Escala) -> dict[str, int]:
    """Lo que dice el generador, con las claves de cm.recuentos_grafo (sin la alcanzabilidad, que no genera)."""
    e = escala.esperado()
    return {
        **{f"nodo:{KIND_DE_CAPA[c]}": n for c, n in e["nodos"].items()},
        **{f"arista:{t}": n for t, n in e["aristas"].items()},
    }


def diferencias(
    referencia: dict[str, int], actual: dict[str, int], objetos: tuple[str, ...] | None = None
) -> list[dict[str, Any]]:
    """Por objeto: referencia, actual, diferencia relativa y si queda dentro de la tolerancia."""
    claves = sorted(set(objetos) if objetos else set(referencia) | set(actual))
    filas: list[dict[str, Any]] = []
    for k in claves:
        r, a = referencia.get(k, 0), actual.get(k, 0)
        dif = abs(a - r) / r if r else (0.0 if a == 0 else 1.0)
        filas.append(
            {"objeto": k, "referencia": r, "actual": a, "dif": dif, "ok": dif <= TOLERANCIA}
        )
    return filas


def _rto_objetivo_s(conn: psycopg.Connection[Any]) -> tuple[float, str]:
    if os.getenv("KC_ENSAYO_RTO_MIN"):
        return float(os.environ["KC_ENSAYO_RTO_MIN"]) * 60, "fijado por KC_ENSAYO_RTO_MIN"
    fila = conn.execute(
        "SELECT rto_minutos, estado FROM core.objetivos_recuperacion "
        "ORDER BY (estado = 'aprobado') DESC, id DESC LIMIT 1"
    ).fetchone()
    if fila is None:
        return RTO_POR_DEFECTO_MIN * 60.0, "por defecto (core.objetivos_recuperacion vacía)"
    return float(fila[0]) * 60, f"core.objetivos_recuperacion ({fila[1]})"


def _recalcular_reachability(app: str) -> None:
    """Paso 6 del runbook tras una ingesta completa: la alcanzabilidad es derivada."""
    with psycopg.connect(app, autocommit=False) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (TENANT,))
        conn.execute("SELECT estado, filas, caminos FROM cm.refrescar_reachability()")
        conn.commit()


def _desastre_y_reconstruccion(
    bench: Any,
    admin: str,
    app: str,
    settings: Settings,
    escala: repo_sintetico.Escala,
    alcance: str,
    referencia: dict[str, int],
    head: str,
) -> dict[str, Any]:
    """Un ciclo: vaciar el grafo del tenant, reconstruirlo y medir. Devuelve el resultado del ensayo."""
    resultado: dict[str, Any] = {"alcance": alcance}
    with psycopg.connect(_dsn_rol(admin, "kallicode_admin"), autocommit=True) as conn:
        inicio = time.perf_counter()
        borrado = conn.execute(
            "SELECT objeto, filas FROM cm.vaciar_grafo_tenant(%s, %s, %s)",
            (TENANT, alcance, TENANT),
        ).fetchall()
        resultado["vaciado_s"] = time.perf_counter() - inicio
        resultado["borrado"] = {str(o): int(n) for o, n in borrado}
        resultado["tras_el_desastre"] = recuentos(conn)

    inicio = time.perf_counter()
    build_graph.full_build(settings, alcance=alcance)
    if alcance == "completo":
        _recalcular_reachability(app)
    resultado["reconstruccion_s"] = time.perf_counter() - inicio
    resultado["recuperacion_s"] = resultado["vaciado_s"] + resultado["reconstruccion_s"]

    with psycopg.connect(admin, autocommit=True) as conn:
        actual = recuentos(conn)
        resultado["recuentos"] = actual
        resultado["extra"] = _extra(conn)
        resultado["snapshot"] = conn.execute(
            "SELECT commit_sha, estado FROM cm.repo_snapshots WHERE tenant_id = %s "
            "ORDER BY iniciado_en DESC LIMIT 1",
            (TENANT,),
        ).fetchone()
    resultado["commit_reconstruido_ok"] = bool(
        resultado["snapshot"]
        and resultado["snapshot"][0] == head
        and resultado["snapshot"][1] == "completo"
    )
    esperado = _esperado(escala)
    resultado["deterministas"] = diferencias(referencia, actual, OBJETOS_DETERMINISTAS)
    resultado["semanticos"] = diferencias(referencia, actual, OBJETOS_SEMANTICOS)
    resultado["frente_al_generador"] = diferencias(esperado, actual, tuple(esperado))
    return resultado


def ejecutar() -> dict[str, Any]:
    bench = _cargar_bench()
    admin = _dsn_admin()
    app = _dsn_rol(admin, "kallicode_app")
    escala = bench._escala()
    base = Path(tempfile.mkdtemp(prefix="kc_ensayo_reconstruccion_"))
    informe: dict[str, Any] = {
        "ejecutado": datetime.now(UTC).isoformat(timespec="seconds"),
        "entorno": os.getenv(
            "KC_ENSAYO_ENTORNO", "CI (Postgres en contenedor, base del cliente simulada)"
        ),
        "escala": escala.esperado(),
        "tolerancia": TOLERANCIA,
    }
    with psycopg.connect(admin, autocommit=True) as conn:
        informe["postgres"] = str(conn.execute("SELECT version()").fetchone()[0]).split(" on ")[0]  # type: ignore[index]
        informe["rto_objetivo_s"], informe["rto_origen"] = _rto_objetivo_s(conn)
        _limpiar(conn)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (TENANT, TENANT)
        )
    try:
        origen = base / "origen"
        head = repo_sintetico.crear_repositorio_git(origen, escala)
        informe["commit"] = head[:10]
        settings = Settings(
            tenant_id=TENANT,
            repo_url=str(origen),
            workdir=str(base / "workdir"),
            store_database_url=app,
            database_url="postgresql://cliente:x@localhost:5432/cliente",  # type: ignore[arg-type]
        )
        with bench._aplicar(bench._dobles(escala)):
            inicio = time.perf_counter()
            build_graph.full_build(settings)
            _recalcular_reachability(app)
            informe["referencia_s"] = time.perf_counter() - inicio
            with psycopg.connect(admin, autocommit=True) as conn:
                referencia = recuentos(conn)
                informe["referencia"] = referencia
                informe["referencia_extra"] = _extra(conn)
            informe["ciclos"] = [
                _desastre_y_reconstruccion(
                    bench, admin, app, settings, escala, alcance, referencia, head
                )
                for alcance in ("determinista", "completo")
            ]
    finally:
        shutil.rmtree(base, ignore_errors=True)
        with psycopg.connect(admin, autocommit=True) as conn:
            _limpiar(conn)
    return informe


# ---------------------------------------------------------------------------
# Acta
# ---------------------------------------------------------------------------
def _s(x: float) -> str:
    return f"{x:.1f} s" if x < 600 else f"{x / 60:.1f} min"


def _tabla(filas: list[dict[str, Any]]) -> list[str]:
    out = [
        "| objeto | referencia | tras reconstruir | diferencia | ¿dentro del 0,5 %? |",
        "|---|---:|---:|---:|---|",
    ]
    for f in filas:
        out.append(
            f"| {f['objeto']} | {f['referencia']:,} | {f['actual']:,} | {f['dif'] * 100:.2f} % | "
            f"{'SÍ' if f['ok'] else 'NO'} |"
        )
    return out


def veredicto(informe: dict[str, Any]) -> list[str]:
    """Incumplimientos del ensayo (lista vacía = cumple)."""
    fallos: list[str] = []
    for c in informe["ciclos"]:
        a = c["alcance"]
        fallos += [
            f"[{a}] {f['objeto']} fuera de la tolerancia" for f in c["deterministas"] if not f["ok"]
        ]
        fallos += [
            f"[{a}] {f['objeto']} (capa semántica) cambió" for f in c["semanticos"] if not f["ok"]
        ]
        fallos += [
            f"[{a}] {f['objeto']} difiere del generador"
            for f in c["frente_al_generador"]
            if not f["ok"]
        ]
        if c["recuperacion_s"] > informe["rto_objetivo_s"]:
            fallos.append(
                f"[{a}] RTO {_s(c['recuperacion_s'])} > objetivo {_s(informe['rto_objetivo_s'])}"
            )
        if not c["commit_reconstruido_ok"]:
            fallos.append(
                f"[{a}] el snapshot reconstruido no es completo o no corresponde al HEAD del repositorio"
            )
        if c["extra"]["aristas_semanticas_colgantes"]:
            fallos.append(f"[{a}] quedaron aristas semánticas sin nodo")
    return fallos


def acta_markdown(informe: dict[str, Any]) -> str:
    fallos = veredicto(informe)
    esc = informe["escala"]["total"]
    ref_extra = informe["referencia_extra"]
    l = [
        "# Acta del ensayo de reconstrucción del grafo (KC-CM-12-1)",
        "",
        f"Ejecutado: {informe['ejecutado']} · {informe['postgres']} · entorno: {informe['entorno']}",
        f"Grafo de referencia: {esc['nodos']:,} nodos y {esc['aristas']:,} aristas (repositorio sintético @ {informe['commit']}). "
        f"Reconstrucción por ingesta del mismo commit.",
        f"Resultado global: **{'CUMPLE' if not fallos else 'NO CUMPLE'}** (recuentos dentro del 0,5 % y tiempo de recuperación "
        f"dentro del RTO vigente).",
        "",
        "## Tiempo de recuperación frente al objetivo",
        "",
        f"RTO objetivo: {_s(informe['rto_objetivo_s'])} (fuente: {informe['rto_origen']}). El tiempo cuenta desde que "
        "empieza el borrado del grafo del tenant hasta que el grafo vuelve a estar completo, con la alcanzabilidad "
        "recalculada; **no** incluye detectar el desastre, decidir reconstruir ni restaurar la capa semántica desde la copia.",
        "",
        "| alcance | borrado | reconstrucción | recuperación | RTO objetivo | ¿cumple? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for c in informe["ciclos"]:
        l.append(
            f"| {c['alcance']} | {_s(c['vaciado_s'])} | {_s(c['reconstruccion_s'])} | {_s(c['recuperacion_s'])} | "
            f"{_s(informe['rto_objetivo_s'])} | {'SÍ' if c['recuperacion_s'] <= informe['rto_objetivo_s'] else 'NO'} |"
        )
    l += [
        "",
        f"Ingesta completa de referencia (con llamadas al LLM y a TEI simuladas): {_s(informe['referencia_s'])}. "
        "Con el LLM y TEI reales, el alcance `completo` suma su latencia y su costo en tokens; el `determinista` no los usa.",
    ]
    for c in informe["ciclos"]:
        borrado = ", ".join(f"{k} {v:,}" for k, v in c["borrado"].items() if v)
        l += [
            "",
            f"## Alcance `{c['alcance']}`",
            "",
            f"Desastre simulado (cm.vaciar_grafo_tenant): {borrado}.",
            "",
            "Capa determinista (archivos, símbolos, tablas, columnas, sus aristas y la alcanzabilidad):",
            "",
            *_tabla(c["deterministas"]),
            "",
            "Capa semántica y humana (no debe cambiar con un desastre determinista, y se repite igual en el completo):",
            "",
            *_tabla(c["semanticos"]),
            "",
            f"Frente al generador del repositorio sintético: {'todos los objetos coinciden' if all(f['ok'] for f in c['frente_al_generador']) else 'HAY DIFERENCIAS'}. "
            f"Aristas semánticas sin nodo: {c['extra']['aristas_semanticas_colgantes']}. "
            f"Símbolos con descripción LLM: {ref_extra['simbolos_con_descripcion']:,} antes, {c['extra']['simbolos_con_descripcion']:,} después.",
        ]
    det = next(c for c in informe["ciclos"] if c["alcance"] == "determinista")
    perdidas = ref_extra["simbolos_con_descripcion"] - det["extra"]["simbolos_con_descripcion"]
    l += [
        "",
        "## Objetivos de recuperación del esquema cm (propuestos a Database)",
        "",
        "| capa | RPO | RTO | cómo se recupera |",
        "|---|---|---|---|",
        "| Determinista (archivos, símbolos, tablas, columnas y sus aristas, alcanzabilidad) | 0 commits: se reconstruye del "
        'HEAD vigente del repositorio | tiempo del alcance `determinista` de esta acta (≤ RTO de la base) | `full_build(alcance="determinista")` |',
        "| Semántica y humana (definiciones, conceptos, aristas semánticas, curación, vectores) | el de la base (PITR) | el de la restauración PITR | copia de seguridad (core.politica_recuperacion) |",
        "",
        "Pendiente: aprobación de estos objetivos por Database (los de la base, RPO 5 min y RTO 60 min, siguen en estado propuesto).",
        "",
        "## Hallazgos",
        "",
    ]
    if perdidas > 0:
        l.append(
            f"- Reconstruir el alcance `determinista` borra {perdidas:,} descripciones LLM de símbolos (`cm.symbols.descripcion`) "
            "y los vectores de código que dependen de ellas: viven en la misma fila y core.politica_recuperacion marca `cm.symbols` "
            "como «sin LLM». Si hay que conservarlas, restaurarlas del backup o ingerir con alcance `completo` (cuesta tokens)."
        )
    else:
        l.append("- Sin hallazgos sobre las descripciones de símbolos.")
    if fallos:
        l += ["", "## Incumplimientos", "", *[f"- {f}" for f in fallos]]
    l += [
        "",
        "## Firmas",
        "",
        "| Rol | Nombre | Fecha |",
        "|---|---|---|",
        "| Ejecutó | (completar) | |",
        "| Revisó (Database) | (completar) | |",
        "| Aprobó | (completar) | |",
        "",
    ]
    return "\n".join(l)


# ---------------------------------------------------------------------------
# Puerta del ensayo mensual
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def informe() -> dict[str, Any]:
    return ejecutar()


def test_la_capa_determinista_vuelve_dentro_del_0_5_por_ciento(informe: dict[str, Any]) -> None:
    for c in informe["ciclos"]:
        assert all(f["ok"] for f in c["deterministas"]), (c["alcance"], c["deterministas"])


def test_la_reconstruccion_coincide_con_el_generador(informe: dict[str, Any]) -> None:
    for c in informe["ciclos"]:
        assert all(f["ok"] for f in c["frente_al_generador"]), (
            c["alcance"],
            c["frente_al_generador"],
        )


def test_la_capa_semantica_no_cambia(informe: dict[str, Any]) -> None:
    for c in informe["ciclos"]:
        assert all(f["ok"] for f in c["semanticos"]), (c["alcance"], c["semanticos"])
        assert c["extra"]["aristas_semanticas_colgantes"] == 0


def test_el_desastre_vacia_lo_que_dice_el_alcance(informe: dict[str, Any]) -> None:
    det = next(c for c in informe["ciclos"] if c["alcance"] == "determinista")
    assert det["tras_el_desastre"].get("nodo:symbol", 0) == 0
    assert (
        det["tras_el_desastre"].get("nodo:definition", 0)
        == informe["referencia"]["nodo:definition"]
    )
    comp = next(c for c in informe["ciclos"] if c["alcance"] == "completo")
    assert comp["tras_el_desastre"].get("nodo:definition", 0) == 0


def test_el_tiempo_de_recuperacion_cumple_el_rto(informe: dict[str, Any]) -> None:
    for c in informe["ciclos"]:
        assert c["recuperacion_s"] <= informe["rto_objetivo_s"], c["alcance"]


def test_el_snapshot_reconstruido_es_completo_y_del_head(informe: dict[str, Any]) -> None:
    assert all(c["commit_reconstruido_ok"] for c in informe["ciclos"])


def test_el_veredicto_del_acta_cumple(informe: dict[str, Any]) -> None:
    assert veredicto(informe) == []


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--acta", help="ruta del acta en Markdown (además de imprimirla)")
    ap.add_argument("--json", help="ruta del JSON con las mediciones")
    args = ap.parse_args()
    datos = ejecutar()
    texto = acta_markdown(datos)
    print(texto)
    if args.acta:
        Path(args.acta).write_text(texto, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(datos, indent=2, default=str), encoding="utf-8")
    sys.exit(1 if veredicto(datos) else 0)
