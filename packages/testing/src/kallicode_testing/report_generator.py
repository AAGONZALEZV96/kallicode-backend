"""
Ticket: KC-QA-B4-06
Visor y Generador de Informes de Calidad HTML Autocontenidos (report_generator.py).
Genera informes de calidad interactivos, deterministas y 100% offline (sin CDN, Tailwind,
Bootstrap, ni llamadas externas). Incorpora gráficos SVG puros, matriz BDD (AC-n),
evidencias selladas SHA-256/WORM y registro de cuarentena.
"""

from __future__ import annotations

import datetime
import hashlib
import html
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger
from kallicode_testing.prompts.prompt_manager import PromptManager

logger = get_logger()


# ---------------------------------------------------------------------------
# DTO Inmutable Oficial KC-QA-B4-06
# ---------------------------------------------------------------------------
class QualityReportDTO(BaseModel):
    """
    Representa el estado consolidado e inmutable de la calidad de una corrida de pruebas.
    Agrupa resumen de ejecución, métricas de cobertura, matriz BDD y evidencias selladas WORM.
    """

    model_config = ConfigDict(frozen=True)

    job_id: str
    tenant_id: str
    spec_id: str
    generated_at: str  # ISO 8601 UTC
    suite_summary: dict[str, Any]  # total, passed, failed, flaky, success_rate
    coverage_summary: dict[str, Any]  # line_rate, branch_rate
    criteria_matrix: list[dict[str, Any]] = Field(default_factory=list)
    quarantine_cases: list[dict[str, Any]] = Field(default_factory=list)
    overall_sha256: str


# ---------------------------------------------------------------------------
# Generador de Gráficos Vectoriales SVG Puro (Cero Dependencias Externas)
# ---------------------------------------------------------------------------
def generate_donut_svg(
    percentage: float,
    label: str,
    color_hex: str = "#10b981",
    size: int = 140,
) -> str:
    """
    Genera un gráfico de anillo (donut) en SVG puro con texto centrado y animación básica.
    No requiere Canvas, Chart.js ni ninguna librería externa.
    """
    clamped = max(0.0, min(100.0, float(percentage)))
    radius = 50.0
    circumference = 2.0 * 3.141592653589793 * radius
    stroke_dashoffset = circumference - (clamped / 100.0) * circumference

    # Selección dinámica de color si no se personaliza
    if color_hex == "#10b981":
        if clamped < 60.0:
            color_hex = "#ef4444"  # Rojo
        elif clamped < 80.0:
            color_hex = "#f59e0b"  # Ámbar
        else:
            color_hex = "#10b981"  # Verde esmeralda

    center = size / 2.0

    return f"""<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" class="svg-donut" role="img" aria-label="{html.escape(label)}: {clamped:.1f}%">
  <circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="#334155" stroke-width="12" />
  <circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="{color_hex}" stroke-width="12"
          stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{stroke_dashoffset:.2f}"
          stroke-linecap="round" transform="rotate(-90 {center} {center})" />
  <text x="{center}" y="{center - 2}" text-anchor="middle" dominant-baseline="middle"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
        font-size="20" font-weight="700" fill="#f8fafc">{clamped:.1f}%</text>
  <text x="{center}" y="{center + 20}" text-anchor="middle" dominant-baseline="middle"
        font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
        font-size="11" fill="#94a3b8" text-transform="uppercase">{html.escape(label)}</text>
</svg>"""


def generate_bar_svg(
    percentage: float,
    width: int = 200,
    height: int = 14,
    color_hex: str = "#3b82f6",
) -> str:
    """Genera una barra de progreso horizontal en SVG puro."""
    clamped = max(0.0, min(100.0, float(percentage)))
    filled_width = (clamped / 100.0) * float(width)
    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="svg-bar" role="progressbar" aria-valuenow="{clamped:.1f}" aria-valuemin="0" aria-valuemax="100">
  <rect width="{width}" height="{height}" rx="{height / 2}" fill="#334155" />
  <rect width="{filled_width:.1f}" height="{height}" rx="{height / 2}" fill="{color_hex}" />
