"""Suite unitaria de autoevaluación para los componentes core del módulo testing."""

import pytest
from kallicode_testing.gen_tests import format_test_code
from kallicode_testing.run_suite import parse_test_results
from kallicode_testing.selector_repair import suggest_new_selector


# --- Tests para gen_tests.py ---
def test_gen_tests_format_code_with_prefix() -> None:
    """Verifica que el generador formatee correctamente y respete el prefijo test_."""
    result = format_test_code("login", "assert True")
    assert result == "def test_login() -> None:\n    assert True\n"


def test_gen_tests_format_code_already_prefixed() -> None:
    """Verifica que no duplique el prefijo test_ si ya existe."""
    result = format_test_code("test_auth", "pass")
    assert result == "def test_auth() -> None:\n    pass\n"


# --- Tests para run_suite.py ---
def test_run_suite_parse_success() -> None:
    """Verifica el parseo de una ejecución exitosa."""
    result = parse_test_results("1 passed in 0.01s")
    assert result["passed"] == 1
    assert result["failed"] == 0


def test_run_suite_parse_failure() -> None:
    """Verifica el parseo cuando la suite contiene fallos."""
    result = parse_test_results("FAILED tests/test_login.py")
    assert result["failed"] == 1


# --- Tests para selector_repair.py ---
def test_selector_repair_not_broken() -> None:
    """Verifica que retorne el mismo selector si aún existe en el DOM fragmentado."""
    dom = "<button id='submit'>Enviar</button>"
    assert suggest_new_selector("#submit", dom) == "#submit"


def test_selector_repair_heuristic_fallback() -> None:
    """Verifica que el motor de auto-healing aplique el fallback a data-testid."""
    dom = "<button data-testid='repaired'>Enviar</button>"
    assert suggest_new_selector("#btn-submit", dom) == "[data-testid='repaired']"


def test_selector_repair_empty_values() -> None:
    """Verifica el manejo de errores (ValueErrors) ante entradas inválidas."""
    with pytest.raises(ValueError, match="no pueden estar vacíos"):
        suggest_new_selector("", "<html/>")
