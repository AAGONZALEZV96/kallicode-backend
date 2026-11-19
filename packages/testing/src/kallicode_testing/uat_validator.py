"""Validador programático de cumplimiento de Criterios UAT (KC-QA-B6-08).

Épica: EQA8 — Pruebas de Carga, Resiliencia, Caos y Expediente de Release (Hito M6 GA).
Responsabilidad:
- Parsear la matriz formal `docs/calidad/uat_signoff_matrix.md`.
- Verificar que el 100% de los escenarios de aceptación por rol estén en estado PASSED
  con referencia de prueba automatizada y evidencia trazable no vacía.
- Bloquear el avance a producción ante escenarios omitidos, pendientes, fallidos o vacíos.
- Exportar matriz Markdown consolidada y reporte JSON para el expediente de release v1.1.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kallicode_testing.logger import get_logger

logger = get_logger()

_UAT_ID_RE = re.compile(r"^UAT-\d+$", re.IGNORECASE)
_TABLE_SEPARATOR_RE = re.compile(r"^[\s|:\-]+$")

_ROLE_DISPLAY_ORDER: tuple[str, ...] = (
    "Owner / Admin",
    "Owner",
    "Admin",
    "Architect",
    "Approver",
    "Viewer",
)


# ---------------------------------------------------------------------------
# Excepción de Dominio
# ---------------------------------------------------------------------------
class UatComplianceThresholdError(RuntimeError):
    """Lanzada cuando la matriz UAT no alcanza el 100% de cumplimiento con evidencia real."""


# ---------------------------------------------------------------------------
# DTOs Oficiales Inmutables (Pydantic v2)
# ---------------------------------------------------------------------------
class UatScenarioDTO(BaseModel):
    """Representa un escenario individual de aceptación de negocio UAT por rol."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    role: str
    title: str
    description: str
    automated_test_ref: str
    evidence_type: str
    status: str
    notes: str


