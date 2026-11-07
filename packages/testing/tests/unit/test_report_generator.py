"""
Ticket: KC-QA-B4-06
Suite de pruebas unitarias y de resiliencia para HTMLReportGenerator (report_generator.py).
Valida:
1. test_generacion_html_autocontenido: Estructura HTML íntegra, CSS/JS embebidos y ausencia de referencias remotas.
2. test_generacion_matriz_bdd_y_evidencias: Renderizado de criterios AC-n, hashes SHA-256 y trazabilidad de evidencias.
3. test_graficos_svg_sin_dependencias_externas: Gráficos de anillo y barras en SVG puro nativo.
4. test_resistencia_a_datos_incompletos: Tolerancia a fallos con fuentes parciales, nulas o diccionarios vacíos.
5. test_seccion_casos_en_cuarentena: Renderizado y etiquetado de pruebas en cuarentena.
6. test_inmutabilidad_dto_y_guardado_en_disco: Inmutabilidad pydantic frozen y persistencia con save_report.
7. test_deteccion_violacion_cero_nube: Prevención estricta ante inyecciones de scripts o estilos remotos.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.evidence_sealer import EvidenceFileDTO, SealedEvidencePackageDTO
from kallicode_testing.report_generator import (
    HTMLReportGenerator,
    generate_bar_svg,
    generate_donut_svg,
)
from kallicode_testing.run_suite import SuiteExecutionReport, TestCaseResultDTO
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Fixture de Datos Completos para Reporte
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_suite_report() -> SuiteExecutionReport:
    results = [
        TestCaseResultDTO(
            criterion_id="AC-01",
            kind="api",
            status="passed",
            case_path="tests/api/test_auth.py",
            duration_ms=45.2,
            evidence_files=["api_response.json"],
        ),
        TestCaseResultDTO(
            criterion_id="AC-02",
            kind="web",
            status="passed",
            case_path="e2e/portal/checkout.spec.ts",
            duration_ms=1200.5,
            evidence_files=["checkout.png", "checkout_trace.zip"],
        ),
        TestCaseResultDTO(
            criterion_id="AC-03",
            kind="mobile",
            status="failed",
            case_path="mobile/flows/login.yaml",
            duration_ms=850.0,
            error_summary="Timeout waiting for element #login_button",
            evidence_files=["login_fail.png"],
        ),
        TestCaseResultDTO(
            criterion_id="AC-04",
            kind="web",
            status="flaky",
            case_path="e2e/portal/flaky_nav.spec.ts",
            duration_ms=410.0,
            evidence_files=["flaky.log"],
        ),
    ]
    return SuiteExecutionReport(
        job_id="job-ci-884",
        tenant_id="tenant-acme",
        total_cases=4,
        passed_count=2,
        failed_count=1,
        flaky_count=1,
        is_success=False,
        results=results,
    )


@pytest.fixture
def mock_evidence_package() -> SealedEvidencePackageDTO:
    evidence_files = [
        EvidenceFileDTO(
            criterion_id="AC-02",
            file_type="screenshot",
            file_path="/tmp/checkout.png",
            sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            blob_ref="evidence/tenant-acme/job-ci-884/AC-02/checkout.png",
            created_at="2026-11-05T19:00:00Z",
        ),
        EvidenceFileDTO(
            criterion_id="AC-03",
            file_type="screenshot",
            file_path="/tmp/login_fail.png",
            sha256_hash="cca92437651a0ae50edb483c65c4013de25c4ef95ef118d035416ef39f15779c",
            blob_ref="evidence/tenant-acme/job-ci-884/AC-03/login_fail.png",
            created_at="2026-11-05T19:01:00Z",
        ),
    ]
    return SealedEvidencePackageDTO(
        job_id="job-ci-884",
        tenant_id="tenant-acme",
        spec_id="SPEC-AUTH-CHECKOUT",
        overall_hash="8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4",
        hmac_signature="sig-hmac-sha256-verified-mock-123",
        evidence_files=evidence_files,
    )


# ---------------------------------------------------------------------------
# Prueba 1: test_generacion_html_autocontenido
# ---------------------------------------------------------------------------
def test_generacion_html_autocontenido(
    mock_suite_report: SuiteExecutionReport,
    mock_evidence_package: SealedEvidencePackageDTO,
) -> None:
    """
    Prueba 1: Generar un informe completo.
    Validaciones:
    - Contiene estructura HTML5 válida (<!DOCTYPE html>, <html>, <head>, <body>).
    - Contiene CSS embebido directamente en etiqueta <style>.
    - Contiene JavaScript interactivo embebido directamente en etiqueta <script>.
    - NO contiene referencias remotas (https://) en scripts, links, imágenes o iframes (Cero CDN).
    """
    generator = HTMLReportGenerator()
    metrics = {"line_rate": 88.5, "branch_rate": 81.2}
    quarantine = [
        {"test_id": "test_flaky_auth", "reason": "Red local inestable", "status": "quarantined"}
    ]

    report_dto = generator.build_report_dto(
        suite_report=mock_suite_report,
        evidence_package=mock_evidence_package,
        metrics=metrics,
        quarantine_list=quarantine,
        spec_id="SPEC-AUTH-CHECKOUT",
    )

    html_content = generator.render_html(report_dto, evidence_package=mock_evidence_package)

    # 1. Validación de estructura HTML básica
    assert "<!DOCTYPE html>" in html_content
    assert '<html lang="es">' in html_content
    assert "<head>" in html_content
    assert "<body>" in html_content
    assert "</html>" in html_content

    # 2. Validación de CSS y JS embebidos
    assert "<style>" in html_content
    assert ":root {" in html_content
    assert "<script>" in html_content
    assert "function filterCriteria" in html_content
    assert "function copyHash" in html_content

    # 3. Validación de ausencia absoluta de dependencias remotas (Cero-Nube)
    assert 'src="https://' not in html_content
    assert 'src="http://' not in html_content
    assert 'href="https://' not in html_content
    assert 'href="http://' not in html_content
    assert "cdn.jsdelivr.net" not in html_content
    assert "tailwindcss" not in html_content
    assert "bootstrap" not in html_content
    assert "googleapis.com" not in html_content


# ---------------------------------------------------------------------------
# Prueba 2: test_generacion_matriz_bdd_y_evidencias
# ---------------------------------------------------------------------------
def test_generacion_matriz_bdd_y_evidencias(
    mock_suite_report: SuiteExecutionReport,
    mock_evidence_package: SealedEvidencePackageDTO,
) -> None:
    """
    Prueba 2: Verificar representación correcta de:
    - Criterios AC-n en la matriz BDD.
    - Evidencias selladas asociadas.
    - Hashes SHA-256 visibles.
    - Estados passed, failed y flaky.
    """
    generator = HTMLReportGenerator()
    report_dto = generator.build_report_dto(
        suite_report=mock_suite_report,
        evidence_package=mock_evidence_package,
        spec_id="SPEC-AUTH-CHECKOUT",
    )

    html_content = generator.render_html(report_dto, evidence_package=mock_evidence_package)

    # Verificar presencia de criterios
    assert "AC-01" in html_content
    assert "AC-02" in html_content
    assert "AC-03" in html_content
    assert "AC-04" in html_content

    # Verificar rutas y tipos
    assert "checkout.spec.ts" in html_content
    assert "login.yaml" in html_content

    # Verificar estados
    assert "badge-passed" in html_content
    assert "badge-failed" in html_content
    assert "badge-flaky" in html_content

    # Verificar tabla de evidencias WORM y hashes SHA-256
    assert "evidence/tenant-acme/job-ci-884/AC-02/checkout.png" in html_content
    assert "e3b0c44298fc1c14" in html_content  # Fragmento del hash SHA-256
    assert mock_evidence_package.overall_hash in html_content


# ---------------------------------------------------------------------------
# Prueba 3: test_graficos_svg_sin_dependencias_externas
# ---------------------------------------------------------------------------
def test_graficos_svg_sin_dependencias_externas() -> None:
    """
    Prueba 3: Verificar que los gráficos se rendericen mediante SVG puro:
    - Sin librerías externas de gráficos (Chart.js, Canvas remoto).
    - Presencia de elementos SVG estándar: <svg>, <circle>, <rect>, <text>.
    """
    donut_svg = generate_donut_svg(85.5, "Cobertura", color_hex="#10b981", size=140)
    assert "<svg" in donut_svg
    assert "<circle" in donut_svg
    assert "<text" in donut_svg
    assert "85.5%" in donut_svg
    assert 'role="img"' in donut_svg

    bar_svg = generate_bar_svg(72.0, width=200, height=14)
    assert "<svg" in bar_svg
    assert "<rect" in bar_svg
    assert 'role="progressbar"' in bar_svg
    assert 'aria-valuenow="72.0"' in bar_svg


# ---------------------------------------------------------------------------
# Prueba 4: test_resistencia_a_datos_incompletos
# ---------------------------------------------------------------------------
def test_resistencia_a_datos_incompletos() -> None:
    """
    Prueba 4: Simular corridas con datos nulos, métricas parciales y listas vacías.
    Resultado esperado:
    - El generador no debe lanzar excepciones no controladas.
    - Debe retornar un QualityReportDTO válido con valores por defecto consistentes.
    - El HTML debe renderizarse normalmente con mensajes informativos de sección vacía.
    """
    generator = HTMLReportGenerator()

    # Invocación con None en todos los argumentos
    report_dto = generator.build_report_dto(
        suite_report=None,
        evidence_package=None,
        metrics=None,
        quarantine_list=None,
    )

    assert report_dto.job_id == "job-unknown"
    assert report_dto.tenant_id == "tenant-default"
    assert report_dto.spec_id == "spec-default"
    assert report_dto.suite_summary["total"] == 0
    assert report_dto.suite_summary["success_rate"] == 0.0
    assert report_dto.coverage_summary["line_rate"] == 0.0
    assert report_dto.criteria_matrix == []
    assert report_dto.quarantine_cases == []
    assert len(report_dto.overall_sha256) == 64  # Hash SHA-256 determinista de respaldo

    # Renderizado debe ser exitoso y contener avisos de datos vacíos
    html_content = generator.render_html(report_dto)
    assert "No se registraron criterios evaluados" in html_content
    assert "No se adjuntaron evidencias WORM" in html_content


# ---------------------------------------------------------------------------
# Prueba 5: test_seccion_casos_en_cuarentena
# ---------------------------------------------------------------------------
def test_seccion_casos_en_cuarentena() -> None:
    """
    Prueba 5: Validar que los casos en cuarentena se destaquen con su motivo y badge.
    """
    generator = HTMLReportGenerator()
    quarantine_items = [
        {
            "test_id": "test_sync_gate_approval",
            "reason": "Condición de carrera en Redis local (Falla intermitente 1 de 10)",
            "status": "quarantined",
        }
    ]

    report_dto = generator.build_report_dto(
        suite_report={"total": 1, "passed": 0, "failed": 1, "flaky": 1},
        quarantine_list=quarantine_items,
        job_id="job-quarantine-01",
    )

    html_content = generator.render_html(report_dto)
    assert "Casos Aislados en Cuarentena" in html_content
    assert "test_sync_gate_approval" in html_content
    assert "Condición de carrera en Redis local" in html_content
    assert "badge-flaky" in html_content


# ---------------------------------------------------------------------------
# Prueba 6: test_inmutabilidad_dto_y_guardado_en_disco
# ---------------------------------------------------------------------------
def test_inmutabilidad_dto_y_guardado_en_disco(tmp_path: Path) -> None:
    """
    Prueba 6: Validar inmutabilidad pydantic (frozen=True) y persistencia atómica en disco.
    """
    generator = HTMLReportGenerator()
    report_dto = generator.build_report_dto(
        suite_report={"total": 2, "passed": 2, "failed": 0, "flaky": 0},
        job_id="job-freeze-01",
        tenant_id="tenant-secure",
    )

    # Inmutabilidad: modificar atributo debe levantar ValidationError
    with pytest.raises(ValidationError):
        report_dto.job_id = "job-mutated"

    # Persistencia en disco
    out_file = tmp_path / "reports" / "summary.html"
    saved_path = generator.save_report(report_dto, out_file)

    assert saved_path.exists()
    assert saved_path == out_file
    disk_content = saved_path.read_text(encoding="utf-8")
    assert "job-freeze-01" in disk_content
    assert "PASSED (VERDE)" in disk_content


# ---------------------------------------------------------------------------
# Prueba 7: test_deteccion_violacion_cero_nube
# ---------------------------------------------------------------------------
def test_deteccion_violacion_cero_nube() -> None:
    """
    Prueba 7: Asegurar que si una plantilla es alterada para inyectar recursos
    remotos externos (https://), el método render_html levante ValueError inmediatamente.
    """
    generator = HTMLReportGenerator()
    # Adulterar intencionalmente la plantilla interna para inyectar un CDN remoto
    generator._template = generator._jinja_env.from_string(
        '<html><head><script src="https://cdn.malicious.com/tracker.js"></script></head><body></body></html>'
    )

    report_dto = generator.build_report_dto(suite_report=None)

    with pytest.raises(ValueError, match="Violación de aislamiento Cero-Nube detectada"):
        generator.render_html(report_dto)


# ---------------------------------------------------------------------------
# Prueba 8: test_cobertura_casos_borde_y_formatos_metricas
# ---------------------------------------------------------------------------
def test_cobertura_casos_borde_y_formatos_metricas() -> None:
    """
    Prueba 8: Casos de borde de métricas, colores de donut y tipos de datos alternativos:
    - Donut con porcentaje < 60% (color rojo #ef4444) y entre 60 y 80% (ámbar #f59e0b).
    - Métricas pasadas como objeto con atributos line_rate / branch_rate.
    - Cuarentena y evidencias en formatos diccionario / objeto custom.
    """
    donut_red = generate_donut_svg(45.0, "Bajo")
    assert "#ef4444" in donut_red

    donut_amber = generate_donut_svg(75.0, "Medio")
    assert "#f59e0b" in donut_amber

    class DummyMetrics:
        line_rate = 0.85
        branch_rate = 0.72

    class DummyQuarantine:
        def model_dump(self) -> dict[str, str]:
            return {"id": "flaky_01", "reason": "timeout", "status": "quarantined"}

    class DummyEvidence:
        def __init__(self) -> None:
            self.criterion_id = "AC-99"
            self.file_type = "log"
            self.blob_ref = "evidence/dummy.log"
            self.sha256_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"

    class DummyEvidencePackage:
        def __init__(self) -> None:
            self.overall_hash = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
            self.evidence_files = [DummyEvidence()]

    generator = HTMLReportGenerator()
    report = generator.build_report_dto(
        suite_report={"total": 1, "passed": 1, "failed": 0, "flaky": 0},
        evidence_package=DummyEvidencePackage(),
        metrics=DummyMetrics(),
        quarantine_list=[DummyQuarantine()],
    )

    assert report.coverage_summary["line_rate"] == 85.0
    assert report.coverage_summary["branch_rate"] == 72.0
    assert len(report.quarantine_cases) == 1

    html_out = generator.render_html(report, evidence_package=DummyEvidencePackage())
    assert "AC-99" in html_out
    assert "evidence/dummy.log" in html_out
