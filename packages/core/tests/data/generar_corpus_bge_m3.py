"""Genera packages/core/tests/data/corpus_bge_m3.npz: embeddings BGE-M3 REALES de textos del repositorio.

Uso (requiere un servidor TEI con BAAI/bge-m3 en 127.0.0.1:8081):
    python generar_corpus_bge_m3.py [por_categoria=350]

Categorías (una por colección vectorial): docs (docs/*.md), tickets (comentarios en lenguaje natural de
db/**/*.sql) y definiciones (docstrings de Python). Ver bench_halfvec.py y ADR-022.
"""

import ast
import random
import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np

RAIZ = Path(__file__).resolve().parents[4]
SALIDA = Path(__file__).parent / "corpus_bge_m3.npz"
URL = "http://127.0.0.1:8081/v1/embeddings"
POR_CATEGORIA = int(sys.argv[1]) if len(sys.argv) > 1 else 350
trozos: list[tuple[str, str]] = []  # (categoria, texto)


def partir(texto: str, max_chars: int = 450) -> list[str]:
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if len(p.strip()) > 60]
    out, acc = [], ""
    for p in parrafos:
        if len(acc) + len(p) < max_chars:
            acc = (acc + "\n\n" + p).strip()
        else:
            if acc:
                out.append(acc)
            acc = p[: max_chars * 2]
    if acc:
        out.append(acc)
    return out


for ruta in (
    list(RAIZ.glob("docs/**/*.md")) + list(RAIZ.glob("*.md")) + list(RAIZ.glob("db/**/*.sql"))
):
    try:
        t = ruta.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    cat = "docs"
    if ruta.suffix == ".sql":
        # comentarios en lenguaje natural de las migraciones: lo más parecido a un ticket que hay en el repo
        cat = "tickets"
        t = "\n".join(
            re.sub(r"^\s*--\s?", "", ln) for ln in t.splitlines() if ln.strip().startswith("--")
        )
    if "plan-indices.md" in ruta.name:
        t = t[:60000]  # el inventario de consultas es casi todo tablas repetidas
    trozos += [(cat, x) for x in partir(t)]

for ruta in list(RAIZ.glob("apps/**/*.py")) + list(RAIZ.glob("packages/**/*.py")):
    try:
        arbol = ast.parse(ruta.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError):
        continue
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            d = ast.get_docstring(nodo)
            if d and len(d) > 60:
                trozos += [("definiciones", x) for x in (partir(d, 450) or [d[:450]])]

random.seed(5)
random.shuffle(trozos)
vistos: set[str] = set()
por_cat: dict[str, int] = {}
unicos: list[tuple[str, str]] = []
for cat, t in trozos:
    k = t[:200]
    if k in vistos or por_cat.get(cat, 0) >= POR_CATEGORIA:
        continue
    vistos.add(k)
    por_cat[cat] = por_cat.get(cat, 0) + 1
    unicos.append((cat, t[:350]))
print(por_cat, flush=True)

salida = []
cli = httpx.Client(timeout=300)
t0 = time.time()
LOTE = 16
for i in range(0, len(unicos), LOTE):
    lote = unicos[i : i + LOTE]
    r = cli.post(URL, json={"model": "bge-m3", "input": [t for _, t in lote]})
    r.raise_for_status()
    for (c, t), d in zip(lote, r.json()["data"], strict=True):
        salida.append((c, d["embedding"]))
    if (i // LOTE) % 10 == 0:
        print(f"  {len(salida)}/{len(unicos)} ({time.time() - t0:.0f}s)", flush=True)
np.savez_compressed(
    SALIDA,
    **{
        c: np.array([e for k, e in salida if k == c], dtype=np.float32)
        for c in sorted({k for k, _ in salida})
    },
)
print(f"listo: {len(salida)} vectores en {time.time() - t0:.0f}s -> {SALIDA}")
