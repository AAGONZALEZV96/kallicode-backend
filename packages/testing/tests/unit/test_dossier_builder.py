"""Pruebas unitarias para el Expediente de Calidad y Matriz de Trazabilidad (KC-QA-B6-07)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from kallicode_testing.dossier_builder import (
    ArtifactDigestDTO,
    DossierBuilder,
    GateComplianceSummaryDTO,
    QualityDossierDTO,
    ReleaseGateBlockedError,
    TraceabilityMatrixEntryDTO,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _write_sample_coverage_xml(
    path: Path,
    *,
    core_lines_valid: int = 100,
    core_lines_covered: int = 86,
    security_lines_valid: int = 100,
    security_lines_covered: int = 97,
) -> Path:
    """Genera un archivo Cobertura XML válido con paquete core y paquete de seguridad."""
    total_valid = core_lines_valid + security_lines_valid
    total_covered = core_lines_covered + security_lines_covered
    total_rate = total_covered / total_valid if total_valid > 0 else 0.0
    core_rate = core_lines_covered / core_lines_valid if core_lines_valid > 0 else 0.0
    sec_rate = security_lines_covered / security_lines_valid if security_lines_valid > 0 else 0.0

    xml_content = f"""<?xml version="1.0" ?>
<coverage lines-valid="{total_valid}" lines-covered="{total_covered}" line-rate="{total_rate:.4f}" branches-valid="0" branches-covered="0" branch-rate="0.0">
  <packages>
    <package name="kallicode_testing.core" line-rate="{core_rate:.4f}" lines-valid="{core_lines_valid}" lines-covered="{core_lines_covered}">
      <classes>
        <class name="pipeline.py" filename="core/pipeline.py" line-rate="{core_rate:.4f}"/>
      </classes>
    </package>
    <package name="kallicode_testing.security" line-rate="{sec_rate:.4f}" lines-valid="{security_lines_valid}" lines-covered="{security_lines_covered}">
      <classes>
        <class name="sandbox_isolation.py" filename="security/sandbox_isolation.py" line-rate="{sec_rate:.4f}"/>
      </classes>
    </package>
  </packages>
</coverage>
"""
    path.write_text(xml_content, encoding="utf-8")
    return path


def _write_sample_junit_xml(path: Path, *, include_failure: bool = False) -> Path:
    """Genera un archivo JUnit XML válido mapeado a criterios AC-1, AC-2 y AC-3."""
    failures_count = 1 if include_failure else 0
    ac3_body = (
        '<failure message="assert False">fallo simulado AC-3</failure>' if include_failure else ""
    )
    xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" failures="{failures_count}" errors="0" skipped="0" time="1.25">
    <testcase classname="tests.unit.test_core" name="test_AC_1_pipeline_ok" time="0.40">
      <properties><property name="criterion" value="AC-1"/></properties>
    </testcase>
    <testcase classname="tests.security.test_sandbox_isolation" name="test_AC_2_sandbox_ok" time="0.45">
      <properties><property name="criterion" value="AC-2"/></properties>
    </testcase>
    <testcase classname="tests.e2e.portal.spec" name="test_AC_3_e2e_portal" time="0.40">
      <properties><property name="criterion" value="AC-3"/></properties>
      {ac3_body}
    </testcase>
  </testsuite>
</testsuites>
"""
    path.write_text(xml_content, encoding="utf-8")
    return path


