"""Pruebas unitarias para el Auditor Final de Seguridad OWASP Top 10 (KC-QA-B6-10)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from kallicode_testing.security_auditor import (
    SecurityAuditGateBlockedError,
    SecurityAuditor,
    SecurityAuditReportDTO,
    SecurityFindingDTO,
    main,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]


def _sample_medium_finding() -> SecurityFindingDTO:
    """Genera un hallazgo Medium controlado no bloqueante para inventario GA."""
    return SecurityFindingDTO(
        tool_source="bandit",
        rule_id="B101",
        severity="LOW",
        file_path="packages/testing/src/kallicode_testing/smoke_runner.py",
        line_number=42,
        description="Advertencia informativa de diagnóstico verificada.",
        owasp_category="A08:2021-Software and Data Integrity Failures",
    )


def test_security_audit_nominal_cero_vulnerabilidades(tmp_path: Path) -> None:
    """Valida reporte conforme con 0 Critical, 0 High y 0 secretos filtrados."""
    auditor = SecurityAuditor()
    owasp_controls = auditor.evaluate_owasp_posture(REPO_ROOT)
    assert all(owasp_controls.values()) is True

    report = auditor.consolidate_audit(
        [_sample_medium_finding()],
        commit_sha="sha-clean-ga",
        owasp_controls=owasp_controls,
    )
    auditor.assert_zero_security_blockers(report)

    assert report.critical_count == 0
    assert report.high_count == 0
    assert report.low_count == 1
    assert report.secrets_detected == 0
    assert report.passed_audit is True
    assert report.is_conforming is True

    json_out = auditor.export_json_report(report, tmp_path / "security-audit-report.json")
    md_out = auditor.export_markdown_report(report, tmp_path / "informe_seguridad_final_v1.1.md")

    assert json_out.exists()
    parsed = json.loads(json_out.read_text(encoding="utf-8"))
    assert parsed["is_conforming"] is True

    assert md_out.exists()
    md_text = md_out.read_text(encoding="utf-8")
    assert "APROBADO (CERO BLOQUEADORES GA)" in md_text
    assert "Inventario Transparente" in md_text
    assert "B101" in md_text


def test_assert_zero_security_blockers_falla_por_secreto_detectado(tmp_path: Path) -> None:
    """Lanza SecurityAuditGateBlockedError si Gitleaks detecta un secreto real."""
    gitleaks_file = tmp_path / "gitleaks.json"
    gitleaks_file.write_text(
        json.dumps(
            [
                {
                    "RuleID": "generic-api-key",
                    "File": "apps/api/src/kallicode_api/config.py",
                    "StartLine": 18,
                    "Description": "Detected API key in production config",
                }
            ]
        ),
        encoding="utf-8",
    )

    auditor = SecurityAuditor()
    findings = auditor.parse_gitleaks_report(gitleaks_file)
    report = auditor.consolidate_audit(findings, commit_sha="sha-secret-leak")

    assert report.secrets_detected == 1
    assert report.critical_count == 1
    assert report.passed_audit is False
    assert report.is_conforming is False

    with pytest.raises(SecurityAuditGateBlockedError, match="Secretos filtrados"):
        auditor.assert_zero_security_blockers(report)


@pytest.mark.parametrize("bad_severity", ["HIGH", "CRITICAL"])
def test_assert_zero_security_blockers_falla_por_vulnerabilidad_alta(bad_severity: str) -> None:
    """Bloquea el release si existe cualquier hallazgo de severidad HIGH o CRITICAL."""
    auditor = SecurityAuditor()
    finding = SecurityFindingDTO(
        tool_source="semgrep",
        rule_id="python.lang.security.audit.sql-injection",
        severity=bad_severity,
        file_path="apps/api/src/kallicode_api/routers/tickets.py",
        line_number=88,
        description="Posible concatenación SQL insegura.",
        owasp_category="A03:2021-Injection",
    )

    report = auditor.consolidate_audit([finding], commit_sha="sha-vuln")

    assert report.passed_audit is False
    assert report.is_conforming is False
    with pytest.raises(SecurityAuditGateBlockedError, match=bad_severity):
        auditor.assert_zero_security_blockers(report)


def test_parse_gitleaks_y_sast_extrae_hallazgos_correctamente(tmp_path: Path) -> None:
    """Parsea JSON reales de Gitleaks (filtrando mocks de tests), Semgrep, Bandit y pip-audit."""
    gitleaks_file = tmp_path / "gitleaks.json"
    gitleaks_file.write_text(
        json.dumps(
            [
                {
                    "RuleID": "jwt-token",
                    "File": "packages/testing/tests/seguridad/test_jwt_seguridad.py",
                    "StartLine": 12,
                    "Description": "Synthetic test JWT mock",
                },
                {
                    "RuleID": "azure-storage-key",
                    "File": "scripts/deploy.sh",
                    "StartLine": 9,
                    "Description": "Real storage key leaked",
                },
            ]
        ),
        encoding="utf-8",
    )

    semgrep_file = tmp_path / "semgrep.json"
    semgrep_file.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.fastapi.security.audit.cors-wildcard",
                        "path": "apps/api/src/kallicode_api/main.py",
                        "start": {"line": 171},
                        "extra": {
                            "severity": "WARNING",
                            "message": "Revisar orígenes CORS permitidos",
                            "metadata": {"owasp": ["A05:2021-Security Misconfiguration"]},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    bandit_file = tmp_path / "bandit.json"
    bandit_file.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "test_id": "B608",
                        "filename": "apps/api/src/kallicode_api/servicios/db.py",
                        "line_number": 55,
                        "issue_severity": "HIGH",
                        "issue_text": "Possible SQL injection vector",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    pip_audit_file = tmp_path / "pip-audit.json"
    pip_audit_file.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "httpx",
                        "version": "0.27.0",
                        "vulns": [
                            {
                                "id": "GHSA-1234-abcd",
                                "cvss_score": 9.8,
                                "description": "Critical vulnerability example",
                                "fix_versions": ["0.28.0"],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    auditor = SecurityAuditor()
    gitleaks_findings = auditor.parse_gitleaks_report(gitleaks_file)
    # Mock sintético bajo packages/testing/tests/ ignorado -> solo queda el secreto real en scripts/
    assert len(gitleaks_findings) == 1
    assert gitleaks_findings[0].file_path == "scripts/deploy.sh"
    assert gitleaks_findings[0].severity == "CRITICAL"

    semgrep_findings = auditor.parse_sast_report(semgrep_file)
    assert len(semgrep_findings) == 1
    assert semgrep_findings[0].tool_source == "semgrep"
    assert semgrep_findings[0].severity == "MEDIUM"
    assert "A05:2021" in semgrep_findings[0].owasp_category

    bandit_findings = auditor.parse_sast_report(bandit_file)
    assert len(bandit_findings) == 1
    assert bandit_findings[0].tool_source == "bandit"
    assert bandit_findings[0].severity == "HIGH"

    pip_findings = auditor.parse_sast_report(pip_audit_file)
    assert len(pip_findings) == 1
    assert pip_findings[0].tool_source == "pip-audit"
    assert pip_findings[0].severity == "CRITICAL"


def test_inmutabilidad_dtos_security() -> None:
    """Valida ConfigDict(frozen=True) y __test__ = False en DTOs Pydantic v2."""
    for dto_cls in (SecurityFindingDTO, SecurityAuditReportDTO):
        assert dto_cls.__test__ is False

    finding = _sample_medium_finding()
    report = SecurityAuditor().consolidate_audit([finding], commit_sha="sha-frozen")

    with pytest.raises(ValidationError):
        finding.severity = "LOW"
    with pytest.raises(ValidationError):
        report.is_conforming = False
    with pytest.raises(ValidationError):
        report.critical_count = 99


def test_cli_y_orquestador_bash_offline_con_reportes_inyectados(tmp_path: Path) -> None:
    """Valida CLI Python y `scripts/security_audit_final.sh --offline` con JSON inyectados."""
    gitleaks_json = tmp_path / "gitleaks.json"
    sast_json = tmp_path / "sast.json"
    audit_json = tmp_path / "audit.json"
    json_out = tmp_path / "security-audit-report.json"
    md_out = tmp_path / "informe_seguridad_final_v1.1.md"

    gitleaks_json.write_text("[]", encoding="utf-8")
    sast_json.write_text(json.dumps({"results": []}), encoding="utf-8")
    audit_json.write_text(json.dumps({"dependencies": []}), encoding="utf-8")

    # 1. CLI Python limpio -> exit 0
    assert (
        main(
            [
                "--commit-sha",
                "sha-cli-ok",
                "--repo-root",
                str(REPO_ROOT),
                "--gitleaks-report",
                str(gitleaks_json),
                "--sast-report",
                str(sast_json),
                "--audit-report",
                str(audit_json),
                "--json-out",
                str(json_out),
                "--md-out",
                str(md_out),
                "--enforce-gate",
            ]
        )
        == 0
    )
    assert json_out.exists()
    assert md_out.exists()

    # 2. Orquestador Bash en modo --offline inyectando reportes -> exit 0
    bash_proc = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "security_audit_final.sh"),
            "--offline",
            "--commit-sha",
            "sha-bash-ok",
            "--gitleaks-report",
            str(gitleaks_json),
            "--sast-report",
            str(sast_json),
            "--audit-report",
            str(audit_json),
            "--json-out",
            str(tmp_path / "bash-sec-report.json"),
            "--md-out",
            str(tmp_path / "bash-sec-report.md"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bash_proc.returncode == 0, bash_proc.stderr

    # 3. Inyectando un secreto real -> Bash debe devolver exit 1 bloqueando el gate
    gitleaks_json.write_text(
        json.dumps([{"RuleID": "aws-access-key", "File": "apps/api/main.py", "StartLine": 5}]),
        encoding="utf-8",
    )
    blocked_proc = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "security_audit_final.sh"),
            "--offline",
            "--gitleaks-report",
            str(gitleaks_json),
            "--sast-report",
            str(sast_json),
            "--audit-report",
            str(audit_json),
            "--json-out",
            str(tmp_path / "bash-blocked.json"),
            "--md-out",
            str(tmp_path / "bash-blocked.md"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked_proc.returncode == 1


def test_cobertura_safety_cvss_owasp_fallos_y_cli_errores(tmp_path: Path) -> None:
    """Cubre formatos Safety/CVSS, bloqueo por control OWASP, Markdown con bloqueadores y CLI error."""
    auditor = SecurityAuditor()

    # Safety JSON + pip-audit con múltiples rangos CVSS y categorías OWASP inferidas
    safety_json = tmp_path / "safety.json"
    safety_json.write_text(
        json.dumps(
            {
                "vulnerabilities": [
                    {
                        "vulnerability_id": "SAFETY-99",
                        "package_name": "urllib3",
                        "severity": "MEDIUM",
                        "advisory": "Header forwarding advisory",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pip_multi_json = tmp_path / "pip-multi.json"
    pip_multi_json.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "pkg-a",
                        "version": "1.0.0",
                        "vulns": [
                            {"id": "CVE-HIGH", "cvss_score": 7.5},
                            {"id": "CVE-MED", "cvss_score": 5.0},
                            {"id": "CVE-LOW", "cvss_score": 2.0},
                            {"id": "CVE-ALIAS", "aliases": ["GHSA-abcd"]},
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    semgrep_multi_json = tmp_path / "semgrep-multi.json"
    semgrep_multi_json.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "check_id": "rule-sql-check",
                        "path": "a.py",
                        "start": {"line": 1},
                        "extra": {"severity": "ERROR", "message": "sql"},
                    },
                    {
                        "check_id": "rule-secret-check",
                        "path": "b.py",
                        "start": {"line": 2},
                        "extra": {"severity": "WARNING", "message": "key"},
                    },
                    {
                        "check_id": "rule-auth-rls",
                        "path": "c.py",
                        "start": {"line": 3},
                        "extra": {"severity": "INFO", "message": "rbac"},
                    },
                    {
                        "check_id": "rule-cors-debug",
                        "path": "d.py",
                        "start": {"line": 4},
                        "extra": {"severity": "INFO", "message": "cors"},
                    },
                    {
                        "check_id": "rule-other",
                        "path": "e.py",
                        "start": {"line": 5},
                        "extra": {"severity": "INFO", "message": "other"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    safety_findings = auditor.parse_sast_report(safety_json)
    pip_findings = auditor.parse_sast_report(pip_multi_json)
    semgrep_findings = auditor.parse_sast_report(semgrep_multi_json)
    assert safety_findings[0].tool_source == "safety"
    assert [f.severity for f in pip_findings] == ["HIGH", "MEDIUM", "LOW", "HIGH"]
    assert "A03:2021" in semgrep_findings[0].owasp_category
    assert "A02:2021" in semgrep_findings[1].owasp_category
    assert "A01:2021" in semgrep_findings[2].owasp_category
    assert "A05:2021" in semgrep_findings[3].owasp_category
    assert "A08:2021" in semgrep_findings[4].owasp_category

    # Bloqueo por control OWASP incumplido + exportación Markdown con sección de bloqueadores
    blocked_report = auditor.consolidate_audit(
        [semgrep_findings[0]],
        commit_sha="sha-owasp-fail",
        owasp_controls={"A01_Broken_Access_Control_RLS": False},
    )
    with pytest.raises(SecurityAuditGateBlockedError, match="A01_Broken_Access_Control_RLS"):
        auditor.assert_zero_security_blockers(blocked_report)

    md_blocked = auditor.export_markdown_report(blocked_report, tmp_path / "blocked.md")
    md_text = md_blocked.read_text(encoding="utf-8")
    assert "Bloqueadores Detectados" in md_text
    assert "No se registraron vulnerabilidades SAST/dependencias activas" in md_text

    # Manejo de JSON inválido y archivos inexistentes en parseadores y CLI
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{invalid", encoding="utf-8")
    assert main(["--sast-report", str(bad_json)]) == 1
    with pytest.raises(FileNotFoundError):
        auditor.parse_gitleaks_report(tmp_path / "missing.json")
    with pytest.raises(FileNotFoundError):
        auditor.parse_sast_report(tmp_path / "missing.json")

    empty_gitleaks = tmp_path / "empty_gl.json"
    empty_gitleaks.write_text("", encoding="utf-8")
    assert auditor.parse_gitleaks_report(empty_gitleaks) == []


def test_secrets_mock_y_bordes_security_auditor(tmp_path: Path) -> None:
    """Cubre LocalKeyVaultMock (>= 95%) y ramas restantes de SecurityAuditor."""
    from kallicode_testing.secrets_mock import LocalKeyVaultMock
    from kallicode_testing.security_auditor import _infer_owasp_category, _map_cvss_to_severity

    # 1. LocalKeyVaultMock: llave explícita hex, clave inexistente, corrupción AES-GCM y doble purge
    explicit_key = "00" * 32
    vault = LocalKeyVaultMock(master_key_hex=explicit_key)
    try:
        vault.set_secret("api_token", "valor-secreto-123")
        assert vault.get_secret("api_token") == "valor-secreto-123"
        assert vault.get_secret("no_existe") is None

        # Corromper ciphertext para cubrir bloque except en get_secret
        other_vault = LocalKeyVaultMock(master_key_hex="ff" * 32)
        assert other_vault.get_secret("api_token") is None
    finally:
        vault.purge_vault()
        vault.purge_vault()

    # 2. Bordes de helpers y parseadores en SecurityAuditor
    assert _infer_owasp_category("custom", "A07:2021-Auth") == "A07:2021-Auth"
    assert "A06:2021" in _infer_owasp_category("cve-2026-0001")
    assert _map_cvss_to_severity(None, None) == "HIGH"

    auditor = SecurityAuditor()
    gl_null = tmp_path / "gl_null.json"
    gl_null.write_text("null", encoding="utf-8")
    assert auditor.parse_gitleaks_report(gl_null) == []

    gl_dict = tmp_path / "gl_dict.json"
    gl_dict.write_text(
        json.dumps({"findings": [{"RuleID": "r1", "File": "apps/a.py", "StartLine": 2}]}),
        encoding="utf-8",
    )
    assert len(auditor.parse_gitleaks_report(gl_dict)) == 1

    gl_other = tmp_path / "gl_other.json"
    gl_other.write_text(json.dumps({"other": 1}), encoding="utf-8")
    assert auditor.parse_gitleaks_report(gl_other) == []

    sast_empty = tmp_path / "sast_empty.json"
    sast_empty.write_text("", encoding="utf-8")
    assert auditor.parse_sast_report(sast_empty) == []

    sast_list = tmp_path / "sast_list.json"
    sast_list.write_text("[]", encoding="utf-8")
    assert auditor.parse_sast_report(sast_list) == []

    # Reporte manualmente marcado no conforme sin hallazgos individuales + CLI --enforce-gate exit 1
    manual_non_conforming = SecurityAuditReportDTO(
        commit_sha="sha-manual",
        total_findings=0,
        critical_count=0,
        high_count=0,
        medium_count=0,
        low_count=0,
        secrets_detected=0,
        passed_audit=False,
        is_conforming=False,
    )
    with pytest.raises(SecurityAuditGateBlockedError, match="is_conforming=False"):
        auditor.assert_zero_security_blockers(manual_non_conforming)

    assert (
        main(
            [
                "--gitleaks-report",
                str(gl_dict),
                "--json-out",
                str(tmp_path / "out.json"),
                "--md-out",
                str(tmp_path / "out.md"),
                "--enforce-gate",
            ]
        )
        == 1
    )
