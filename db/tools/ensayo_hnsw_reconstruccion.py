#!/usr/bin/env python3
"""Reconstrucción concurrente de los TRES índices HNSW bajo carga (KC-DB-11-4).

Carga N vectores en cada una de las tres tablas (vec.ticket_embeddings, vec.doc_chunks, vec.definition_embeddings),
arranca una carga concurrente de escrituras y de búsquedas por similitud y reconstruye cada índice con
REINDEX INDEX CONCURRENTLY (la misma orden que programa pg_cron, docs/runbook-vectorial.md). Registra:

  * duración y tamaño antes/después de cada reconstrucción (vec.medir_hnsw: queda en vec.hnsw_metricas),
  * errores de la carga concurrente (el criterio es CERO) y su latencia durante la reconstrucción,
  * que cada índice queda válido, sin restos de reconstrucciones a medias, y que las búsquedas lo siguen usando,
  * recall@10 antes y después contra la búsqueda exacta (SOLO informativo: con vectores aleatorios el recall depende de
    cómo se construyó el índice; el recall con embeddings reales lo vigila el flujo recall_vectorial_nocturno.yml).

IMPORTANTE:
  * Es para una base de PRUEBAS o staging: carga filas sintéticas (vectores aleatorios) con las claves foráneas
    desactivadas (session_replication_role = replica), las borra al terminar y deja los índices hinchados.
  * Los vectores son aleatorios, no embeddings reales: el recall absoluto no es el de producción; solo importa
    que no cambie al reconstruir.
  * Sobre 10^6 filas por tabla hace falta un servidor dimensionado (docs/runbook-vectorial.md §7: ~4,5 KB de
    maintenance_work_mem por fila, ~80 min por millón). Con --filas 1000000 --mwm 5GB en una máquina con RAM
    suficiente es el mismo ensayo; en un equipo con ~6 GB de RAM no se completa (KC-DB-08-2).

Uso:  KC_DATABASE_URL_MIGRATIONS=postgresql://... python db/tools/ensayo_hnsw_reconstruccion.py --filas 30000 --salida ./informe_hnsw
Código de salida: 0 si no hubo errores y los índices quedaron válidos y sin degradar el recall, 1 si no.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

DIM = 1024
TENANT = "org_hnsw_ens"
INDICES = {
    "ticket_embeddings_hnsw_ix": "vec.ticket_embeddings",
    "doc_chunks_hnsw_ix": "vec.doc_chunks",
    "definition_embeddings_hnsw_ix": "vec.definition_embeddings",
}
TABLAS = list(INDICES.values())


def _vec(v: Any) -> bytes:
    """Formato binario de pgvector: dim (int16), reservado (int16) y float4 en red."""
    return struct.pack(">HH", DIM, 0) + v.astype(">f4").tobytes()


def _lit(v: Any) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


def _unit(rng: np.random.Generator, n: int) -> Any:
    m = rng.standard_normal((n, DIM)).astype(np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    return m


def _conectar(dsn: str) -> Any:
    import psycopg

    c = psycopg.connect(dsn, autocommit=True)
    c.execute(
        "SET session_replication_role = replica"
    )  # sin claves foráneas ni disparadores: solo importan los índices
    return c


def cargar(conn: Any, filas: int, rng: np.random.Generator) -> None:
    """N filas en cada tabla, por COPY binario en lotes."""
    columnas = {
        "vec.ticket_embeddings": "(ticket_id, tenant_id, embedding, texto_hash)",
        "vec.doc_chunks": "(id, tenant_id, document_id, chunk_index, content, embedding, texto_hash)",
        "vec.definition_embeddings": "(id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash)",
    }
    tipos = {
        "vec.ticket_embeddings": ["text", "text", "bytea", "text"],
        "vec.doc_chunks": ["text", "text", "text", "int4", "text", "bytea", "text"],
        "vec.definition_embeddings": [
            "text",
            "text",
            "text",
            "text",
            "text",
            "text",
            "bytea",
            "text",
        ],
    }
    for tabla in TABLAS:
        hecho = 0
        while hecho < filas:
            n = min(10_000, filas - hecho)
            m = _unit(rng, n)
            with (
                conn.cursor() as cur,
                cur.copy(f"COPY {tabla} {columnas[tabla]} FROM STDIN (FORMAT BINARY)") as cp,
            ):
                cp.set_types(tipos[tabla])
                for i in range(n):
                    k = hecho + i
                    if tabla == "vec.ticket_embeddings":
                        cp.write_row((f"tk_{k}", TENANT, _vec(m[i]), "h"))
                    elif tabla == "vec.doc_chunks":
                        cp.write_row(
                            (f"ch_{k}", TENANT, "doc_ens", k, "contenido", _vec(m[i]), "h")
                        )
                    else:
                        cp.write_row(
                            (f"de_{k}", TENANT, f"nd_{k}", "symbol", f"n{k}", "d", _vec(m[i]), "h")
                        )
            hecho += n
        conn.execute(f"ANALYZE {tabla}")  # type: ignore[call-overload]


def borrar(conn: Any) -> None:
    for tabla in TABLAS:
        conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (TENANT,))  # type: ignore[call-overload]
        conn.execute(f"VACUUM {tabla}")  # type: ignore[call-overload]


def recall(conn: Any, rng: np.random.Generator, consultas: int = 30) -> dict[str, float]:
    """Recall@10 de la búsqueda por índice frente a la exacta, por tabla."""
    out: dict[str, float] = {}
    qs = _unit(rng, consultas)
    for tabla in TABLAS:
        aciertos = 0
        for q in qs:
            lit = _lit(q)
            sql = f"SELECT {'ticket_id' if 'ticket' in tabla else 'id'} FROM {tabla} WHERE tenant_id = %s ORDER BY embedding <=> %s::vector LIMIT 10"
            conn.execute(
                "SET enable_indexscan = on; SET enable_seqscan = off; SET hnsw.ef_search = 100"
            )
            aprox = {r[0] for r in conn.execute(sql, (TENANT, lit)).fetchall()}  # type: ignore[call-overload]
            conn.execute("SET enable_indexscan = off; SET enable_seqscan = on")
            exacta = {r[0] for r in conn.execute(sql, (TENANT, lit)).fetchall()}  # type: ignore[call-overload]
            aciertos += len(aprox & exacta)
        out[tabla] = round(aciertos / (10 * consultas), 3)
    conn.execute("RESET enable_indexscan; RESET enable_seqscan")
    return out


class CargaConcurrente:
    """Escribe (inserta y actualiza) y busca por similitud en las tres tablas mientras se reconstruyen los índices."""

    def __init__(self, dsn: str, filas: int) -> None:
        self.dsn, self.filas = dsn, filas
        self.parar = threading.Event()
        self.errores: list[str] = []
        self.lat: dict[str, list[tuple[float, float]]] = {"escritura": [], "lectura": []}
        self.hilos = [
            threading.Thread(target=self._escritor, daemon=True),
            threading.Thread(target=self._lector, daemon=True),
        ]

    def _escritor(self) -> None:
        c = _conectar(self.dsn)
        rng = np.random.default_rng(99)
        k = 10_000_000 + int(time.time()) % 1_000_000
        while not self.parar.is_set():
            v = _unit(rng, 1)[0]
            t = time.perf_counter()
            try:
                c.execute(
                    "INSERT INTO vec.doc_chunks (id, tenant_id, document_id, chunk_index, content, embedding, texto_hash) "
                    "VALUES (%s, %s, 'doc_ens', %s, 'concurrente', %s::vector, 'h')",
                    (f"cc_{k}", TENANT, k, _lit(v)),
                )
                c.execute(
                    "INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding, texto_hash) VALUES (%s, %s, %s::vector, 'h')",
                    (f"tc_{k}", TENANT, _lit(v)),
                )
                c.execute(
                    "UPDATE vec.definition_embeddings SET actualizado_en = now() WHERE tenant_id = %s AND id = %s",
                    (TENANT, f"de_{k % max(self.filas, 1)}"),
                )
            except Exception as exc:
                self.errores.append(f"escritura: {str(exc)[:120]}")
            self.lat["escritura"].append((time.time(), time.perf_counter() - t))
            k += 1
            time.sleep(0.02)

    def _lector(self) -> None:
        c = _conectar(self.dsn)
        rng = np.random.default_rng(123)
        i = 0
        while not self.parar.is_set():
            q = _lit(_unit(rng, 1)[0])
            tabla = TABLAS[i % 3]
            i += 1
            t = time.perf_counter()
            try:
                c.execute(
                    f"SELECT 1 FROM {tabla} WHERE tenant_id = %s ORDER BY embedding <=> %s::vector LIMIT 10",
                    (TENANT, q),
                ).fetchall()  # type: ignore[call-overload]
            except Exception as exc:
                self.errores.append(f"lectura: {str(exc)[:120]}")
            self.lat["lectura"].append((time.time(), time.perf_counter() - t))
            time.sleep(0.02)

    def iniciar(self) -> None:
        for h in self.hilos:
            h.start()

    def detener(self) -> None:
        self.parar.set()
        for h in self.hilos:
            h.join(timeout=15)


def _pct(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    s = sorted(valores)
    return round(s[min(int(len(s) * p), len(s) - 1)] * 1000, 1)


def _estado(conn: Any, indice: str) -> dict[str, Any]:
    fila = conn.execute(
        "SELECT i.indisvalid, i.indisready, CASE WHEN c.relkind = 'I' THEN (SELECT coalesce(sum(pg_relation_size(t.relid)), 0) FROM pg_partition_tree(c.oid) t WHERE t.isleaf) ELSE pg_relation_size(c.oid) END "
        "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.oid = ('vec.' || %s)::regclass",
        (indice,),
    ).fetchone()
    restos = conn.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'vec' AND (c.relname LIKE '%%\\_ccnew%%' OR c.relname LIKE '%%\\_ccold%%')"
    ).fetchone()
    invalidos = conn.execute(
        "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'vec' AND NOT i.indisvalid"
    ).fetchone()
    return {
        "valido": bool(fila[0]),
        "listo": bool(fila[1]),
        "tamano_mb": round(int(fila[2]) / 1048576, 1),
        "restos_de_reconstruccion": int(restos[0]),
        "indices_invalidos_en_vec": int(invalidos[0]),
    }


def ensayar(dsn: str, filas: int, mwm: str) -> dict[str, Any]:
    admin = _conectar(dsn)
    rng = np.random.default_rng(7)
    informe: dict[str, Any] = {"filas_por_tabla": filas, "maintenance_work_mem": mwm, "indices": {}}
    carga: CargaConcurrente | None = None
    try:
        t0 = time.perf_counter()
        cargar(admin, filas, rng)
        informe["carga_inicial_s"] = round(time.perf_counter() - t0, 1)
        informe["recall_antes"] = recall(admin, np.random.default_rng(5))

        carga = CargaConcurrente(dsn, filas)
        carga.iniciar()
        time.sleep(3)
        admin.execute(f"SET maintenance_work_mem = '{mwm}'")  # type: ignore[call-overload]
        for indice in INDICES:
            antes = _estado(admin, indice)
            admin.execute("SELECT * FROM vec.medir_hnsw('antes', NULL, %s::text)", (indice,))
            inicio = time.time()
            t = time.perf_counter()
            admin.execute(f"REINDEX INDEX CONCURRENTLY vec.{indice}")  # type: ignore[call-overload]
            dur = time.perf_counter() - t
            fin = time.time()
            admin.execute(
                "SELECT * FROM vec.medir_hnsw('despues', %s::numeric, %s::text)",
                (round(dur / 60, 2), indice),
            )
            despues = _estado(admin, indice)
            durante_e = [x for ts, x in carga.lat["escritura"] if inicio <= ts <= fin]
            durante_l = [x for ts, x in carga.lat["lectura"] if inicio <= ts <= fin]
            informe["indices"][indice] = {
                "reconstruccion_s": round(dur, 1),
                "tamano_antes_mb": antes["tamano_mb"],
                "tamano_despues_mb": despues["tamano_mb"],
                "valido": despues["valido"],
                "restos_de_reconstruccion": despues["restos_de_reconstruccion"],
                "escrituras_durante": len(durante_e),
                "escritura_p50_ms": _pct(durante_e, 0.5),
                "escritura_p99_ms": _pct(durante_e, 0.99),
                "escritura_max_ms": _pct(durante_e, 1.0),
                "lecturas_durante": len(durante_l),
                "lectura_p50_ms": _pct(durante_l, 0.5),
                "lectura_p99_ms": _pct(durante_l, 0.99),
                "lectura_max_ms": _pct(durante_l, 1.0),
            }
        carga.detener()
        informe["errores_de_la_carga"] = len(carga.errores)
        informe["primeros_errores"] = carga.errores[:3]
        informe["escrituras_totales"] = len(carga.lat["escritura"])
        informe["lecturas_totales"] = len(carga.lat["lectura"])
        informe["recall_despues"] = recall(admin, np.random.default_rng(5))
        plan = {}
        for tabla in TABLAS:
            lit = _lit(_unit(np.random.default_rng(1), 1)[0])
            admin.execute("SET enable_seqscan = off")
            txt = "\n".join(
                r[0]
                for r in admin.execute(
                    f"EXPLAIN SELECT 1 FROM {tabla} WHERE tenant_id = %s ORDER BY embedding <=> %s::vector LIMIT 10",
                    (TENANT, lit),
                ).fetchall()  # type: ignore[call-overload]
            )
            plan[tabla] = "hnsw" in txt
        informe["las_busquedas_usan_el_indice"] = plan
        degradado = {
            t: (informe["recall_antes"][t], informe["recall_despues"][t])
            for t in TABLAS
            if informe["recall_despues"][t] < informe["recall_antes"][t] - 0.03
        }
        informe["recall_degradado"] = degradado
        informe["ok"] = bool(
            informe["errores_de_la_carga"] == 0
            and all(
                v["valido"] and v["restos_de_reconstruccion"] == 0
                for v in informe["indices"].values()
            )
            and all(plan.values())
        )
    finally:
        if carga is not None:
            carga.detener()
        borrar(admin)
        admin.close()
    return informe


def markdown(i: dict[str, Any]) -> str:
    filas = [
        "| Índice | Reconstrucción | Tamaño antes → después | Escrituras durante (p50/p99/máx ms) | Lecturas durante (p50/p99/máx ms) | Válido |",
        "|---|---|---|---|---|---|",
    ]
    for nombre, v in i["indices"].items():
        filas.append(
            f"| {nombre} | {v['reconstruccion_s']} s | {v['tamano_antes_mb']} → {v['tamano_despues_mb']} MB "
            f"| {v['escrituras_durante']} ({v['escritura_p50_ms']}/{v['escritura_p99_ms']}/{v['escritura_max_ms']}) "
            f"| {v['lecturas_durante']} ({v['lectura_p50_ms']}/{v['lectura_p99_ms']}/{v['lectura_max_ms']}) | {'SÍ' if v['valido'] else 'NO'} |"
        )
    return "\n".join(
        [
            "# Reconstrucción concurrente de los índices HNSW bajo carga (KC-DB-11-4)",
            "",
            f"Resultado: **{'SIN ERRORES' if i['ok'] else 'FALLA'}** · {i['filas_por_tabla']} filas por tabla · maintenance_work_mem {i['maintenance_work_mem']}",
            "",
            f"* Carga inicial: {i['carga_inicial_s']} s.",
            f"* Carga concurrente durante la reconstrucción: {i['escrituras_totales']} ciclos de escritura y {i['lecturas_totales']} búsquedas; **errores: {i['errores_de_la_carga']}** {i['primeros_errores']}.",
            f"* Recall@10 (informativo; vectores aleatorios, NO gobierna el resultado) antes: {i['recall_antes']} · después: {i['recall_despues']} · caída de más de 0,03: {i['recall_degradado'] or 'no'}.",
            f"* Las búsquedas siguen usando el índice HNSW: {i['las_busquedas_usan_el_indice']}.",
            "",
            *filas,
            "",
            "## Límites",
            "",
            "* Vectores aleatorios (no embeddings reales), claves foráneas desactivadas y un solo tenant: mide la mecánica de la reconstrucción, no la calidad semántica.",
            "* Docker local con pocos recursos; el tamaño del ensayo no es el de producción (el criterio de 10^6 filas pide un servidor dimensionado: docs/runbook-vectorial.md §7).",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--filas", type=int, default=30_000, help="filas por tabla (por defecto 30000)")
    ap.add_argument("--mwm", default="1GB", help="maintenance_work_mem de la reconstrucción")
    ap.add_argument("--salida", type=Path)
    args = ap.parse_args(argv)
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        raise SystemExit("KC_DATABASE_URL_MIGRATIONS (rol administrador) no está definida")
    esquema, sep, resto = url.partition("://")
    dsn = f"{esquema.split('+', 1)[0]}://{resto}" if sep else url
    informe = ensayar(dsn, args.filas, args.mwm)
    texto = markdown(informe)
    print(texto)
    if args.salida:
        args.salida.mkdir(parents=True, exist_ok=True)
        (args.salida / "informe.md").write_text(texto, encoding="utf-8")
        (args.salida / "informe.json").write_text(
            json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return 0 if informe["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
