"""Módulo de Análisis y Reporte de Accesibilidad Web (axe-core / Playwright).

Ticket: KC-QA-B5-03
Épica: EQA5 - Motor de Pruebas Autónomo y E2E
Propósito: Analizar, validar y exportar reportes de accesibilidad WCAG 2.1 A/AA
obtenidos mediante escaneos de @axe-core/playwright sobre el Portal Cliente,
asegurando el cumplimiento estricto del umbral de cero violaciones críticas o serias.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class AccessibilityViolationError(Exception):
    """Lanzada cuando una auditoría detecta violaciones de accesibilidad críticas o serias."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B5-03 (Inmutables)
# ---------------------------------------------------------------------------
class AccessibilityNodeDTO(BaseModel):
    """Representa un nodo DOM específico que infringe una regla de accesibilidad."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    html: str
    target: list[str]
    failure_summary: str | None = None


class AccessibilityViolationDTO(BaseModel):
    """Representa una regla de accesibilidad infringida según el estándar axe-core."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    id: str
    impact: str | None = None
    description: str
    help: str
    help_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    nodes: list[AccessibilityNodeDTO] = Field(default_factory=list)


class AccessibilitySummaryDTO(BaseModel):
    """Resumen estructurado e inmutable de la auditoría de accesibilidad WCAG."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    url: str
    scanned_at: str
    violations_count: int
    critical_count: int
    serious_count: int
    moderate_count: int
    minor_count: int
    passes_count: int
    incomplete_count: int
    inapplicable_count: int
    violations: list[AccessibilityViolationDTO] = Field(default_factory=list)
    wcag_tags: list[str] = Field(default_factory=list)
    sha256_hash: str | None = None


# ---------------------------------------------------------------------------
# Analizador y Reportero Oficial
# ---------------------------------------------------------------------------
class AccessibilityReporter:
    """Procesa resultados de escaneo de axe-core, valida compuertas y genera reportes."""

    @staticmethod
    def parse_axe_results(results_json: dict[str, Any]) -> AccessibilitySummaryDTO:
        """Parsea el resultado crudo de axe-core JSON a un DTO inmutable AccessibilitySummaryDTO."""
        url = str(results_json.get("url", ""))
        scanned_at = str(results_json.get("timestamp", datetime.now(UTC).isoformat()))

        raw_violations: list[dict[str, Any]] = results_json.get("violations", [])
        passes_count = len(results_json.get("passes", []))
        incomplete_count = len(results_json.get("incomplete", []))
        inapplicable_count = len(results_json.get("inapplicable", []))

        parsed_violations: list[AccessibilityViolationDTO] = []
        all_wcag_tags: set[str] = set()

        critical_count = 0
        serious_count = 0
        moderate_count = 0
        minor_count = 0

        for raw_v in raw_violations:
            rule_id = str(raw_v.get("id", "desconocido"))
            impact = raw_v.get("impact")
            impact_str = str(impact) if impact is not None else None
            description = str(raw_v.get("description", ""))
            help_text = str(raw_v.get("help", ""))
            help_url = str(raw_v.get("helpUrl")) if raw_v.get("helpUrl") else None
            tags = [str(t) for t in raw_v.get("tags", [])]
            for tag in tags:
                if "wcag" in tag.lower():
                    all_wcag_tags.add(tag)

            if impact_str == "critical":
                critical_count += 1
            elif impact_str == "serious":
                serious_count += 1
            elif impact_str == "moderate":
                moderate_count += 1
            elif impact_str == "minor":
                minor_count += 1

            raw_nodes: list[dict[str, Any]] = raw_v.get("nodes", [])
            parsed_nodes: list[AccessibilityNodeDTO] = []
            for raw_n in raw_nodes:
                html = str(raw_n.get("html", ""))
                raw_target = raw_n.get("target", [])
                target_list: list[str] = (
                    [str(t) for t in raw_target]
                    if isinstance(raw_target, list)
                    else [str(raw_target)]
                )
                failure_summary = (
                    str(raw_n.get("failureSummary")) if raw_n.get("failureSummary") else None
                )
                parsed_nodes.append(
                    AccessibilityNodeDTO(
                        html=html,
                        target=target_list,
                        failure_summary=failure_summary,
                    )
                )

            parsed_violations.append(
                AccessibilityViolationDTO(
                    id=rule_id,
                    impact=impact_str,
                    description=description,
                    help=help_text,
                    help_url=help_url,
                    tags=tags,
                    nodes=parsed_nodes,
                )
            )

        # Cálculo determinista del hash SHA-256 para trazabilidad WORM
        serialized_for_hash = json.dumps(
            {
                "url": url,
                "scanned_at": scanned_at,
                "violations": [v.model_dump() for v in parsed_violations],
                "passes_count": passes_count,
            },
            sort_keys=True,
        ).encode("utf-8")
        sha256_hash = hashlib.sha256(serialized_for_hash).hexdigest()

        return AccessibilitySummaryDTO(
            url=url,
            scanned_at=scanned_at,
            violations_count=len(parsed_violations),
            critical_count=critical_count,
            serious_count=serious_count,
            moderate_count=moderate_count,
            minor_count=minor_count,
            passes_count=passes_count,
            incomplete_count=incomplete_count,
            inapplicable_count=inapplicable_count,
            violations=parsed_violations,
            wcag_tags=sorted(all_wcag_tags),
            sha256_hash=sha256_hash,
        )

    @staticmethod
    def assert_zero_critical_violations(summary: AccessibilitySummaryDTO) -> None:
        """Verifica que no existan violaciones de accesibilidad con impacto critical o serious.

        Lanza AccessibilityViolationError si el umbral se infringe.
        """
        if summary.critical_count == 0 and summary.serious_count == 0:
            logger.info(
                "accessibility.audit_passed",
                url=summary.url,
                total_violations=summary.violations_count,
                moderate=summary.moderate_count,
                minor=summary.minor_count,
            )
            return

        detalles_fallos: list[str] = []
        for v in summary.violations:
            if v.impact in ("critical", "serious"):
                targets = ", ".join(node.target[0] if node.target else "node" for node in v.nodes)
                detalles_fallos.append(
                    f"[{v.impact.upper()}] Regla '{v.id}': {v.help} (Selectores: {targets})"
                )

        mensaje = (
            f"Se detectaron {summary.critical_count} violaciones críticas y "
            f"{summary.serious_count} violaciones serias de accesibilidad en '{summary.url}':\n"
            + "\n".join(detalles_fallos)
        )
        logger.error(
            "accessibility.audit_failed",
            url=summary.url,
            critical=summary.critical_count,
            serious=summary.serious_count,
        )
        raise AccessibilityViolationError(mensaje)

    @staticmethod
    def export_report_json(summary: AccessibilitySummaryDTO, output_path: Path) -> Path:
        """Exporta el resumen estructurado en formato JSON determinista para EvidenceSealer."""
        target_path = output_path.resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = summary.model_dump()
        target_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        logger.info(
            "accessibility.report_exported",
            path=str(target_path),
            sha256=summary.sha256_hash,
        )
        return target_path