def _seed_compliant_workspace(artifacts_dir: Path) -> dict[str, Path]:
    """Crea un conjunto completo de artefactos conformes dentro de `artifacts_dir`."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    cov_path = _write_sample_coverage_xml(artifacts_dir / "coverage.xml")
    junit_path = _write_sample_junit_xml(artifacts_dir / "junit.xml")

    criteria_file = artifacts_dir / "criteria.json"
    criteria_file.write_text(
        json.dumps(
            [
                {"criterion_id": "AC-1", "description": "Pipeline core funcional"},
                {"criterion_id": "AC-2", "description": "Aislamiento sandbox sin red"},
                {"criterion_id": "AC-3", "description": "Flujo E2E portal aprobado"},
            ]
        ),
        encoding="utf-8",
    )

    k6_report = artifacts_dir / "k6-kanban-report.json"
    k6_report.write_text(
        json.dumps({"report_id": "kanban-001", "is_conforming": True, "p95_ms": 120.5}),
        encoding="utf-8",
    )
    quarantine_file = artifacts_dir / "quarantine.json"
    quarantine_file.write_text(
        json.dumps(
            [
                {
                    "test_identifier": "tests/e2e/test_legacy.py",
                    "status": "active",
                    "desflake_ticket": "KC-QA-DESFLAKE-42",
                }
            ]
        ),
        encoding="utf-8",
    )
    integrity_file = artifacts_dir / "integrity.json"
    integrity_file.write_text(json.dumps({"integrity_violations": 0}), encoding="utf-8")

    return {
        "coverage": cov_path,
        "junit": junit_path,
        "criteria": criteria_file,
        "k6_report": k6_report,
        "quarantine": quarantine_file,
        "integrity": integrity_file,
    }


def test_build_dossier_conforme_aprobado(tmp_path: Path) -> None:
    """Genera expediente válido con cobertura > 80%, 0 fallos y root_hash determinista."""
    artifacts_dir = tmp_path / "artifacts"
    _seed_compliant_workspace(artifacts_dir)

    builder = DossierBuilder()
    dossier_a = builder.build_dossier("v1.1", "abc1234deadbeef", artifacts_dir)
    dossier_b = builder.build_dossier("v1.1", "abc1234deadbeef", artifacts_dir)

    builder.assert_release_ready(dossier_a)

    assert dossier_a.release_version == "v1.1"
    assert dossier_a.commit_sha == "abc1234deadbeef"
    assert dossier_a.compliance_summary.is_ready is True
    assert dossier_a.compliance_summary.coverage_pct >= 80.0
    assert dossier_a.compliance_summary.security_coverage_pct >= 95.0
    assert dossier_a.compliance_summary.total_criteria == 3
    assert dossier_a.compliance_summary.passed_criteria == 3
    assert dossier_a.compliance_summary.open_flaky_unassigned == 0
    assert dossier_a.compliance_summary.integrity_violations == 0
    assert len(dossier_a.root_hash) == 64
    assert dossier_a.root_hash == dossier_b.root_hash
    assert len(dossier_a.artifacts) == 6

    # Exportación JSON, Markdown y PDF imprimible
    json_out = builder.export_json(dossier_a, tmp_path / "out" / "dossier_v1.1.json")
    md_out = builder.export_markdown(dossier_a, tmp_path / "out" / "expediente_calidad_v1.1.md")
    pdf_out = builder.export_pdf(dossier_a, tmp_path / "out" / "expediente_calidad_v1.1.pdf")

    assert json_out.exists()
    parsed_json = json.loads(json_out.read_text(encoding="utf-8"))
    assert parsed_json["root_hash"] == dossier_a.root_hash

    assert md_out.exists()
    md_text = md_out.read_text(encoding="utf-8")
    assert "APROBADO (M6 GA READY)" in md_text
    assert "AC-1" in md_text
    assert dossier_a.root_hash in md_text

    assert pdf_out.exists()
    pdf_bytes = pdf_out.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf_bytes


def test_assert_release_ready_bloquea_por_cobertura_insuficiente(tmp_path: Path) -> None:
    """Lanza ReleaseGateBlockedError si la cobertura global es menor al 80%."""
    artifacts_dir = tmp_path / "artifacts"
    _seed_compliant_workspace(artifacts_dir)
    # 50/100 core + 96/100 security = 73.0% global (< 80%)
    _write_sample_coverage_xml(
        artifacts_dir / "coverage.xml",
        core_lines_valid=100,
        core_lines_covered=50,
        security_lines_valid=100,
        security_lines_covered=96,
    )

    builder = DossierBuilder()
    dossier = builder.build_dossier("v1.1", "commit-low-cov", artifacts_dir)

    assert dossier.compliance_summary.coverage_pct < 80.0
    assert dossier.compliance_summary.is_ready is False
    with pytest.raises(ReleaseGateBlockedError, match="Cobertura global insuficiente"):
        builder.assert_release_ready(dossier)


def test_assert_release_ready_bloquea_por_cuarentena_huerfana(tmp_path: Path) -> None:
    """Bloquea el release si hay pruebas inestables activas sin ticket de desflake."""
    artifacts_dir = tmp_path / "artifacts"
    files = _seed_compliant_workspace(artifacts_dir)
    files["quarantine"].write_text(
        json.dumps(
            {
                "tenant-alpha": {
                    "tests/e2e/test_flaky_orphan.py": {
                        "test_identifier": "tests/e2e/test_flaky_orphan.py",
                        "status": "active",
                        "desflake_ticket": "",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    builder = DossierBuilder()
    dossier = builder.build_dossier("v1.1", "commit-flaky", artifacts_dir)

    assert dossier.compliance_summary.open_flaky_unassigned == 1
    assert dossier.compliance_summary.is_ready is False
    with pytest.raises(ReleaseGateBlockedError, match="sin ticket de desflake"):
        builder.assert_release_ready(dossier)


def test_integridad_hash_sha256_detecta_tampering(tmp_path: Path) -> None:
    """Confirma que modificar el contenido de un artefacto altera el SHA-256 y el root_hash."""
    artifacts_dir = tmp_path / "artifacts"
    files = _seed_compliant_workspace(artifacts_dir)

    builder = DossierBuilder()
    dossier_original = builder.build_dossier("v1.1", "commit-1", artifacts_dir)

    # Tampering silencioso de 1 campo en reporte k6
    files["k6_report"].write_text(
        json.dumps({"report_id": "kanban-001", "is_conforming": True, "p95_ms": 999.9}),
        encoding="utf-8",
    )
    dossier_tampered = builder.build_dossier("v1.1", "commit-1", artifacts_dir)

    assert dossier_original.root_hash != dossier_tampered.root_hash
    orig_k6 = next(a for a in dossier_original.artifacts if a.name == "k6-kanban-report.json")
    tamp_k6 = next(a for a in dossier_tampered.artifacts if a.name == "k6-kanban-report.json")
    assert orig_k6.sha256_hash != tamp_k6.sha256_hash


def test_inmutabilidad_dtos_dossier(tmp_path: Path) -> None:
    """Verifica frozen=True y __test__ = False en todos los DTOs Pydantic v2."""
    artifacts_dir = tmp_path / "artifacts"
    _seed_compliant_workspace(artifacts_dir)

    builder = DossierBuilder()
    dossier = builder.build_dossier("v1.1", "commit-frozen", artifacts_dir)

    for dto_cls in (
        ArtifactDigestDTO,
        TraceabilityMatrixEntryDTO,
        GateComplianceSummaryDTO,
        QualityDossierDTO,
    ):
        assert dto_cls.__test__ is False

    with pytest.raises(ValidationError):
        dossier.root_hash = "tampered"
    with pytest.raises(ValidationError):
        dossier.compliance_summary.is_ready = False
    with pytest.raises(ValidationError):
        dossier.artifacts[0].sha256_hash = "0000"
    with pytest.raises(ValidationError):
        dossier.traceability_matrix[0].passed = False


def test_bloqueos_seguridad_integridad_criterios_vacios_y_cli(tmp_path: Path) -> None:
    """Cubre bloqueo por seguridad < 95%, integridad > 0, criterios vacíos y CLI build_dossier."""
    from scripts.build_dossier import main as cli_main

    artifacts_dir = tmp_path / "artifacts"
    files = _seed_compliant_workspace(artifacts_dir)
    builder = DossierBuilder()

    # 1. Bloqueo por cobertura de seguridad < 95% aunque la global sea >= 80%
    _write_sample_coverage_xml(
        files["coverage"],
        core_lines_valid=100,
        core_lines_covered=95,
        security_lines_valid=100,
        security_lines_covered=88,
    )
    dossier_sec = builder.build_dossier("v1.1", "commit-sec", artifacts_dir)
    assert dossier_sec.compliance_summary.coverage_pct >= 80.0
    assert dossier_sec.compliance_summary.security_coverage_pct < 95.0
    with pytest.raises(ReleaseGateBlockedError, match="seguridad/auditoría insuficiente"):
        builder.assert_release_ready(dossier_sec)

    # 2. Restaurar cobertura y simular violaciones de core.validar_integridad() + criterio fallido
    _write_sample_coverage_xml(files["coverage"])
    files["integrity"].write_text(json.dumps({"integrity_violations": 2}), encoding="utf-8")
    _write_sample_junit_xml(files["junit"], include_failure=True)
    dossier_fail = builder.build_dossier("v1.1", "commit-fail", artifacts_dir)
    with pytest.raises(ReleaseGateBlockedError) as exc_info:
        builder.assert_release_ready(dossier_fail)
    assert "core.validar_integridad()" in str(exc_info.value)
    assert "AC-3" in str(exc_info.value)

    # 3. Expediente vacío / 0 criterios prohibido
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    (empty_dir / "placeholder.txt").write_text("hola", encoding="utf-8")
    dossier_empty = builder.build_dossier("v1.1", "commit-empty", empty_dir, spec_criteria=[])
    with pytest.raises(ReleaseGateBlockedError, match="Cero criterios"):
        builder.assert_release_ready(dossier_empty)

    # 4. CLI exit code 0 cuando todo está conforme y exit code 1 cuando se bloquea
    ok_dir = tmp_path / "cli_ok"
    _seed_compliant_workspace(ok_dir)
    json_out = tmp_path / "cli_out" / "dossier_v1.1.json"
    md_out = tmp_path / "cli_out" / "expediente_calidad_v1.1.md"
    pdf_out = tmp_path / "cli_out" / "expediente_calidad_v1.1.pdf"

    assert (
        cli_main(
            [
                "--version",
                "v1.1",
                "--commit-sha",
                "sha-cli-ok",
                "--artifacts-dir",
                str(ok_dir),
                "--json-out",
                str(json_out),
                "--md-out",
                str(md_out),
                "--pdf-out",
                str(pdf_out),
                "--enforce-gate",
            ]
        )
        == 0
    )
    assert json_out.exists()
    assert md_out.exists()
    assert pdf_out.exists()

    # CLI exit 1 con --enforce-gate si integridad falla
    (ok_dir / "integrity.json").write_text(
        json.dumps({"integrity_violations": 1}), encoding="utf-8"
    )
    assert (
        cli_main(
            [
                "--artifacts-dir",
                str(ok_dir),
                "--json-out",
                str(json_out),
                "--md-out",
                str(md_out),
                "--enforce-gate",
            ]
        )
        == 1
    )

    # 5. Directorio inexistente tolerado sin FileNotFoundError y ejecución contra docs/calidad/
    missing_dossier = builder.build_dossier("v1.1", "sha-missing", tmp_path / "dir_inexistente")
    assert missing_dossier.artifacts == []
    calidad_json = tmp_path / "calidad_dossier.json"
    calidad_md = tmp_path / "calidad_expediente.md"
    assert (
        cli_main(
            [
                "--artifacts-dir",
                str(REPO_ROOT / "docs" / "calidad"),
                "--json-out",
                str(calidad_json),
                "--md-out",
                str(calidad_md),
                "--enforce-gate",
            ]
        )
        == 0
    )
    assert (
        cli_main(
            [
                "--verify-only",
                "--json-out",
                str(calidad_json),
                "--md-out",
                str(calidad_md),
                "--enforce-gate",
            ]
        )
        == 0
    )
    assert (
        cli_main(
            [
                "--verify-only",
                "--json-out",
                str(tmp_path / "no_existe_dossier.json"),
                "--md-out",
                str(calidad_md),
            ]
        )
        == 1
    )
