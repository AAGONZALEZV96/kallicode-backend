"""Compuerta de calidad de las pruebas de mutación (KC-CM-06-6).

Ejecuta `mutmut results --all true`, calcula el puntaje de mutación
(mutantes muertos / mutantes evaluados) y falla si queda por debajo del mínimo.

Uso (desde packages/codemapping, después de `mutmut run`):
    uv run --with "mutmut>=3.8,<4" python scripts/verificar_mutacion.py --minimo 90

Los mutantes equivalentes (p. ej. `encoding="utf-8"` -> `"UTF-8"`) sobreviven por
construcción; por eso el mínimo es 90 y no 100. El valor medido al cerrar el
ticket fue 96,7 % (325 de 336).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter

_LINEA = re.compile(r"^\s*\S+:\s+(?P<estado>[a-z ]+?)\s*$")
_ESTADOS_QUE_NO_CUENTAN = {"skipped"}


def contar_estados(salida: str) -> Counter[str]:
    """Cuenta mutantes por estado a partir de la salida de `mutmut results`."""
    conteo: Counter[str] = Counter()
    for linea in salida.splitlines():
        m = _LINEA.match(linea)
        if m:
            conteo[m.group("estado")] += 1
    return conteo


def puntaje(conteo: Counter[str]) -> float:
    """Porcentaje de mutantes muertos sobre los evaluados (excluye 'skipped')."""
    evaluados = sum(n for estado, n in conteo.items() if estado not in _ESTADOS_QUE_NO_CUENTAN)
    if evaluados == 0:
        return 0.0
    return 100.0 * conteo["killed"] / evaluados


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimo", type=float, default=90.0, help="puntaje mínimo en %%")
    args = parser.parse_args(argv)

    proceso = subprocess.run(
        ["mutmut", "results", "--all", "true"], capture_output=True, text=True, check=False
    )
    conteo = contar_estados(proceso.stdout)
    if not conteo:
        print("No se encontraron resultados de mutmut: ¿se ejecutó `mutmut run`?")
        return 2
    sobrevivientes = conteo["survived"] + conteo["suspicious"] + conteo["no tests"]
    valor = puntaje(conteo)
    print(f"Mutantes: {dict(conteo)}")
    print(
        f"Puntaje de mutación: {valor:.1f} % (mínimo {args.minimo:.1f} %), sin matar: {sobrevivientes}"
    )
    if valor < args.minimo:
        print("FALLA: el puntaje de mutación está bajo el mínimo.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
