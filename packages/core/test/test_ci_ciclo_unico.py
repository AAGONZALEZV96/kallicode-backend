"""Puerta de CI (KC-BE-09-1): único punto de derivación del ciclo de facturación."""

from __future__ import annotations

import re
from pathlib import Path

ARCHIVOS_SQL_CICLO_PERMITIDOS = {
    "packages/core/src/kallicode_core/comercial.py",
    "apps/api/src/kallicode_api/routers/usage.py",
    "packages/codemapping/src/kallicode_codemapping/budget.py",
}

PATRONES_PROHIBIDOS = [
    (re.compile(r"\bciclo_actual\b"), "uso de ciclo_actual() basado en mes calendario"),
    (re.compile(r"strftime\(\s*['\"]%Y-%m['\"]\s*\)"), "derivación strftime('%Y-%m')"),
    (
        re.compile(r"to_char\([^)]*,\s*['\"]YYYY-MM['\"]\s*\)", re.IGNORECASE),
        "derivación SQL to_char(..., 'YYYY-MM')",
    ),
]

PATRON_FUNCION_SQL_CICLO = re.compile(r"core\.obtener_ciclo_cuota_actual\s*\(", re.IGNORECASE)


def test_puerta_ci_ciclo_facturacion_delegado_en_punto_unico() -> None:
    """Verifica que no existan derivaciones paralelas de fecha a ciclo ni llamadas dispersas."""
    ruta_raiz = Path(__file__).resolve().parents[3]
    violaciones: list[str] = []

    for carpeta in ("apps", "packages"):
        base = ruta_raiz / carpeta
        if not base.exists():
            continue
        for archivo_py in sorted(base.rglob("*.py")):
            rel_path = archivo_py.relative_to(ruta_raiz).as_posix()
            if (
                "/test/" in f"/{rel_path}"
                or "/tests/" in f"/{rel_path}"
                or "__pycache__" in rel_path
            ):
                continue

            contenido = archivo_py.read_text(encoding="utf-8", errors="ignore")
            for patron, motivo in PATRONES_PROHIBIDOS:
                if patron.search(contenido):
                    violaciones.append(f"{rel_path}: {motivo}")

            if rel_path not in ARCHIVOS_SQL_CICLO_PERMITIDOS and PATRON_FUNCION_SQL_CICLO.search(
                contenido
            ):
                violaciones.append(
                    f"{rel_path}: invoca core.obtener_ciclo_cuota_actual fuera de comercial.py"
                )

    assert not violaciones, (
        "REGRESIÓN DETECTADA EN CI (KC-BE-09-1): derivación paralela de ciclo de facturación:\n"
        + "\n".join(f"  - {v}" for v in violaciones)
    )
