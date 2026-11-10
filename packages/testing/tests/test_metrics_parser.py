"""Pruebas del recolector de métricas JUnit + Cobertura (KC-QA-B4-05)."""

from pathlib import Path

from kallicode_testing.metrics_parser import consolidar, leer_cobertura, leer_junit

JUNIT_PYTEST = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests">
  <testsuite name="pytest" tests="10" failures="1" errors="1" skipped="2" time="1.5" />
</testsuites>
"""

JUNIT_VARIAS_SUITES = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="a" tests="4" failures="0" errors="0" skipped="0" time="0.25" />
  <testsuite name="b" tests="3" failures="1" errors="0" skipped="1" time="0.5" />
</testsuites>
"""

COBERTURA_CON_RAMAS = """<?xml version="1.0" ?>
<coverage line-rate="0.9" branch-rate="0.75" lines-valid="100" lines-covered="90"
          branches-valid="20" branches-covered="15" version="7.6" timestamp="0">
</coverage>
"""

COBERTURA_SOLO_LINEAS = """<?xml version="1.0" ?>
<coverage line-rate="0.8235" branch-rate="0" version="1">
</coverage>
"""


def _escribir(tmp_path: Path, nombre: str, contenido: str) -> Path:
    ruta = tmp_path / nombre
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def test_junit_pytest_cuenta_cada_resultado(tmp_path: Path) -> None:
    """Las aprobadas son el total menos fallidas, errores y omitidas."""
    metricas = leer_junit(_escribir(tmp_path, "junit.xml", JUNIT_PYTEST))
    assert metricas == {
        "total": 10,
        "passed": 6,
        "failed": 1,
        "errors": 1,
        "skipped": 2,
        "duration_s": 1.5,
    }


def test_junit_suma_varias_suites(tmp_path: Path) -> None:
    """Con varias <testsuite> (como vitest) se suman todas."""
    metricas = leer_junit(_escribir(tmp_path, "junit.xml", JUNIT_VARIAS_SUITES))
    assert metricas["total"] == 7
    assert metricas["passed"] == 5
    assert metricas["duration_s"] == 0.75


def test_cobertura_combina_lineas_y_ramas(tmp_path: Path) -> None:
    """(90 + 15) / (100 + 20) = 87,5 %: el mismo cálculo que el TOTAL de pytest-cov."""
    ruta = _escribir(tmp_path, "coverage.xml", COBERTURA_CON_RAMAS)
    assert leer_cobertura(ruta) == 87.5


def test_cobertura_solo_line_rate(tmp_path: Path) -> None:
    """Sin contadores de líneas, line-rate 0.8235 se mapea exacto a 82,35 %."""
    ruta = _escribir(tmp_path, "coverage.xml", COBERTURA_SOLO_LINEAS)
    assert leer_cobertura(ruta) == 82.35


def test_consolidar_une_ambos_reportes(tmp_path: Path) -> None:
    """El resultado final trae los contadores de JUnit y el % de cobertura."""
    junit = _escribir(tmp_path, "junit.xml", JUNIT_PYTEST)
    cobertura = _escribir(tmp_path, "coverage.xml", COBERTURA_CON_RAMAS)
    metricas = consolidar(junit, cobertura)
    assert metricas["passed"] == 6
    assert metricas["coverage_pct"] == 87.5


def test_consolidar_sin_cobertura(tmp_path: Path) -> None:
    """Si no hay reporte de cobertura, coverage_pct queda en None."""
    metricas = consolidar(_escribir(tmp_path, "junit.xml", JUNIT_PYTEST))
    assert metricas["coverage_pct"] is None
