"""Pruebas de la compuerta de mutación (scripts/verificar_mutacion.py)."""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

RUTA = Path(__file__).resolve().parents[1] / "scripts" / "verificar_mutacion.py"


@pytest.fixture(scope="module")
def gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verificar_mutacion", RUTA)
    assert spec is not None
    assert spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


SALIDA = """\
    kallicode_codemapping.budget.x_a__mutmut_1: killed
    kallicode_codemapping.budget.x_a__mutmut_2: killed
    kallicode_codemapping.budget.x_a__mutmut_3: killed
    kallicode_codemapping.budget.x_a__mutmut_4: survived
    kallicode_codemapping.budget.x_b__mutmut_1: no tests
    kallicode_codemapping.budget.x_b__mutmut_2: skipped
"""


def test_cuenta_mutantes_por_estado(gate: ModuleType) -> None:
    assert gate.contar_estados(SALIDA) == Counter(
        {"killed": 3, "survived": 1, "no tests": 1, "skipped": 1}
    )


def test_puntaje_excluye_los_omitidos(gate: ModuleType) -> None:
    assert gate.puntaje(gate.contar_estados(SALIDA)) == pytest.approx(60.0)


def test_puntaje_sin_mutantes_es_cero(gate: ModuleType) -> None:
    assert gate.puntaje(Counter()) == 0.0


def _con_salida(texto: str) -> MagicMock:
    return MagicMock(stdout=texto)


def test_main_aprueba_si_alcanza_el_minimo(gate: ModuleType) -> None:
    with patch.object(gate.subprocess, "run", return_value=_con_salida(SALIDA)):
        assert gate.main(["--minimo", "60"]) == 0


def test_main_falla_si_el_puntaje_es_menor(gate: ModuleType) -> None:
    with patch.object(gate.subprocess, "run", return_value=_con_salida(SALIDA)):
        assert gate.main(["--minimo", "61"]) == 1


def test_main_avisa_si_no_hay_resultados(gate: ModuleType) -> None:
    with patch.object(gate.subprocess, "run", return_value=_con_salida("")):
        assert gate.main([]) == 2
