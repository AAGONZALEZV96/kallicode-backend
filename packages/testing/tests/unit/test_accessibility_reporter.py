"""Pruebas unitarias para AccessibilityReporter y DTOs de accesibilidad WCAG.

Ticket: KC-QA-B5-03
Épica: EQA5 - Motor de Pruebas Autónomo y E2E
"""

import json
from pathlib import Path
from typing import Any

import pytest
from kallicode_testing.accessibility_reporter import (
    AccessibilityReporter,
    AccessibilitySummaryDTO,
    AccessibilityViolationDTO,
    AccessibilityViolationError,
)
from pydantic import ValidationError


@pytest.fixture
def axe_raw_results_limpio() -> dict[str, Any]:
    """Resultados sintéticos de escaneo axe-core sin violaciones."""
    return {
        "url": "http://localhost:3000/login",
        "timestamp": "2026-11-09T12:00:00.000Z",
        "passes": [
            {"id": "color-contrast", "impact": None, "tags": ["wcag2aa", "wcag143"]},
            {"id": "label", "impact": None, "tags": ["wcag2a", "wcag412"]},
        ],
        "violations": [],
        "incomplete": [],
        "inapplicable": [{"id": "audio-caption", "impact": None}],
    }


@pytest.fixture
def axe_raw_results_con_fallos() -> dict[str, Any]:
    """Resultados sintéticos con violaciones de diversos impactos."""
    return {
        "url": "http://localhost:3000/nuevo-ticket",
        "timestamp": "2026-11-09T12:05:00.000Z",
        "passes": [{"id": "page-has-heading-one"}],
        "violations": [
            {
                "id": "color-contrast",
                "impact": "serious",
                "description": "Ensures the contrast between foreground and background colors meets WCAG 2 AA minimum thresholds",
                "help": "Elements must meet minimum color contrast ratio thresholds",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/color-contrast",
                "tags": ["wcag2aa", "wcag143"],
                "nodes": [
                    {
                        "html": '<button class="btn-subtle">Cancelar</button>',
                        "target": ["#btn-cancel"],
                        "failureSummary": "Fix any of the following: Element has insufficient color contrast of 2.5",
                    }
                ],
            },
            {
                "id": "button-name",
                "impact": "critical",
                "description": "Ensures buttons have discernible text",
                "help": "Buttons must have discernible text",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/button-name",
                "tags": ["wcag2a", "wcag412"],
                "nodes": [
                    {
                        "html": '<button class="icon-only"><span class="icon"></span></button>',
                        "target": ["#btn-icon", ".action-bar"],
                        "failureSummary": "Fix all of the following: Element does not have inner text that is visible to screen readers",
                    }
                ],
            },
            {
                "id": "landmark-complementary-is-top-level",
                "impact": "moderate",
                "description": "Ensures complementary landmark is top level",
                "help": "Complementary landmarks must be top level",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/landmark",
                "tags": ["wcag2aa"],
                "nodes": [],
            },
            {
                "id": "focus-order-semantics",
                "impact": "minor",
                "description": "Ensures elements in the focus order have an appropriate role",
                "help": "Elements in focus order must have appropriate role",
                "helpUrl": "https://dequeuniversity.com/rules/axe/4.10/focus-order",
                "tags": ["wcag2a"],
                "nodes": [],
            },
        ],
        "incomplete": [{"id": "color-contrast-enhanced"}],
        "inapplicable": [],
    }


# ---------------------------------------------------------------------------
# Tests de Parseo y Construcción de DTOs
# ---------------------------------------------------------------------------
def test_parse_axe_results_sin_violaciones(axe_raw_results_limpio: dict[str, Any]) -> None:
    """Verifica el parseo correcto de resultados limpios."""
    summary = AccessibilityReporter.parse_axe_results(axe_raw_results_limpio)

    assert summary.url == "http://localhost:3000/login"
    assert summary.violations_count == 0
    assert summary.critical_count == 0
    assert summary.serious_count == 0
    assert summary.moderate_count == 0
    assert summary.minor_count == 0
    assert summary.passes_count == 2
    assert summary.inapplicable_count == 1
    assert summary.incomplete_count == 0
    assert summary.sha256_hash is not None
    assert len(summary.sha256_hash) == 64


def test_parse_axe_results_con_clasificacion_de_impactos(
    axe_raw_results_con_fallos: dict[str, Any],
) -> None:
    """Verifica la clasificación adecuada de violaciones según su severidad."""
    summary = AccessibilityReporter.parse_axe_results(axe_raw_results_con_fallos)

    assert summary.violations_count == 4
    assert summary.critical_count == 1
    assert summary.serious_count == 1
    assert summary.moderate_count == 1
    assert summary.minor_count == 1
    assert "wcag2a" in summary.wcag_tags
    assert "wcag2aa" in summary.wcag_tags

    crit_v = next(v for v in summary.violations if v.impact == "critical")
    assert crit_v.id == "button-name"
    assert len(crit_v.nodes) == 1
    assert crit_v.nodes[0].target == ["#btn-icon", ".action-bar"]
    assert crit_v.nodes[0].failure_summary is not None