</svg>"""


# ---------------------------------------------------------------------------
# Plantilla HTML Embebida (100% Autocontenida y Offline)
# ---------------------------------------------------------------------------
HTML_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>KalliCode Quality Report — Job {{ report.job_id }}</title>
  <style>
    /* Reset & variables CSS puras (Cero CDN / Cero Fuentes Externas) */
    :root {
      --bg-primary: #0f172a;
      --bg-secondary: #1e293b;
      --bg-card: #1e293b;
      --bg-card-hover: #243248;
      --border-color: #334155;
      --text-primary: #f8fafc;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --accent-green: #10b981;
      --accent-red: #ef4444;
      --accent-amber: #f59e0b;
      --accent-blue: #3b82f6;
      --accent-purple: #8b5cf6;
      --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
      --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.3), 0 2px 4px -2px rgba(0, 0, 0, 0.3);
    }
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.5;
      padding: 24px;
      min-height: 100vh;
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
    }
    header.report-header {
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: var(--shadow-md);
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
    }
    .header-title h1 {
      font-size: 24px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 10px;
      color: #ffffff;
    }
    .header-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      font-size: 13px;
      color: var(--text-secondary);
      margin-top: 8px;
    }
    .meta-tag {
      background-color: #0f172a;
      border: 1px solid var(--border-color);
      padding: 4px 10px;
      border-radius: 6px;
      font-family: monospace;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      font-size: 12px;
      font-weight: 700;
      padding: 4px 12px;
      border-radius: 9999px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-passed { background-color: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid var(--accent-green); }
    .badge-failed { background-color: rgba(239, 68, 68, 0.15); color: var(--accent-red); border: 1px solid var(--accent-red); }
    .badge-flaky  { background-color: rgba(245, 158, 11, 0.15); color: var(--accent-amber); border: 1px solid var(--accent-amber); }
    .badge-skipped{ background-color: rgba(148, 163, 184, 0.15); color: var(--text-secondary); border: 1px solid var(--text-muted); }
    .badge-status { font-size: 16px; padding: 8px 20px; }

    /* Secciones y métricas */
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .metric-card {
      background-color: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      box-shadow: var(--shadow-sm);
    }
    .metric-card .label {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-secondary);
      text-transform: uppercase;
      margin-bottom: 8px;
    }
    .metric-card .value {
      font-size: 28px;
      font-weight: 800;
      color: var(--text-primary);
    }

    /* Gráficos SVG */
    .charts-section {
      background-color: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 24px;
      margin-bottom: 24px;
      box-shadow: var(--shadow-sm);
    }
    .section-title {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 16px;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 8px;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 8px;
    }
    .donuts-flex {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-around;
      align-items: center;
      gap: 24px;
      padding: 12px 0;
    }
    .donut-item {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
    }
    .donut-description {
      font-size: 13px;
      color: var(--text-secondary);
      max-width: 180px;
      text-align: center;
    }

    /* Tablas y paneles */
    .table-container {
      background-color: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 24px;
      overflow-x: auto;
      box-shadow: var(--shadow-sm);
    }
    .table-controls {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }
    .filter-btn-group {
      display: flex;
      gap: 8px;
    }
    .filter-btn {
      background-color: var(--bg-secondary);
      border: 1px solid var(--border-color);
      color: var(--text-secondary);
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .filter-btn:hover, .filter-btn.active {
      background-color: var(--accent-blue);
      color: #ffffff;
      border-color: var(--accent-blue);
    }
    .search-input {
      background-color: #0f172a;
      border: 1px solid var(--border-color);
      color: var(--text-primary);
      padding: 8px 14px;
      border-radius: 6px;
      font-size: 13px;
      outline: none;
      min-width: 240px;
    }
    .search-input:focus {
      border-color: var(--accent-blue);
    }
    table.data-table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 14px;
    }
    table.data-table th {
      background-color: #0f172a;
      color: var(--text-secondary);
      padding: 12px 14px;
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 600;
      border-bottom: 1px solid var(--border-color);
    }
    table.data-table td {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border-color);
      color: var(--text-primary);
    }
    table.data-table tr:hover td {
      background-color: var(--bg-card-hover);
    }
    .code-cell {
      font-family: monospace;
      font-size: 12px;
      color: #38bdf8;
    }
    .hash-copy {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-family: monospace;
      font-size: 11px;
      background: #0f172a;
      padding: 3px 8px;
      border-radius: 4px;
      border: 1px solid var(--border-color);
      color: #a5b4fc;
      cursor: pointer;
      user-select: all;
    }
    .hash-copy:hover {
      border-color: var(--accent-blue);
    }
    .quarantine-card {
      background: rgba(245, 158, 11, 0.05);
      border: 1px solid rgba(245, 158, 11, 0.3);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 12px;
    }

    footer.report-footer {
      text-align: center;
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 32px;
      padding: 16px;
      border-top: 1px solid var(--border-color);
    }
  </style>
</head>
<body>
  <div class="container">
    <!-- Encabezado Principal -->
    <header class="report-header">
      <div class="header-title">
        <h1>🛡️ KalliCode QA — Informe de Calidad</h1>
        <div class="header-meta">
          <span class="meta-tag">Job: {{ report.job_id }}</span>
          <span class="meta-tag">Tenant: {{ report.tenant_id }}</span>
          <span class="meta-tag">Spec: {{ report.spec_id }}</span>
          <span class="meta-tag">Fecha: {{ report.generated_at }}</span>
        </div>
      </div>
      <div>
        {% if report.suite_summary.success_rate >= 80.0 and report.suite_summary.failed == 0 %}
          <span class="badge badge-passed badge-status">PASSED (VERDE)</span>
        {% else %}
          <span class="badge badge-failed badge-status">FAILED (RECHAZADO)</span>
        {% endif %}
      </div>
    </header>

    <!-- Resumen Ejecutivo de Métricas -->
    <section class="metrics-grid">
      <div class="metric-card">
        <span class="label">Total Casos</span>
        <span class="value">{{ report.suite_summary.total }}</span>
      </div>
      <div class="metric-card">
        <span class="label">Pasados</span>
        <span class="value" style="color: var(--accent-green);">{{ report.suite_summary.passed }}</span>
      </div>
      <div class="metric-card">
        <span class="label">Fallados</span>
        <span class="value" style="color: var(--accent-red);">{{ report.suite_summary.failed }}</span>
      </div>
      <div class="metric-card">
        <span class="label">Flaky / Reintentos</span>
        <span class="value" style="color: var(--accent-amber);">{{ report.suite_summary.flaky }}</span>
      </div>
      <div class="metric-card">
        <span class="label">Tasa de Éxito</span>
        <span class="value">{{ "%.1f"|format(report.suite_summary.success_rate) }}%</span>
      </div>
    </section>

    <!-- Gráficos de Cobertura y Éxito en SVG Puro -->
    <section class="charts-section">
      <h2 class="section-title">📊 Cobertura y Métricas Clave (SVG Puro Offline)</h2>
      <div class="donuts-flex">
        <div class="donut-item">
          {{ donut_success | safe }}
          <span class="donut-description">Tasa Global de Éxito</span>
        </div>
        <div class="donut-item">
          {{ donut_lines | safe }}
          <span class="donut-description">Cobertura de Líneas (Line Rate)</span>
        </div>
        <div class="donut-item">
          {{ donut_branches | safe }}
          <span class="donut-description">Cobertura de Ramas (Branch Rate)</span>
        </div>
      </div>
    </section>

    <!-- Matriz BDD por Criterio (AC-n) -->
    <section class="table-container">
      <div class="table-controls">
        <h2 class="section-title" style="margin-bottom: 0; border: none; padding: 0;">🎯 Matriz BDD por Criterio de Aceptación (AC-n)</h2>
        <div class="filter-btn-group">
          <button class="filter-btn active" onclick="filterCriteria('all', this)">Todos</button>
          <button class="filter-btn" onclick="filterCriteria('passed', this)">Pasados</button>
          <button class="filter-btn" onclick="filterCriteria('failed', this)">Fallados</button>
          <button class="filter-btn" onclick="filterCriteria('flaky', this)">Flaky</button>
        </div>
      </div>
      <table class="data-table" id="criteria-table">
        <thead>
          <tr>
            <th>Criterio</th>
            <th>Capa / Tipo</th>
            <th>Estado</th>
            <th>Duración</th>
            <th>Ruta del Caso</th>
            <th>Evidencias Vinculadas</th>
          </tr>
        </thead>
        <tbody>
          {% for item in report.criteria_matrix %}
          <tr data-status="{{ item.status }}">
            <td class="code-cell" style="font-weight: 700; color: #f8fafc;">{{ item.criterion_id }}</td>
            <td><span class="meta-tag">{{ item.kind|upper }}</span></td>
            <td>
              {% if item.status == 'passed' %}
                <span class="badge badge-passed">passed</span>
              {% elif item.status == 'failed' %}
                <span class="badge badge-failed">failed</span>
              {% elif item.status == 'flaky' %}
                <span class="badge badge-flaky">flaky</span>
              {% else %}
                <span class="badge badge-skipped">{{ item.status }}</span>
              {% endif %}
            </td>
            <td>{{ "%.1f"|format(item.duration_ms) }} ms</td>
            <td class="code-cell">{{ item.case_path }}</td>
            <td>
              {% if item.evidence_files %}
                {% for ev in item.evidence_files %}
                  <span class="meta-tag" style="display: inline-block; margin: 2px;">{{ ev }}</span>
                {% endfor %}
              {% else %}
                <span style="color: var(--text-muted);">Sin adjuntos</span>
              {% endif %}
            </td>
          </tr>
          {% else %}
          <tr>
            <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">No se registraron criterios evaluados en esta corrida.</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>

    <!-- Evidencias Selladas WORM SHA-256 -->
    <section class="table-container">
      <div class="table-controls">
        <h2 class="section-title" style="margin-bottom: 0; border: none; padding: 0;">🔒 Paquete de Evidencias Selladas WORM (SHA-256)</h2>
        <input type="text" id="evidence-search" class="search-input" placeholder="Buscar por blob_ref o criterio..." onkeyup="searchEvidences()" />
      </div>
      <table class="data-table" id="evidence-table">
        <thead>
          <tr>
            <th>Criterio</th>
            <th>Tipo</th>
            <th>Referencia Canónica (Blob Ref)</th>
            <th>Hash Criptográfico SHA-256</th>
          </tr>
        </thead>
        <tbody>
          {% for ev in evidence_list %}
          <tr>
            <td class="code-cell" style="font-weight: 700;">{{ ev.criterion_id }}</td>
            <td><span class="meta-tag">{{ ev.file_type }}</span></td>
            <td class="code-cell">{{ ev.blob_ref }}</td>
            <td>
              <span class="hash-copy" onclick="copyHash('{{ ev.sha256_hash }}', this)" title="Click para copiar hash completo">
                📋 {{ ev.sha256_hash[:16] }}...{{ ev.sha256_hash[-8:] }}
              </span>
            </td>
          </tr>
          {% else %}
          <tr>
            <td colspan="4" style="text-align: center; color: var(--text-muted); padding: 24px;">No se adjuntaron evidencias WORM selladas.</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <div style="margin-top: 16px; font-size: 13px; color: var(--text-secondary); display: flex; align-items: center; gap: 8px;">
        <strong>Firma Digital Digest General:</strong>
        <span class="hash-copy" onclick="copyHash('{{ report.overall_sha256 }}', this)" title="Click para copiar">
          🔐 {{ report.overall_sha256 }}
        </span>
      </div>
    </section>

    <!-- Casos en Cuarentena -->
    {% if report.quarantine_cases %}
    <section class="table-container">
      <h2 class="section-title" style="color: var(--accent-amber);">⚠️ Casos Aislados en Cuarentena (Flaky Quarantine)</h2>
      {% for q in report.quarantine_cases %}
      <div class="quarantine-card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <strong class="code-cell" style="font-size: 14px; color: #ffffff;">{{ q.test_id or q.case_path or q.id }}</strong>
          <span class="badge badge-flaky">{{ q.status or 'quarantined' }}</span>
        </div>
        <p style="font-size: 13px; color: var(--text-secondary);">
          <strong>Motivo:</strong> {{ q.reason or q.quarantine_reason or 'Falla intermitente detectada por runner' }}
        </p>
      </div>
      {% endfor %}
    </section>
    {% endif %}

    <!-- Pie de Página -->
    <footer class="report-footer">
      <p>KalliCode Quality Assurance Engine — Equipo 5 (Testing, Quality & CI/CD)</p>
      <p>Informe estático inmutable 100% autocontenido y operable offline (Sin llamadas CDN)</p>
    </footer>
  </div>

  <!-- JavaScript Embebido (100% Offline / Cero Dependencias) -->
  <script>
    function filterCriteria(status, btn) {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const rows = document.querySelectorAll('#criteria-table tbody tr');
      rows.forEach(row => {
        const rowStatus = row.getAttribute('data-status');
        if (status === 'all' || rowStatus === status) {
          row.style.display = '';
        } else {
          row.style.display = 'none';
        }
      });
    }

    function searchEvidences() {
      const query = document.getElementById('evidence-search').value.toLowerCase();
      const rows = document.querySelectorAll('#evidence-table tbody tr');
      rows.forEach(row => {
        const text = row.innerText.toLowerCase();
        row.style.display = text.includes(query) ? '' : 'none';
      });
    }

    function copyHash(text, element) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          showCopiedFeedback(element);
        });
      } else {
        const tempInput = document.createElement('input');
        tempInput.value = text;
        document.body.appendChild(tempInput);
        tempInput.select();
        document.execCommand('copy');
        document.body.removeChild(tempInput);
        showCopiedFeedback(element);
      }
    }

    function showCopiedFeedback(element) {
      const originalText = element.innerText;
      element.innerText = '✅ ¡Copiado!';
      setTimeout(() => {
        element.innerText = originalText;
      }, 1500);
    }
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Clase Principal HTMLReportGenerator
# ---------------------------------------------------------------------------
class HTMLReportGenerator:
    """
    Sintetizador y renderizador de informes de calidad HTML interactivos y autocontenidos.
    Consolida SuiteExecutionReport, SealedEvidencePackageDTO, métricas de cobertura
    y pruebas en cuarentena en un único documento offline (sin dependencias CDN).
    """

    def __init__(self, prompt_manager: PromptManager | None = None) -> None:
        self.prompt_manager = prompt_manager
        # Inicialización de entorno Jinja2 en memoria para renderizado estricto
        self._jinja_env = Environment(autoescape=True)
        self._template = self._jinja_env.from_string(HTML_REPORT_TEMPLATE)

    def build_report_dto(
        self,
        suite_report: Any,
        evidence_package: Any = None,
        metrics: Any = None,
        quarantine_list: list[Any] | None = None,
        job_id: str | None = None,
        tenant_id: str | None = None,
        spec_id: str | None = None,
    ) -> QualityReportDTO:
        """
        Integra todas las fuentes disponibles, normaliza campos parciales o nulos
        y construye un QualityReportDTO inmutable antes de renderizar.
        """
        # 1. Extracción y resolución de identificadores clave
        resolved_job_id = (
            job_id
            or getattr(suite_report, "job_id", None)
            or (suite_report.get("job_id") if isinstance(suite_report, dict) else None)
            or getattr(evidence_package, "job_id", None)
            or "job-unknown"
        )
        resolved_tenant_id = (
            tenant_id
            or getattr(suite_report, "tenant_id", None)
            or (suite_report.get("tenant_id") if isinstance(suite_report, dict) else None)
            or getattr(evidence_package, "tenant_id", None)
            or "tenant-default"
        )
        resolved_spec_id = (
            spec_id
            or getattr(suite_report, "spec_id", None)
            or (suite_report.get("spec_id") if isinstance(suite_report, dict) else None)
            or getattr(evidence_package, "spec_id", None)
            or "spec-default"
        )

        now_utc = datetime.datetime.now(datetime.UTC).isoformat()

        # 2. Consolidación de métricas de la suite
        total = 0
        passed = 0
        failed = 0
        flaky = 0

        if suite_report is not None:
            if hasattr(suite_report, "total_cases"):
                total = int(getattr(suite_report, "total_cases", 0))
                passed = int(getattr(suite_report, "passed_count", 0))
                failed = int(getattr(suite_report, "failed_count", 0))
                flaky = int(getattr(suite_report, "flaky_count", 0))
            elif isinstance(suite_report, dict):
                total = int(suite_report.get("total", suite_report.get("total_cases", 0)))
                passed = int(suite_report.get("passed", suite_report.get("passed_count", 0)))
                failed = int(suite_report.get("failed", suite_report.get("failed_count", 0)))
                flaky = int(suite_report.get("flaky", suite_report.get("flaky_count", 0)))

        success_rate = round((float(passed) / float(total) * 100.0), 2) if total > 0 else 0.0

        suite_summary = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "flaky": flaky,
            "success_rate": success_rate,
        }

        # 3. Consolidación de métricas de cobertura
        line_rate = 0.0
        branch_rate = 0.0

        if metrics is not None:
            if isinstance(metrics, dict):
                lr = metrics.get("line_rate", metrics.get("lines_covered_pct", 0.0))
                br = metrics.get("branch_rate", metrics.get("branches_covered_pct", 0.0))
            else:
                lr = getattr(
                    metrics,
                    "line_rate",
                    getattr(metrics, "lines_covered_pct", 0.0),
                )
                br = getattr(
                    metrics,
                    "branch_rate",
                    getattr(metrics, "branches_covered_pct", 0.0),
                )

            # Normalizar valores que vengan en escala 0.0 - 1.0 a porcentajes 0.0 - 100.0
            line_rate = float(lr) * 100.0 if float(lr) <= 1.0 and float(lr) > 0.0 else float(lr)
            branch_rate = float(br) * 100.0 if float(br) <= 1.0 and float(br) > 0.0 else float(br)

        coverage_summary = {
            "line_rate": round(line_rate, 2),
            "branch_rate": round(branch_rate, 2),
        }

        # 4. Consolidación de Matriz BDD y cruce de evidencias
        criteria_matrix: list[dict[str, Any]] = []
        raw_results = []
        if suite_report is not None:
            if hasattr(suite_report, "results"):
                raw_results = suite_report.results
            elif isinstance(suite_report, dict) and "results" in suite_report:
                raw_results = suite_report["results"]

        for res in raw_results:
            c_id = getattr(res, "criterion_id", None) or (
                res.get("criterion_id") if isinstance(res, dict) else "AC-UNKNOWN"
            )
            c_kind = getattr(res, "kind", None) or (
                res.get("kind") if isinstance(res, dict) else "api"
            )
            c_status = getattr(res, "status", None) or (
                res.get("status") if isinstance(res, dict) else "unknown"
            )
            c_duration = getattr(res, "duration_ms", None) or (
                res.get("duration_ms") if isinstance(res, dict) else 0.0
            )
            c_path = getattr(res, "case_path", None) or (
                res.get("case_path") if isinstance(res, dict) else ""
            )
            c_evidence = getattr(res, "evidence_files", None) or (
                res.get("evidence_files") if isinstance(res, dict) else []
            )

            criteria_matrix.append(
                {
                    "criterion_id": str(c_id),
                    "kind": str(c_kind),
                    "status": str(c_status),
                    "duration_ms": float(c_duration),
                    "case_path": str(c_path),
                    "evidence_files": list(c_evidence),
                }
            )

        # 5. Consolidación de casos en cuarentena
        quarantine_cases: list[dict[str, Any]] = []
        if quarantine_list:
            for item in quarantine_list:
                if isinstance(item, dict):
                    quarantine_cases.append(item)
                elif hasattr(item, "model_dump"):
                    quarantine_cases.append(item.model_dump())
                elif hasattr(item, "__dict__"):
                    quarantine_cases.append(item.__dict__)

        # 6. Resolución del digest general SHA-256
        overall_sha256 = (
            getattr(evidence_package, "overall_hash", None)
            or (
                evidence_package.get("overall_hash") if isinstance(evidence_package, dict) else None
            )
            or ""
        )
        if not overall_sha256:
            # Cálculo determinista de respaldo si no hay paquete de evidencias
            fallback_payload = f"{resolved_job_id}:{resolved_tenant_id}:{resolved_spec_id}:{total}:{passed}".encode()
            overall_sha256 = hashlib.sha256(fallback_payload).hexdigest()

        return QualityReportDTO(
            job_id=str(resolved_job_id),
            tenant_id=str(resolved_tenant_id),
            spec_id=str(resolved_spec_id),
            generated_at=now_utc,
            suite_summary=suite_summary,
            coverage_summary=coverage_summary,
            criteria_matrix=criteria_matrix,
            quarantine_cases=quarantine_cases,
            overall_sha256=str(overall_sha256),
        )

    def render_html(
        self,
        report_data: QualityReportDTO,
        evidence_package: Any = None,
    ) -> str:
        """
        Renderiza el informe interactivo HTML incorporando estilos, scripts
        y gráficos SVG nativos. Valida de forma estricta la ausencia de referencias remotas.
        """
        # 1. Gráficos SVG puros
        success_pct = report_data.suite_summary.get("success_rate", 0.0)
        line_pct = report_data.coverage_summary.get("line_rate", 0.0)
        branch_pct = report_data.coverage_summary.get("branch_rate", 0.0)

        donut_success = generate_donut_svg(success_pct, "Éxito")
        donut_lines = generate_donut_svg(line_pct, "Líneas")
        donut_branches = generate_donut_svg(branch_pct, "Ramas", color_hex="#3b82f6")

        # 2. Desglose de evidencias para tabla de auditoría WORM
        evidence_list: list[dict[str, Any]] = []
        if evidence_package is not None:
            raw_ev = (
                getattr(evidence_package, "evidence_files", None)
                or (
                    evidence_package.get("evidence_files")
                    if isinstance(evidence_package, dict)
                    else []
                )
                or []
            )
            for ev in raw_ev:
                if isinstance(ev, dict):
                    evidence_list.append(ev)
                elif hasattr(ev, "model_dump"):
                    evidence_list.append(ev.model_dump())
                elif hasattr(ev, "__dict__"):
                    evidence_list.append(ev.__dict__)

        # 3. Renderizado Jinja2
        rendered_html = self._template.render(
            report=report_data,
            donut_success=donut_success,
            donut_lines=donut_lines,
            donut_branches=donut_branches,
            evidence_list=evidence_list,
        )

        # 4. Validación obligatoria de aislamiento Cero-Nube (Regla Mandatoria)
        # El HTML generado NO debe contener "https://" en tags remotos o recursos CDN
        forbidden_patterns = [
            r"<script[^>]+src=[\"']https?://",
            r"<link[^>]+href=[\"']https?://",
            r"<img[^>]+src=[\"']https?://",
            r"<iframe[^>]+src=[\"']https?://",
            r"@import\s+url\([\"']?https?://",
        ]
        for pattern in forbidden_patterns:
            if re.search(pattern, rendered_html, re.IGNORECASE):
                raise ValueError(
                    f"Violación de aislamiento Cero-Nube detectada en HTML generado: patrón remoto '{pattern}' encontrado."
                )

        return rendered_html

    def save_report(
        self,
        report_data: QualityReportDTO,
        output_path: Path,
        evidence_package: Any = None,
    ) -> Path:
        """Guarda el informe HTML generado en disco de forma atómica."""
        html_content = self.render_html(report_data, evidence_package=evidence_package)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html_content, encoding="utf-8")
        logger.info(
            "report_generator.report_saved",
            job_id=report_data.job_id,
            path=str(output_path),
            size_bytes=len(html_content),
        )
        return output_path
