"""Compuerta de cobertura por ámbito de CodeMapping (KC-CM-12-2, criterio 2).

La compuerta global (`--cov-fail-under`) no basta: un módulo crítico puede quedar por debajo del
objetivo y taparse con el resto. Este script lee el `coverage.xml` que deja pytest-cov y exige el
mínimo en cada ámbito, con la misma fórmula que `coverage report` con `--cov-branch`:
(líneas cubiertas + ramas cubiertas) / (líneas + ramas).

    uv run python packages/codemapping/scripts/verificar_cobertura_modulos.py coverage.xml --minimo 90
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Qué cuenta como cada ámbito del criterio (decidido en KC-CM-12-2).
AMBITOS: dict[str, tuple[str, ...]] = {
    "consulta": ("cm_queries.py", "consulta_grafo.py", "recuperacion.py"),
    "cargador": ("graph_loader.py",),
    "aislamiento": ("cm_db.py", "security.py"),
}
PATRON_RAMAS = re.compile(r"\((\d+)/(\d+)\)")


@dataclass
class Cobertura:
    lineas: int = 0
    lineas_cubiertas: int = 0
    ramas: int = 0
    ramas_cubiertas: int = 0

    def sumar(self, otra: Cobertura) -> None:
        self.lineas += otra.lineas
        self.lineas_cubiertas += otra.lineas_cubiertas
        self.ramas += otra.ramas
        self.ramas_cubiertas += otra.ramas_cubiertas

    @property
    def porcentaje(self) -> float:
        total = self.lineas + self.ramas
        return (
            100.0 if total == 0 else 100.0 * (self.lineas_cubiertas + self.ramas_cubiertas) / total
        )


def leer_coverage_xml(ruta: Path) -> dict[str, Cobertura]:
    """Cobertura por nombre de archivo del paquete kallicode_codemapping."""
    por_archivo: dict[str, Cobertura] = {}
    for clase in ET.parse(ruta).getroot().iter("class"):
        nombre = clase.get("filename", "").replace("\\", "/")
        if "kallicode_codemapping/" not in nombre:
            continue
        cob = Cobertura()
        for linea in clase.iter("line"):
            cob.lineas += 1
            if int(linea.get("hits", "0")) > 0:
                cob.lineas_cubiertas += 1
            if linea.get("branch") == "true":
                m = PATRON_RAMAS.search(linea.get("condition-coverage", ""))
                if m:
                    cob.ramas_cubiertas += int(m.group(1))
                    cob.ramas += int(m.group(2))
        por_archivo[nombre.rsplit("/", 1)[-1]] = cob
    return por_archivo


def evaluar(por_archivo: dict[str, Cobertura], minimo: float) -> list[str]:
    """Una línea por ámbito; las que empiezan por 'FALLA' hacen fallar la compuerta."""
    informe: list[str] = []
    for ambito, modulos in AMBITOS.items():
        faltan = [m for m in modulos if m not in por_archivo]
        if faltan:
            informe.append(f"FALLA {ambito}: sin datos de cobertura para {', '.join(faltan)}")
            continue
        total = Cobertura()
        for m in modulos:
            total.sumar(por_archivo[m])
        detalle = ", ".join(f"{m} {por_archivo[m].porcentaje:.1f} %" for m in modulos)
        estado = "OK   " if total.porcentaje >= minimo else "FALLA"
        informe.append(
            f"{estado} {ambito}: {total.porcentaje:.1f} % (mínimo {minimo:g} %) -- {detalle}"
        )
    return informe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("coverage_xml", type=Path)
    parser.add_argument("--minimo", type=float, default=90.0)
    args = parser.parse_args(argv)
    informe = evaluar(leer_coverage_xml(args.coverage_xml), args.minimo)
    print("\n".join(informe))
    return 1 if any(linea.startswith("FALLA") for linea in informe) else 0


if __name__ == "__main__":
    sys.exit(main())
