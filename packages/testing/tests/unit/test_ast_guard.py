"""
Ticket: KC-QA-B3-03
Suite de pruebas unitarias y adversarias para ASTGuard (ast_guard.py).
Valida código Pytest estándar con @criterio("AC-01"), detección de SyntaxError,
bloqueo determinista de importaciones/llamadas peligrosas (QA-F04) y estructura E2E Playwright.
"""

from __future__ import annotations

import pytest
from kallicode_testing.ast_guard import ASTGuard


def test_validacion_codigo_python_valido() -> None:
    """
    Prueba 1: Valida un test Pytest estándar que contiene assert y el decorador
    @criterio("AC-01"), esperando is_valid is True y errors == [].
    """
    guard = ASTGuard()
    valid_python_code = (
        "from kallicode_testing.gen_tests import criterio\n\n"
        '@criterio("AC-01")\n'
        "def test_creacion_ticket_exitoso() -> None:\n"
        "    resultado = {'status': 201}\n"
        "    assert resultado['status'] == 201\n"
    )

    is_valid, errors = guard.validate(valid_python_code, target_kind="unit")
    assert is_valid is True
    assert errors == []

    # Validar también función asíncrona async def test_*
    valid_async_code = (
        '@criterio("AC-01")\nasync def test_flujo_asincrono() -> None:\n    assert 1 + 1 == 2\n'
    )
    is_valid_async, errors_async = guard.validate_python_ast(valid_async_code)
    assert is_valid_async is True
    assert errors_async == []


def test_deteccion_syntax_error_python() -> None:
    """
    Prueba 2: Valida la detección de código Python malformado ('def test_foo(: assert True'),
    verificando is_valid is False y el mensaje descriptivo con línea y SyntaxError.
    """
    guard = ASTGuard()
    malformed_code = "def test_foo(: assert True\n"

    is_valid, errors = guard.validate(malformed_code, target_kind="unit")
    assert is_valid is False
    assert len(errors) == 1
    assert "SyntaxError: line 1:" in errors[0]

    # Validar también rechazo de código vacío o sin función test_*
    empty_valid, empty_errors = guard.validate("   ", target_kind="unit")
    assert empty_valid is False
    assert len(empty_errors) == 1

    no_test_valid, no_test_errors = guard.validate_python_ast(
        "def helper() -> int:\n    return 1\n"
    )
    assert no_test_valid is False
    assert any("def test_*" in err for err in no_test_errors)


@pytest.mark.parametrize(
    "malicious_snippet, expected_keyword",
    [
        ("import os\ndef test_a():\n    assert True\n", "os"),
        ("import subprocess\ndef test_b():\n    assert True\n", "subprocess"),
        ("from socket import socket\ndef test_c():\n    assert True\n", "socket"),
        ("from os import system\ndef test_d():\n    assert True\n", "os"),
        ("def test_e():\n    eval('1+1')\n", "eval"),
        ("def test_f():\n    exec('x=1')\n", "exec"),
        ("def test_g():\n    __import__('sys')\n", "__import__"),
        ("def test_h():\n    os.system('id')\n", "system"),
        ("def test_i():\n    subprocess.Popen(['ls'])\n", "subprocess.Popen"),
    ],
)
def test_bloqueo_imports_y_llamadas_peligrosas(
    malicious_snippet: str,
    expected_keyword: str,
) -> None:
    """
    Prueba 3: Valida protección frente a import os, import subprocess, eval("1+1"),
    exec(...), __import__("sys") e invocaciones sobre módulos bloqueados.
    """
    guard = ASTGuard()
    is_valid, errors = guard.validate(malicious_snippet, target_kind="unit")

    assert is_valid is False
    assert len(errors) > 0, "Debe reportar al menos un error de seguridad AST."
    assert any(expected_keyword in err for err in errors), (
        f"Se esperaba mención a '{expected_keyword}' en los errores: {errors}"
    )


def test_validacion_estructura_playwright_e2e() -> None:
    """
    Prueba 4: Valida un spec Playwright E2E bien formado contra escenarios con llaves
    desbalanceadas, ausencia de test(...)/expect(...) y llamadas peligrosas en Node.js.
    """
    guard = ASTGuard()

    valid_ts_spec = (
        'import { test, expect } from "@playwright/test";\n\n'
        'test("AC-01 aprobacion gate", async ({ page }) => {\n'
        '  await page.goto("/gates");\n'
        '  await expect(page.locator("[data-testid=\\"btn-approve\\"]")).toBeVisible();\n'
        "});\n"
    )
    is_valid, errors = guard.validate(valid_ts_spec, target_kind="e2e")
    assert is_valid is True
    assert errors == []

    # Caso inválido 1: llaves desbalanceadas
    unbalanced_ts = 'test("AC-01", async ({ page }) => {\n  await page.goto("/");\n'
    valid_unbalanced, err_unbalanced = guard.validate(unbalanced_ts, target_kind="e2e")
    assert valid_unbalanced is False
    assert any("Desbalanceo" in e for e in err_unbalanced)

    # Caso inválido 2: ausencia de test(...) o expect(...)
    no_test_ts = "const x = () => { return 42; };"
    valid_no_test, err_no_test = guard.validate(no_test_ts, target_kind="e2e")
    assert valid_no_test is False
    assert any("test(...)" in e for e in err_no_test)

    # Caso inválido 3: patrones peligrosos (child_process, execSync, process.exit)
    dangerous_ts = (
        'import { execSync } from "child_process";\n'
        'test("AC-01", async ({ page }) => {\n'
        '  execSync("whoami");\n'
        "  process.exit(1);\n"
        "});\n"
    )
    valid_dangerous, err_dangerous = guard.validate(dangerous_ts, target_kind="e2e")
    assert valid_dangerous is False
    assert any("child_process" in e for e in err_dangerous)
    assert any("execSync" in e for e in err_dangerous)
    assert any("process.exit" in e for e in err_dangerous)