class UatSignoffReportDTO(BaseModel):
    """Resultado consolidado e inmutable de la evaluación UAT de Release."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    report_id: str
    evaluated_at: str
    total_scenarios: int
    passed_scenarios: int
    compliance_pct: float
    is_fully_approved: bool
    scenarios: list[UatScenarioDTO]


# ---------------------------------------------------------------------------
# Helpers Internos
# ---------------------------------------------------------------------------
def _clean_cell(raw_value: str) -> str:
    """Limpia espacios y backticks Markdown de una celda de tabla."""
    cleaned = raw_value.strip()
    if cleaned.startswith("`") and cleaned.endswith("`") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _split_markdown_row(line: str) -> list[str]:
    """Divide una fila de tabla Markdown en celdas conservando columnas vacías."""
    stripped = line.strip()
    stripped = stripped.removeprefix("|")
    stripped = stripped.removesuffix("|")
    return [_clean_cell(cell) for cell in stripped.split("|")]


def _has_traceable_evidence(scenario: UatScenarioDTO) -> bool:
    """Verifica que el escenario tenga referencia de prueba y evidencia no vacía."""
    return bool(scenario.automated_test_ref.strip()) and bool(scenario.evidence_type.strip())


def _group_roles_for_summary(scenarios: list[UatScenarioDTO]) -> list[tuple[str, int, int]]:
    """Agrupa conteos total y PASSED por rol preservando un orden determinista."""
    counts: dict[str, list[int]] = {}
    for item in scenarios:
        role_name = item.role.strip() or "Sin Rol"
        bucket = counts.setdefault(role_name, [0, 0])
        bucket[0] += 1
        if item.status.strip().upper() == "PASSED" and _has_traceable_evidence(item):
            bucket[1] += 1

    ordered: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for role in _ROLE_DISPLAY_ORDER:
        if role in counts:
            ordered.append((role, counts[role][0], counts[role][1]))
            seen.add(role)
    for role in sorted(counts):
        if role not in seen:
            ordered.append((role, counts[role][0], counts[role][1]))
    return ordered


# ---------------------------------------------------------------------------
# Clase Principal UatValidator
# ---------------------------------------------------------------------------
class UatValidator:
    """Validador y exportador oficial de la Matriz de Aceptación UAT (KC-QA-B6-08)."""

    REQUIRED_COMPLIANCE_PCT: float = 100.0

    def load_scenarios_from_markdown(self, matrix_path: Path) -> list[UatScenarioDTO]:
        """Parsea escenarios `UAT-nn` desde tablas Markdown en `matrix_path`."""
        if not matrix_path.exists():
            raise FileNotFoundError(f"Matriz UAT no encontrada: {matrix_path}")

        content = matrix_path.read_text(encoding="utf-8")
        scenarios: list[UatScenarioDTO] = []
        current_role_heading = ""
        in_uat_table = False

        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("#"):
                heading_text = stripped.lstrip("#").strip()
                if any(
                    keyword in heading_text.lower()
                    for keyword in ("owner", "admin", "architect", "approver", "viewer")
                ):
                    current_role_heading = heading_text
                in_uat_table = False
                continue

            if not stripped.startswith("|"):
                in_uat_table = False
                continue

            if _TABLE_SEPARATOR_RE.match(stripped):
                continue

            cells = _split_markdown_row(stripped)
            if not cells:
                continue

            first_upper = cells[0].upper()
            if first_upper in {"ID", "SCENARIO ID", "SCENARIO_ID", "ESCENARIO"}:
                in_uat_table = True
                continue

            if not _UAT_ID_RE.match(first_upper):
                continue

            if not in_uat_table and len(cells) < 7:
                continue

            # Formato estándar de 8 columnas:
            # Scenario ID | Rol | Título | Descripción | Prueba Automatizada | Tipo Evidencia | Estado | Notas
            if len(cells) >= 8:
                scenario_id, role, title, desc, test_ref, evidence, status, notes = cells[:8]
            elif len(cells) == 7:
                scenario_id, title, desc, test_ref, evidence, status, notes = cells
                role = current_role_heading or "General"
            else:
                continue

            scenarios.append(
                UatScenarioDTO(
                    scenario_id=scenario_id.upper(),
                    role=role or current_role_heading or "General",
                    title=title,
                    description=desc,
                    automated_test_ref=test_ref,
                    evidence_type=evidence,
                    status=status.upper(),
                    notes=notes,
                )
            )

        logger.info(
            "uat.scenarios_loaded",
            matrix_path=str(matrix_path),
            scenarios_count=len(scenarios),
        )
        return scenarios

    def evaluate_compliance(self, scenarios: list[UatScenarioDTO]) -> UatSignoffReportDTO:
        """Evalúa el porcentaje de cumplimiento UAT sin permitir falsos verdes."""
        total = len(scenarios)
        if total == 0:
            report = UatSignoffReportDTO(
                report_id=f"uat-{uuid.uuid4().hex[:12]}",
                evaluated_at=datetime.now(UTC).isoformat(),
                total_scenarios=0,
                passed_scenarios=0,
                compliance_pct=0.0,
                is_fully_approved=False,
                scenarios=[],
            )
            logger.warning("uat.empty_matrix_evaluated", report_id=report.report_id)
            return report

        passed_count = sum(
            1
            for item in scenarios
            if item.status.strip().upper() == "PASSED" and _has_traceable_evidence(item)
        )
        compliance_pct = round((passed_count / total) * 100.0, 2)
        is_fully_approved = (
            total > 0 and passed_count == total and compliance_pct >= self.REQUIRED_COMPLIANCE_PCT
        )

        report = UatSignoffReportDTO(
            report_id=f"uat-{uuid.uuid4().hex[:12]}",
            evaluated_at=datetime.now(UTC).isoformat(),
            total_scenarios=total,
            passed_scenarios=passed_count,
            compliance_pct=compliance_pct,
            is_fully_approved=is_fully_approved,
            scenarios=list(scenarios),
        )
        logger.info(
            "uat.compliance_evaluated",
            report_id=report.report_id,
            total_scenarios=total,
            passed_scenarios=passed_count,
            compliance_pct=compliance_pct,
            is_fully_approved=is_fully_approved,
        )
        return report

    def assert_all_scenarios_passed(self, report: UatSignoffReportDTO) -> None:
        """Compuerta dura UAT: exige 100% de escenarios PASSED con evidencia trazable."""
        reasons: list[str] = []

        if report.total_scenarios <= 0 or not report.scenarios:
            reasons.append("Matriz UAT vacía: se requiere al menos un escenario evaluado")

        failed_or_pending = [
            f"{item.scenario_id}({item.status})"
            for item in report.scenarios
            if item.status.strip().upper() != "PASSED"
        ]
        if failed_or_pending:
            reasons.append(f"Escenarios no aprobados: {', '.join(failed_or_pending)}")

        missing_evidence = [
            item.scenario_id for item in report.scenarios if not _has_traceable_evidence(item)
        ]
        if missing_evidence:
            reasons.append(
                f"Escenarios sin prueba automatizada o evidencia trazable: "
                f"{', '.join(missing_evidence)}"
            )

        if report.compliance_pct < self.REQUIRED_COMPLIANCE_PCT or (
            report.passed_scenarios < report.total_scenarios
        ):
            reasons.append(
                f"Cumplimiento UAT insuficiente: {report.compliance_pct:.2f}% "
                f"({report.passed_scenarios}/{report.total_scenarios})"
            )

        if not report.is_fully_approved and not reasons:
            reasons.append("El reporte UAT reportó is_fully_approved=False")

        if reasons:
            detail = "; ".join(reasons)
            logger.error(
                "uat.compliance_blocked",
                report_id=report.report_id,
                reasons=reasons,
            )
            raise UatComplianceThresholdError(f"Compuerta UAT bloqueada: {detail}")

    def evaluate_uat_readiness(self, matrix_path: Path) -> UatSignoffReportDTO:
        """Carga `matrix_path`, evalúa cumplimiento del 100% y exige compuerta aprobada."""
        scenarios = self.load_scenarios_from_markdown(matrix_path)
        report = self.evaluate_compliance(scenarios)
        self.assert_all_scenarios_passed(report)
        return report

    def export_markdown_matrix(self, report: UatSignoffReportDTO, output_path: Path) -> Path:
        """Exporta `docs/calidad/uat_signoff_matrix.md` con tablas por rol y manifiesto de firmas."""
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        verdict = "APROBADO (100% UAT GA)" if report.is_fully_approved else "NO APROBADO"

        lines: list[str] = [
            "# Matriz Oficial de Aceptación de Usuario (UAT) — KalliCode v1.1",
            "",
            f"- **ID Reporte UAT:** `{report.report_id}`",
            f"- **Evaluado (UTC):** `{report.evaluated_at}`",
            f"- **Veredicto UAT:** `{verdict}`",
            (
                f"- **Cumplimiento Global:** `{report.compliance_pct:.2f}%` "
                f"({report.passed_scenarios}/{report.total_scenarios} escenarios)"
            ),
            "",
            "## Resumen por Rol de Negocio",
            "",
            "| Rol | Total Escenarios | Aprobados | Cumplimiento |",
            "| :--- | ---: | ---: | ---: |",
        ]

        for role_name, role_total, role_passed in _group_roles_for_summary(report.scenarios):
            pct = round((role_passed / role_total) * 100.0, 2) if role_total > 0 else 0.0
            lines.append(f"| {role_name} | {role_total} | {role_passed} | {pct:.2f}% |")

        lines.extend(
            [
                "",
                "## Matriz Exhaustiva de Escenarios UAT",
                "",
                (
                    "| Scenario ID | Rol | Título | Descripción | "
                    "Prueba Automatizada | Tipo Evidencia | Estado | Notas |"
                ),
                "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )

        for entry in report.scenarios:
            lines.append(
                f"| `{entry.scenario_id}` | {entry.role} | {entry.title} | "
                f"{entry.description} | `{entry.automated_test_ref}` | "
                f"{entry.evidence_type} | {entry.status} | {entry.notes} |"
            )

        lines.extend(
            [
                "",
                "## Manifiesto de Firmas de Aprobación (Hito M6 GA)",
                "",
                "| Autoridad Firmante | Rol en Gobernanza | Estado de Firma | Evidencia / Criterio |",
                "| :--- | :--- | :--- | :--- |",
                (
                    f"| Product Owner | Validación Funcional de Negocio | "
                    f"{'FIRMADO' if report.is_fully_approved else 'PENDIENTE'} | "
                    f"Cobertura UAT {report.compliance_pct:.2f}% |"
                ),
                (
                    f"| Architecture Board | Aprobación Técnica y Gates 1-3 | "
                    f"{'FIRMADO' if report.is_fully_approved else 'PENDIENTE'} | "
                    "Contratos OpenAPI y Preflight GA |"
                ),
                (
                    f"| Tech Lead QA (Equipo 5) | Certificación Cero Falsos Verdes | "
                    f"{'FIRMADO' if report.is_fully_approved else 'PENDIENTE'} | "
                    f"Reporte `{report.report_id}` |"
                ),
            ]
        )

        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("uat.markdown_exported", path=str(target), report_id=report.report_id)
        return target

    def export_json_report(self, report: UatSignoffReportDTO, output_path: Path) -> Path:
        """Exporta `uat_signoff_report.json` estructurado para incorporación en release v1.1."""
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
        target.write_text(f"{payload}\n", encoding="utf-8")
        logger.info("uat.json_exported", path=str(target), report_id=report.report_id)
        return target
