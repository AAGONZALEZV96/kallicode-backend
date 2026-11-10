"""Recolector de métricas de pruebas (KC-QA-B4-05).

Lee los dos reportes estándar que generan pytest y vitest y los consolida:

- JUnit XML (``--junitxml``): cuántas pruebas pasaron, fallaron, dieron error o se omitieron.
- Cobertura XML (``--cov-report=xml``): porcentaje de código cubierto.

Se usa ``defusedxml`` en vez de ``xml.etree`` para que un XML malicioso
(por ejemplo, con entidades recursivas) no pueda colgar el proceso.
"""

from pathlib import Path
from typing import TypedDict

from defusedxml.ElementTree import parse


class MetricasJUnit(TypedDict):
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_s: float


class MetricasConsolidadas(MetricasJUnit):
    coverage_pct: float | None


def leer_junit(ruta: str | Path) -> MetricasJUnit:
    """Suma los contadores de todas las <testsuite> de un reporte JUnit.

    pytest y vitest envuelven las suites en <testsuites>; otros reportadores
    escriben una sola <testsuite> como raíz. Se aceptan ambos formatos.
    """
    raiz = parse(ruta).getroot()
    suites = [raiz] if raiz.tag == "testsuite" else raiz.findall("testsuite")

    total = failed = errors = skipped = 0
    duracion = 0.0
    for suite in suites:
        total += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        duracion += float(suite.get("time", 0))

    return {
        "total": total,
        "passed": total - failed - errors - skipped,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "duration_s": round(duracion, 3),
    }


def leer_cobertura(ruta: str | Path) -> float:
    """Devuelve el % de cobertura de un reporte Cobertura XML, con 2 decimales.

    Si el reporte trae líneas y ramas (pytest --cov-branch), se combinan igual que
    el TOTAL que imprime pytest-cov: (líneas + ramas cubiertas) / (líneas + ramas).
    Si no, se usa line-rate (de 0 a 1) multiplicado por 100.
    """
    raiz = parse(ruta).getroot()
    validas = int(raiz.get("lines-valid", 0)) + int(raiz.get("branches-valid", 0))
    if validas > 0:
        cubiertas = int(raiz.get("lines-covered", 0)) + int(raiz.get("branches-covered", 0))
        return round(cubiertas / validas * 100, 2)
    return round(float(raiz.get("line-rate", 0)) * 100, 2)


def consolidar(
    ruta_junit: str | Path, ruta_cobertura: str | Path | None = None
) -> MetricasConsolidadas:
    """Une ambos reportes en un solo diccionario. La cobertura es opcional."""
    metricas = leer_junit(ruta_junit)
    cobertura = leer_cobertura(ruta_cobertura) if ruta_cobertura else None
    return MetricasConsolidadas(**metricas, coverage_pct=cobertura)
