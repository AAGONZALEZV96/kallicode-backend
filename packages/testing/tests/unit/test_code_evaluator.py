"""
Ticket: KC-QA-B3-09
Suite de pruebas unitarias y adversarias para CodeEvaluator (code_evaluator.py).
Verifica:
1. Evaluación exitosa de código limpio en Python (Pytest) y TypeScript (Playwright).
2. Detección de violaciones de estilo en Python (F401 import sin uso, E501 línea > 100, F841 variable sin uso).
3. Generación de formatted_feedback estructurado con línea, código, explicación y sugerencia para el LLM.
4. Prevención de Path Traversal mediante resolve_safe_path al evaluar archivos en disco.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.code_evaluator import CodeEvaluator, EvaluationResult


def test_evaluacion_codigo_limpio_python_y_ts() -> None:
    """
    Prueba 1: Evalúa código Python válido y código TypeScript/Playwright válido
    (<= 100 caracteres, sin errores de estilo ni infracciones), verificando
    is_clean is True e issues == [].
    """
    evaluator = CodeEvaluator(line_length_limit=100)

    clean_python = (
        "from kallicode_testing.gen_tests import criterio\n\n\n"
        '@criterio("AC-01")\n'
        "def test_autenticacion_valida() -> None:\n"
        '    expected_status = "ok"\n'
        '    assert expected_status == "ok"\n'
    )
    py_result = evaluator.evaluate_code(clean_python, target_kind="unit")
    assert isinstance(py_result, EvaluationResult)
    assert py_result.is_clean is True
    assert py_result.issues == []
    assert py_result.formatted_feedback == ""

    clean_ts = (
        'import { test, expect } from "@playwright/test";\n\n'
        'test("AC-01 flujo login", async ({ page }) => {\n'
        '  await page.goto("/login");\n'
        "  const btn = page.locator('[data-testid=\"login.form.btn-submit\"]');\n"
        "  await expect(btn).toBeVisible();\n"
        "});\n"
    )
    ts_result = evaluator.evaluate_code(clean_ts, target_kind="e2e")
    assert ts_result.is_clean is True
    assert ts_result.issues == []
    assert ts_result.formatted_feedback == ""


def test_deteccion_violaciones_estilo_python() -> None:
    """
    Prueba 2: Valida la detección de:
    - import os / import json sin uso (F401 y S101 para os)
    - línea > 120 caracteres (E501)
    - variable local sin referencias (F841)
    - función de prueba vacía (PT01)
    """
    evaluator = CodeEvaluator(line_length_limit=100)

    long_comment = "# " + ("x" * 125)
    dirty_python = (
        "import os\n"
        "import json\n\n"
        f"{long_comment}\n"
        "def test_con_violaciones() -> None:\n"
        "    unused_variable = 42\n"
        "    valid_value = 10\n"
        "    assert valid_value == 10\n\n"
        "def test_vacio() -> None:\n"
        "    pass\n"
    )

    result = evaluator.evaluate_python_style(dirty_python)
    assert result.is_clean is False
    assert len(result.issues) > 0

    codes = {issue.code for issue in result.issues}
    assert "F401" in codes, f"Se esperaba F401 en {codes}"
    assert "E501" in codes, f"Se esperaba E501 en {codes}"
    assert "F841" in codes, f"Se esperaba F841 en {codes}"
    assert "S101" in codes, f"Se esperaba S101 (import os prohibido) en {codes}"
    assert "PT01" in codes, f"Se esperaba PT01 (función vacía) en {codes}"


def test_generacion_formatted_feedback_para_llm() -> None:
    """
    Prueba 3: Verifica que formatted_feedback incluya la línea afectada, el código
    de error, la explicación y la sugerencia de corrección tanto para Python como TS.
    """
    evaluator = CodeEvaluator(line_length_limit=100)

    code_with_issues = (
        "import math\n\ndef test_calculo() -> None:\n    sin_uso = 99\n    assert 2 + 2 == 4\n"
    )
    py_res = evaluator.evaluate_code(code_with_issues, target_kind="api")
    assert py_res.is_clean is False
    feedback = py_res.formatted_feedback

    assert "Línea 1" in feedback
    assert "[F401]" in feedback
    assert "math" in feedback
    assert "Sugerencia:" in feedback
    assert "[F841]" in feedback
    assert "sin_uso" in feedback

    # Verificar también infracciones de estilo TypeScript (TS02 console.log, TS03 any, A11Y01)
    dirty_ts = (
        'import { test, expect } from "@playwright/test";\n'
        'test("AC-02", async ({ page }) => {\n'
        "  const payload: any = {};\n"
        '  console.log("debug", payload);\n'
        '  await expect(page.locator(".btn-primary-fragil")).toBeVisible();\n'
        "});\n"
    )
    ts_res = evaluator.evaluate_typescript_style(dirty_ts)
    assert ts_res.is_clean is False
    ts_codes = {issue.code for issue in ts_res.issues}
    assert {"TS02", "TS03", "A11Y01"}.issubset(ts_codes)
    assert "[TS02]" in ts_res.formatted_feedback
    assert "[TS03]" in ts_res.formatted_feedback
    assert "[A11Y01]" in ts_res.formatted_feedback


def test_prevencion_path_traversal_al_evaluar_archivo(tmp_path: Path) -> None:
    """
    Prueba 4: Intenta evaluar archivos temporales fuera del directorio autorizado
    mediante evaluate_file y verifica el bloqueo con ValueError por resolve_safe_path.
    """
    evaluator = CodeEvaluator(allowed_base_dir=tmp_path)

    valid_file = tmp_path / "test_ok.py"
    valid_file.write_text(
        "def test_ok() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    ok_res = evaluator.evaluate_file(valid_file)
    assert ok_res.is_clean is True

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        evaluator.evaluate_file("../../etc/passwd")

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        evaluator.evaluate_file("/etc/passwd")

    with pytest.raises(FileNotFoundError):
        evaluator.evaluate_file(tmp_path / "inexistente.py")

    with pytest.raises(ValueError, match="target_kind no soportado"):
        evaluator.evaluate_code("def test_a(): assert True", target_kind="unknown")