def test_inmutabilidad_summary_dto(axe_raw_results_limpio: dict[str, Any]) -> None:
    """Certifica que los DTOs de accesibilidad son inmutables (frozen=True)."""
    summary = AccessibilityReporter.parse_axe_results(axe_raw_results_limpio)

    with pytest.raises(ValidationError):
        # Intento de mutación directa sobre DTO frozen
        summary.critical_count = 5


# ---------------------------------------------------------------------------
# Tests de Aserción y Compuertas de Calidad
# ---------------------------------------------------------------------------
def test_assert_zero_critical_violations_exitoso(
    axe_raw_results_limpio: dict[str, Any],
) -> None:
    """La compuerta no debe emitir excepción si no hay violaciones críticas ni serias."""
    summary = AccessibilityReporter.parse_axe_results(axe_raw_results_limpio)
    # No debe levantar AccessibilityViolationError
    AccessibilityReporter.assert_zero_critical_violations(summary)


def test_assert_permite_violaciones_menores_y_moderadas() -> None:
    """Permite violaciones minor o moderate sin romper el build si critical y serious son 0."""
    dto = AccessibilitySummaryDTO(
        url="http://localhost:3000/auditoria",
        scanned_at="2026-11-09T12:00:00Z",
        violations_count=2,
        critical_count=0,
        serious_count=0,
        moderate_count=1,
        minor_count=1,
        passes_count=10,
        incomplete_count=0,
        inapplicable_count=0,
        violations=[
            AccessibilityViolationDTO(
                id="link-in-text-block",
                impact="moderate",
                description="Links should be distinguishable without relying on color",
                help="Distinguish links without color",
                nodes=[],
            )
        ],
    )
    AccessibilityReporter.assert_zero_critical_violations(dto)


def test_assert_zero_critical_violations_lanza_excepcion_critica(
    axe_raw_results_con_fallos: dict[str, Any],
) -> None:
    """Debe lanzar AccessibilityViolationError con desglose de la regla crítica."""
    summary = AccessibilityReporter.parse_axe_results(axe_raw_results_con_fallos)

    with pytest.raises(AccessibilityViolationError) as exc_info:
        AccessibilityReporter.assert_zero_critical_violations(summary)

    err_msg = str(exc_info.value)
    assert "1 violaciones críticas" in err_msg
    assert "1 violaciones serias" in err_msg
    assert "button-name" in err_msg
    assert "color-contrast" in err_msg
    assert "#btn-icon" in err_msg


# ---------------------------------------------------------------------------
# Tests de Exportación e Integración de Archivo
# ---------------------------------------------------------------------------
def test_export_report_json_persistencia_y_contenido(
    tmp_path: Path,
    axe_raw_results_con_fallos: dict[str, Any],
) -> None:
    """Valida la serialización y exportación de reporte en JSON estructurado."""
    summary = AccessibilityReporter.parse_axe_results(axe_raw_results_con_fallos)
    target_file = tmp_path / "reportes" / "subcarpeta" / "axe-report.json"

    result_path = AccessibilityReporter.export_report_json(summary, target_file)

    assert result_path.exists()
    assert result_path == target_file.resolve()

    contenido = json.loads(result_path.read_text(encoding="utf-8"))
    assert contenido["url"] == "http://localhost:3000/nuevo-ticket"
    assert contenido["critical_count"] == 1
    assert contenido["serious_count"] == 1
    assert len(contenido["violations"]) == 4
    assert contenido["sha256_hash"] == summary.sha256_hash


def test_parse_axe_results_con_campos_vacios_y_resiliencia() -> None:
    """Valida resiliencia de parseo ante payloads con campos opcionales ausentes."""
    raw: dict[str, Any] = {
        "violations": [
            {
                "id": "unknown-rule",
                "impact": None,
                "description": "Regla sin impacto definido",
                "help": "Ayuda genérica",
                "nodes": [{"html": "<div></div>", "target": "div#main"}],
            }
        ]
    }
    summary = AccessibilityReporter.parse_axe_results(raw)
    assert summary.violations_count == 1
    assert summary.critical_count == 0
    assert summary.url == ""
    assert summary.violations[0].nodes[0].target == ["div#main"]
    assert summary.violations[0].nodes[0].failure_summary is None
