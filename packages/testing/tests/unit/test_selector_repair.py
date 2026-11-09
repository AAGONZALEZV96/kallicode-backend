"""
Ticket: KC-QA-B4-03
Suite de pruebas unitarias y de resiliencia para SelectorRepairEngine (selector_repair.py).
Valida:
1. test_reparacion_selector_exitosa_con_reemplazo_localizado: Reemplazo acotado exclusivamente a line_number.
2. test_descarte_posicional_css_y_evaluacion_ordenada: Prohibición estricta de nth-child y jerarquías CSS (QA-F17).
3. test_escalado_a_revision_humana_bajo_umbral_confianza: Escalamiento a human_review con confidence < 0.8 (QA-F29).
4. test_restauracion_atomica_y_prevencion_path_traversal: Confinamiento seguro y rollback íntegro en finally (QA-F13, QA-F28).
5. test_retrocompatibilidad_suggest_new_selector: Preservación de contratos legados.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from kallicode_testing.selector_repair import (
    RepairRequestDTO,
    SelectorCandidateDTO,
    SelectorRepairEngine,
    is_valid_selector_pattern,
    suggest_new_selector,
)
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Prueba 1: test_reparacion_selector_exitosa_con_reemplazo_localizado
# ---------------------------------------------------------------------------
def test_reparacion_selector_exitosa_con_reemplazo_localizado(tmp_path: Path) -> None:
    """
    Prueba 1: Crear una spec con el mismo selector roto repetido en múltiples líneas.
    Solicitar reparación indicando line_number específico.
    Validaciones:
    - Solo se modifica la línea indicada por line_number (QA-F13).
    - Las demás apariciones del selector en otras líneas no se alteran.
    - El resultado retorna status == 'patched'.
    - Genera branch_name con el formato canónico repair/{slug}-{line_number}.
    """
    spec_file = tmp_path / "checkout.spec.ts"
    original_lines = [
        "import { test } from '@playwright/test';\n",
        "test('checkout flow', async ({ page }) => {\n",
        "  await page.goto('/checkout');\n",
        "  await page.click('#submit-btn');\n",  # Línea 4: Objetivo a reparar
        "  await page.waitForTimeout(500);\n",
        "  const isVisible = await page.isVisible('#submit-btn');\n",  # Línea 6: Repetido, no debe cambiar
        "});\n",
    ]
    spec_file.write_text("".join(original_lines), encoding="utf-8")

    dom_content = """
    <div class="checkout-panel">
        <button data-testid="checkout.btn-submit" role="button" aria-label="Confirmar">Enviar Pedido</button>
    </div>
    """

    request = RepairRequestDTO(
        spec_path="checkout.spec.ts",
        line_number=4,
        broken_selector="#submit-btn",
        step_intent="Confirmar pedido de compra",
        dom_excerpt=dom_content,
    )

    mock_executor = MagicMock(return_value=True)
    engine = SelectorRepairEngine(command_executor=mock_executor)

    result = engine.repair_selector(request, base_dir=tmp_path)

    assert result.status == "patched"
    assert result.repaired_selector == '[data-testid="checkout.btn-submit"]'
    assert result.applied_candidate is not None
    assert result.applied_candidate.strategy == "data-testid"
    assert result.branch_name == "repair/checkout-spec-4"

    # Verificar contenido del archivo en disco
    updated_lines = spec_file.read_text(encoding="utf-8").splitlines(keepends=True)
    # Línea 4 (índice 3) modificada con preservación de sangría
    assert "  await page.click('[data-testid=\"checkout.btn-submit\"]');\n" == updated_lines[3]
    # Línea 6 (índice 5) preserva el selector original sin cambios
    assert "  const isVisible = await page.isVisible('#submit-btn');\n" == updated_lines[5]

    mock_executor.assert_called_once()


# ---------------------------------------------------------------------------
# Prueba 2: test_descarte_posicional_css_y_evaluacion_ordenada
# ---------------------------------------------------------------------------
def test_descarte_posicional_css_y_evaluacion_ordenada() -> None:
    """
    Prueba 2: Simular propuesta con selectores posicionales CSS frágiles
    (nth-child, nth-of-type, combinadores jerárquicos) y de contrato.
    Validaciones:
    - is_valid_selector_pattern rechaza patrones posicionales (QA-F17).
    - propose_candidates descarta automáticamente alternativas posicionales.
    - Conserva alternativas robustas (data-testid, aria-role).
    """
    # Verificación directa de patrones posicionales prohibidos
    assert is_valid_selector_pattern("div > button:nth-child(3)") is False
    assert is_valid_selector_pattern("ul > li:nth-of-type(2)") is False
    assert is_valid_selector_pattern("table tr:first-child td") is False
    assert is_valid_selector_pattern("div > div > span") is False
    assert is_valid_selector_pattern('[data-testid="tickets.list.btn-create"]') is True
    assert is_valid_selector_pattern('[role="button"]') is True
    assert is_valid_selector_pattern('text="Aceptar"') is True

    # Simular LLM stub que propone mezcla de posicional y robusto
    mock_llm = MagicMock()
    mock_llm.propose_candidates.return_value = [
        SelectorCandidateDTO(
            selector="form > div:nth-child(2) > button:nth-child(1)",
            strategy="legacy-attr",
            confidence=0.99,
        ),
        SelectorCandidateDTO(
            selector='[data-testid="form.btn-save"]',
            strategy="data-testid",
            confidence=0.92,
        ),
    ]

    engine = SelectorRepairEngine(llm_stub=mock_llm)
    request = RepairRequestDTO(
        spec_path="test.spec.ts",
        line_number=10,
        broken_selector="#btn",
        step_intent="Guardar",
        dom_excerpt="<div></div>",
    )

    candidates, _ = engine.propose_candidates(request)

    # El candidato posicional debe haber sido eliminado completamente
    selectors = [c.selector for c in candidates]
    assert "form > div:nth-child(2) > button:nth-child(1)" not in selectors
    assert '[data-testid="form.btn-save"]' in selectors
    assert all(is_valid_selector_pattern(s) for s in selectors)


# ---------------------------------------------------------------------------
# Prueba 3: test_escalado_a_revision_humana_bajo_umbral_confianza
# ---------------------------------------------------------------------------
def test_escalado_a_revision_humana_bajo_umbral_confianza(tmp_path: Path) -> None:
    """
    Prueba 3: Simular candidatos con confianza inferior al umbral mínimo (0.8).
    Validaciones:
    - No se aplica parche en el archivo de prueba.
    - El archivo permanece estrictamente idéntico al original.
    - Retorno status == 'human_review'.
    - branch_name es None (QA-F29).
    """
    spec_file = tmp_path / "low_conf.spec.ts"
    initial_content = "await page.click('#legacy-id');\n"
    spec_file.write_text(initial_content, encoding="utf-8")

    mock_llm = MagicMock()
    mock_llm.propose_candidates.return_value = [
        SelectorCandidateDTO(
            selector='[name="legacy_name"]',
            strategy="legacy-attr",
            confidence=0.65,  # < 0.8
        ),
        SelectorCandidateDTO(
            selector='text="Maybe Button"',
            strategy="text-content",
            confidence=0.55,  # < 0.8
        ),
    ]

    mock_executor = MagicMock()
    engine = SelectorRepairEngine(
        llm_stub=mock_llm,
        command_executor=mock_executor,
        confidence_floor=0.80,
    )

    request = RepairRequestDTO(
        spec_path="low_conf.spec.ts",
        line_number=1,
        broken_selector="#legacy-id",
        step_intent="Acción incierta",
        dom_excerpt="<div><span>Maybe Button</span></div>",
    )

    result = engine.repair_selector(request, base_dir=tmp_path)

    assert result.status == "human_review"
    assert result.repaired_selector is None
    assert result.applied_candidate is None
    assert result.branch_name is None
    assert spec_file.read_text(encoding="utf-8") == initial_content

    # No debe invocarse el ejecutor para candidatos bajo el umbral de confianza
    mock_executor.assert_not_called()


# ---------------------------------------------------------------------------
# Prueba 4: test_restauracion_atomica_y_prevencion_path_traversal
# ---------------------------------------------------------------------------
def test_restauracion_atomica_y_prevencion_path_traversal(tmp_path: Path) -> None:
    """
    Prueba 4: Validar prevención de Path Traversal y rollback atómico en finally.
    Validaciones:
    - resolve_safe_path bloquea rutas con '../' o inyecciones fuera de base_dir (QA-F03).
    - Ante un fallo o excepción en command_executor, el bloque finally restaura
      el archivo original y elimina cualquier archivo .bak (QA-F13, QA-F28).
    """
    safe_dir = tmp_path / "specs"
    safe_dir.mkdir()

    spec_file = safe_dir / "flaky_spec.ts"
    initial_code = "const btn = '#broken';\nawait page.click(btn);\nconsole.log('done');\n"
    spec_file.write_text(initial_code, encoding="utf-8")

    engine = SelectorRepairEngine()

    # Caso A: Intento de Path Traversal con retroceso
    request_traversal = RepairRequestDTO(
        spec_path="../../etc/passwd",
        line_number=1,
        broken_selector="root",
        step_intent="Exploit",
        dom_excerpt="<div/>",
    )
    with pytest.raises(ValueError, match="Path-Traversal.*detectado"):
        engine.repair_selector(request_traversal, base_dir=safe_dir)

    # Caso B: Fallo con excepción durante la ejecución de validación
    def failing_executor(cmd: list[str]) -> bool:
        raise RuntimeError("Subprocess timeout crashed unexpectedly")

    failing_engine = SelectorRepairEngine(command_executor=failing_executor)
    valid_request = RepairRequestDTO(
        spec_path="flaky_spec.ts",
        line_number=1,
        broken_selector="#broken",
        step_intent="Click botón",
        dom_excerpt='<button data-testid="repaired-btn">Click</button>',
    )

    result_fail = failing_engine.repair_selector(valid_request, base_dir=safe_dir)

    assert result_fail.status == "human_review"
    # El archivo original debe haberse restaurado completamente a su estado previo
    assert spec_file.read_text(encoding="utf-8") == initial_code

    # El archivo .bak no debe quedar residual en disco
    bak_file = spec_file.with_suffix(spec_file.suffix + ".bak")
    assert not bak_file.exists()


# ---------------------------------------------------------------------------
# Prueba 5: test_retrocompatibilidad_suggest_new_selector
# ---------------------------------------------------------------------------
def test_retrocompatibilidad_suggest_new_selector() -> None:
    """
    Prueba 5: Asegura que la función legada suggest_new_selector preserve
    su contrato previo para satisfacer test_testing_module.py.
    """
    # 1. Selector que aún existe en el DOM
    dom_existing = "<button id='submit'>Enviar</button>"
    assert suggest_new_selector("#submit", dom_existing) == "#submit"

    # 2. Selector no presente pero existe data-testid
    dom_testid = "<button data-testid='repaired'>Enviar</button>"
    assert suggest_new_selector("#btn-submit", dom_testid) == "[data-testid='repaired']"

    # 3. Validaciones de entradas vacías
    with pytest.raises(ValueError, match="El selector y el DOM no pueden estar vacíos"):
        suggest_new_selector("", "<div></div>")

    with pytest.raises(ValueError, match="El selector y el DOM no pueden estar vacíos"):
        suggest_new_selector("#btn", "")


# ---------------------------------------------------------------------------
# Prueba 6: test_casos_borde_validacion_y_errores_especificacion
# ---------------------------------------------------------------------------
def test_casos_borde_validacion_y_errores_especificacion(tmp_path: Path) -> None:
    """
    Prueba 6: Casos de borde y robustez operativa:
    - line_number fuera de rango lanza ValueError.
    - Selector roto ausente en la línea indicada retorna False.
    - Archivo de prueba inexistente lanza FileNotFoundError.
    - Inmutabilidad de DTOs frozen.
    - Soporte de ejecutores con exit_code y retorno int.
    """
    spec_file = tmp_path / "edge_case.spec.ts"
    spec_file.write_text("line 1\nline 2\nline 3\n", encoding="utf-8")

    engine = SelectorRepairEngine()

    # line_number fuera de rango
    with pytest.raises(ValueError, match="line_number.*fuera de rango"):
        engine.validate_candidate(spec_file, 99, "broken", "repaired")

    with pytest.raises(ValueError, match="line_number.*fuera de rango"):
        engine.validate_candidate(spec_file, 0, "broken", "repaired")

    # broken no está en la línea target
    assert engine.validate_candidate(spec_file, 2, "not_present", "repaired") is False

    # Archivo inexistente en validate_candidate
    missing_file = tmp_path / "non_existent.spec.ts"
    with pytest.raises(FileNotFoundError, match="Archivo de spec no encontrado"):
        engine.validate_candidate(missing_file, 1, "a", "b")

    # Archivo inexistente en repair_selector
    missing_req = RepairRequestDTO(
        spec_path="non_existent.spec.ts",
        line_number=1,
        broken_selector="#a",
        step_intent="Missing",
        dom_excerpt="<div/>",
    )
    with pytest.raises(FileNotFoundError, match="Archivo de prueba no encontrado"):
        engine.repair_selector(missing_req, base_dir=tmp_path)

    # Inmutabilidad DTO (frozen=True)
    cand = SelectorCandidateDTO(selector="#a", strategy="legacy-attr", confidence=0.9)
    with pytest.raises(ValidationError):
        cand.confidence = 0.5

    # Ejecutor con objeto exit_code y entero
    mock_result_ok = MagicMock()
    mock_result_ok.exit_code = 0
    engine_ok = SelectorRepairEngine(command_executor=lambda cmd: mock_result_ok)
    assert engine_ok._execute_test(spec_file) is True

    mock_result_fail = MagicMock()
    mock_result_fail.exit_code = 1
    engine_fail = SelectorRepairEngine(command_executor=lambda cmd: mock_result_fail)
    assert engine_fail._execute_test(spec_file) is False

    engine_int_zero = SelectorRepairEngine(command_executor=lambda cmd: 0)
    assert engine_int_zero._execute_test(spec_file) is True

    engine_int_err = SelectorRepairEngine(command_executor=lambda cmd: 1)
    assert engine_int_err._execute_test(spec_file) is False
