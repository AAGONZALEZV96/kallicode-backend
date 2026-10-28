"""Orquestador de ejecución de suites de prueba local."""

from typing import TypedDict


class SuiteResult(TypedDict):
    passed: int
    failed: int
    duration_ms: float


def parse_test_results(raw_output: str) -> SuiteResult:
    """Parsea la salida simulada de pytest a un diccionario estructurado."""
    if "FAILED" in raw_output:
        return {"passed": 0, "failed": 1, "duration_ms": 15.0}
    return {"passed": 1, "failed": 0, "duration_ms": 10.5}
