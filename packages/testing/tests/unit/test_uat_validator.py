"""Pruebas unitarias para el Validador de Criterios UAT (KC-QA-B6-08)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from kallicode_testing.uat_validator import (
    UatComplianceThresholdError,
    UatScenarioDTO,
    UatSignoffReportDTO,
    UatValidator,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _sample_scenarios(*, status_override: str | None = None) -> list[UatScenarioDTO]:
    """Genera una lista representativa de escenarios UAT aprobados (o con estado alterado)."""
    return [
        UatScenarioDTO(
            scenario_id="UAT-01",
            role="Owner / Admin",
            title="Creación de tickets y Triage",
            description="Creación de bug/mejora y paso a Triage.",
            automated_test_ref="packages/testing/e2e/tests/portal_critical_flows.spec.ts",
            evidence_type="Playwright Trace + Reporte JSON",
            status="PASSED",
            notes="Validado sin errores 5xx.",
        ),
        UatScenarioDTO(
            scenario_id="UAT-08",
            role="Viewer",
            title="Consulta Kanban y rechazo HTTP 403",
            description="Rechazo estricto ante intentos de mutación por Viewer.",
            automated_test_ref="packages/testing/tests/seguridad/test_rbac_matrix.py",
            evidence_type="Reporte Matriz RBAC + Digest WORM",
            status=status_override or "PASSED",
            notes="Control RBAC verificado.",
        ),
    ]


def test_uat_evaluation_nominal_100_percent(tmp_path: Path) -> None:
    """Valida informe de aprobación total con 100% de criterios cumplidos."""
    validator = UatValidator()
    scenarios = _sample_scenarios()

    report = validator.evaluate_compliance(scenarios)
    validator.assert_all_scenarios_passed(report)

    assert report.total_scenarios == 2
    assert report.passed_scenarios == 2
    assert report.compliance_pct == 100.0
    assert report.is_fully_approved is True

    # Exportación JSON y Markdown deterministas + roundtrip
    json_out = validator.export_json_report(report, tmp_path / "uat_signoff_report.json")
    md_out = validator.export_markdown_matrix(report, tmp_path / "uat_signoff_matrix.md")

    assert json_out.exists()
    parsed_json = json.loads(json_out.read_text(encoding="utf-8"))
    assert parsed_json["is_fully_approved"] is True
    assert parsed_json["compliance_pct"] == 100.0

    assert md_out.exists()
    reloaded = validator.load_scenarios_from_markdown(md_out)
    assert len(reloaded) == 2
    assert reloaded[0].scenario_id == "UAT-01"
    assert reloaded[1].scenario_id == "UAT-08"


@pytest.mark.parametrize("bad_status", ["FAILED", "PENDING", "SKIPPED"])
def test_assert_all_scenarios_passed_bloquea_ante_fallo(bad_status: str) -> None:
    """Lanza UatComplianceThresholdError si algún escenario está FAILED, PENDING o SKIPPED."""
    validator = UatValidator()
    scenarios = _sample_scenarios(status_override=bad_status)

    report = validator.evaluate_compliance(scenarios)

    assert report.is_fully_approved is False
    assert report.compliance_pct == 50.0
    with pytest.raises(UatComplianceThresholdError, match="UAT-08"):
        validator.assert_all_scenarios_passed(report)


def test_parse_scenarios_from_markdown() -> None:
    """Comprueba la carga y validación del 100% de `docs/calidad/uat_signoff_matrix.md`."""
    matrix_path = REPO_ROOT / "docs" / "calidad" / "uat_signoff_matrix.md"
    assert matrix_path.exists()

    validator = UatValidator()
    scenarios = validator.load_scenarios_from_markdown(matrix_path)
    report = validator.evaluate_uat_readiness(matrix_path)

    assert len(scenarios) == 8
    scenario_ids = [item.scenario_id for item in scenarios]
    assert scenario_ids == [f"UAT-0{i}" for i in range(1, 9)]
    assert report.is_fully_approved is True
    assert report.compliance_pct == 100.0


def test_prevencion_falsos_verdes_matriz_vacia(tmp_path: Path) -> None:
    """Valida rechazo estricto si no hay escenarios cargados o falta evidencia trazable."""
    validator = UatValidator()

    empty_report = validator.evaluate_compliance([])
    assert empty_report.total_scenarios == 0
    assert empty_report.compliance_pct == 0.0
    assert empty_report.is_fully_approved is False
    with pytest.raises(UatComplianceThresholdError, match="Matriz UAT vacía"):
        validator.assert_all_scenarios_passed(empty_report)

    # Archivo Markdown sin filas UAT válidas también debe bloquear
    empty_md = tmp_path / "empty_uat.md"
    empty_md.write_text("# Matriz vacía\n\nSin escenarios registrados.\n", encoding="utf-8")
    with pytest.raises(UatComplianceThresholdError, match="Matriz UAT vacía"):
        validator.evaluate_uat_readiness(empty_md)

    # Escenario marcado PASSED pero sin prueba automatizada/evidencia -> prohibido falso verde
    orphan_scenario = UatScenarioDTO(
        scenario_id="UAT-99",
        role="Viewer",
        title="Sin evidencia",
        description="Escenario sin trazabilidad",
        automated_test_ref="",
        evidence_type="",
        status="PASSED",
        notes="Faltan evidencias",
    )
    orphan_report = validator.evaluate_compliance([orphan_scenario])
    assert orphan_report.passed_scenarios == 0
    assert orphan_report.is_fully_approved is False
    with pytest.raises(UatComplianceThresholdError, match="sin prueba automatizada"):
        validator.assert_all_scenarios_passed(orphan_report)


def test_inmutabilidad_dtos_uat() -> None:
    """Confirma ConfigDict(frozen=True) y __test__ = False en todos los DTOs UAT."""
    for dto_cls in (UatScenarioDTO, UatSignoffReportDTO):
        assert dto_cls.__test__ is False

    validator = UatValidator()
    scenarios = _sample_scenarios()
    report = validator.evaluate_compliance(scenarios)

    with pytest.raises(ValidationError):
        scenarios[0].status = "FAILED"
    with pytest.raises(ValidationError):
        report.is_fully_approved = False
    with pytest.raises(ValidationError):
        report.compliance_pct = 0.0


def test_verify_uat_cli_y_tablas_por_rol(tmp_path: Path) -> None:
    """Cubre CLI `scripts/verify_uat.py` (exit 0/1) y parser de tablas por encabezado de rol."""
    from scripts.verify_uat import main as cli_main

    matrix_path = REPO_ROOT / "docs" / "calidad" / "uat_signoff_matrix.md"
    json_out = tmp_path / "uat_signoff_report.json"
    md_out = tmp_path / "uat_exported.md"

    assert (
        cli_main(
            [
                "--matrix-path",
                str(matrix_path),
                "--json-out",
                str(json_out),
                "--md-out",
                str(md_out),
                "--enforce-approval",
            ]
        )
        == 0
    )
    assert json_out.exists()
    assert md_out.exists()

    # Tabla agrupada por encabezado de rol (7 columnas) con un escenario PENDING -> exit 1
    role_grouped_md = tmp_path / "role_grouped.md"
    role_grouped_md.write_text(
        """# Matriz UAT por Secciones

## Architect

| ID | Título | Descripción | Prueba Automatizada | Tipo Evidencia | Estado | Notas |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| UAT-04 | Gate 1 | Firma Gate 1 | `test_gate1.py` | WORM | PENDING | En revisión |
""",
        encoding="utf-8",
    )
    assert (
        cli_main(
            [
                "--matrix-path",
                str(role_grouped_md),
                "--json-out",
                str(tmp_path / "failed_report.json"),
                "--enforce-approval",
            ]
        )
        == 1
    )

    # Archivo faltante -> exit 1
    assert cli_main(["--matrix-path", str(tmp_path / "no_existe.md")]) == 1
