"""
Ticket: KC-QA-B4-01
Runner Adapter Unificado para Pytest y Playwright (run_suite.py).
Orquestador de ejecución unificada, aislada y determinista para las tres capas de pruebas:
- API (Pytest + httpx)
- Web (Playwright TS)
- Mobile (Maestro YAML)

Implementa:
- Regresión dirigida por impacto con FlowRegistry.
- Embudo de capas secuencial (API -> Web -> Mobile) con compuerta de corto circuito.
- Política de reintento único para detección y aislamiento de flaky tests.
- Principio de fallo cerrado (QA-F01) ante ejecuciones vacías o reportes truncados.
- Aislamiento riguroso de variables de entorno host (QA-F02).
- Retrocompatibilidad con SuiteResult y parse_test_results.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, TypedDict

import structlog.contextvars
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.flow_registry import FlowRegistry, TestFlowDTO
from kallicode_testing.logger import get_logger
from kallicode_testing.scratch_cleaner import ScratchCleaner, ScratchCleanSummaryDTO
from kallicode_testing.traceability import TraceabilityMapper

logger = get_logger()

# Tipos permitidos para casos y estados
TestKind = Literal["unit", "api", "web", "mobile"]
TestStatus = Literal["passed", "failed", "flaky", "skipped", "error"]


# ---------------------------------------------------------------------------
# Contratos Legados (Retrocompatibilidad con test_testing_module.py)
# ---------------------------------------------------------------------------
class SuiteResult(TypedDict):
    passed: int
    failed: int
    duration_ms: float


def parse_test_results(raw_output: str) -> SuiteResult:
    """Parsea la salida simulada de pytest a un diccionario estructurado (legado)."""
    if "FAILED" in raw_output:
        return {"passed": 0, "failed": 1, "duration_ms": 15.0}
    return {"passed": 1, "failed": 0, "duration_ms": 10.5}


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-01
# ---------------------------------------------------------------------------
class TestCaseResultDTO(BaseModel):
    """Representa el resultado individual e inmutable de un caso de prueba."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    criterion_id: str
    kind: TestKind
    status: TestStatus
    case_path: str
    duration_ms: float
    error_summary: str | None = None
    evidence_files: list[str] = Field(default_factory=list)


class SuiteExecutionReport(BaseModel):
    """Reporte consolidado e inmutable de la ejecución completa de la suite."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    job_id: str
    tenant_id: str
    total_cases: int
    passed_count: int
    failed_count: int
    flaky_count: int
    is_success: bool
    results: list[TestCaseResultDTO]
    error_details: str | None = None


# ---------------------------------------------------------------------------
# Modelo para Salida de Comandos y Protocolo de Invocación
# ---------------------------------------------------------------------------
class CommandExecutionResult(BaseModel):
    """Captura stdout, stderr, código de salida y evidencias de un subproceso."""

    model_config = ConfigDict(frozen=True)

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    evidence_files: list[str] = Field(default_factory=list)


CommandExecutor = Callable[[list[str], dict[str, str], Path], Awaitable[CommandExecutionResult]]


# ---------------------------------------------------------------------------
# Aislamiento Estricto de Entorno (QA-F02)
# ---------------------------------------------------------------------------
ALLOWED_ENV_VARS: frozenset[str] = frozenset(
    {
        "BASE_URL",
        "TENANT_ID",
        "JOB_ID",
        "SPEC_ID",
        "CI",
        "PATH",
        "PYTHONPATH",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "TMP",
        "HOME",
        "LANG",
        "LC_ALL",
    }
)

FORBIDDEN_ENV_SUBSTRINGS: tuple[str, ...] = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASS",
    "DSN",
    "PG_",
    "DATABASE",
    "DB_",
    "REDIS",
    "VAULT",
    "CREDENTIAL",
    "AZURE_",
    "AWS_",
    "LLM_",
    "OPENAI_",
)


def build_isolated_env(
    base_url: str,
    tenant_id: str,
    job_id: str,
    spec_id: str = "",
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """
    Construye un diccionario explícito de entorno sanitizado bajo lista blanca estricta.
    Expulsa activamente variables sensibles del host (QA-F02).
    """
    clean_env: dict[str, str] = {
        "BASE_URL": base_url,
        "TENANT_ID": tenant_id,
        "JOB_ID": job_id,
    }
    if spec_id:
        clean_env["SPEC_ID"] = spec_id

    # Heredar únicamente variables de runtime del sistema permitidas y no sensibles
    for key, value in os.environ.items():
        upper_key = key.upper()
        if upper_key in ALLOWED_ENV_VARS:
            if not any(forbidden in upper_key for forbidden in FORBIDDEN_ENV_SUBSTRINGS):
                clean_env[key] = value

    if extra_env:
        for key, value in extra_env.items():
            upper_key = key.upper()
            if not any(forbidden in upper_key for forbidden in FORBIDDEN_ENV_SUBSTRINGS):
                clean_env[key] = value

    return clean_env


# ---------------------------------------------------------------------------
# Ejecutor por Defecto de Procesos Asíncronos
# ---------------------------------------------------------------------------
async def default_command_executor(
    cmd: list[str],
    env: dict[str, str],
    cwd: Path,
) -> CommandExecutionResult:
    """Ejecuta comandos de prueba mediante subprocesos asíncronos reales."""
    start_time = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=str(cwd),
    )
    stdout_b, stderr_b = await proc.communicate()
    duration_ms = (time.perf_counter() - start_time) * 1000.0

    return CommandExecutionResult(
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
        duration_ms=duration_ms,
        evidence_files=[],
    )


def normalize_flow_kind(flow_kind: str, path: str) -> TestKind:
    """Mapea tipos de flujo a las capas de prueba unificadas."""
    normalized = flow_kind.lower().strip()
    if normalized == "mobile" or ".yaml" in path or "/mobile/" in path:
        return "mobile"
    if normalized in ("unit", "api"):
        return "api" if normalized == "api" else "unit"
    return "web"


# ---------------------------------------------------------------------------
# SuiteRunner Principal
# ---------------------------------------------------------------------------
class SuiteRunner:
    """
    Orquestador unificado de suites de prueba con regresión dirigida,
    embudo por capas, reintento único para flaky tests y principio de fallo cerrado.
    """

    def __init__(
        self,
        flow_registry: FlowRegistry | None = None,
        traceability_mapper: TraceabilityMapper | None = None,
        command_executor: CommandExecutor | None = None,
        scratch_cleaner: ScratchCleaner | None = None,
    ) -> None:
        self.flow_registry = flow_registry
        self.traceability_mapper = traceability_mapper
        self.command_executor = command_executor or default_command_executor
        self.scratch_cleaner = scratch_cleaner

    def cleanup_scratch(
        self,
        job_id: str,
        scratch_dir: Path,
        preserve_evidence_sealed: bool = True,
    ) -> ScratchCleanSummaryDTO:
        """Invoca la eliminación segura del directorio scratch temporal utilizando ScratchCleaner."""
        cleaner = self.scratch_cleaner or ScratchCleaner()
        return cleaner.clean_job_scratch(
            job_id=job_id,
            scratch_dir=scratch_dir,
            preserve_evidence_sealed=preserve_evidence_sealed,
        )

    async def _execute_flow_with_retry(
        self,
        flow: TestFlowDTO,
        kind: TestKind,
        env: dict[str, str],
        out_dir: Path,
    ) -> TestCaseResultDTO:
        """
        Ejecuta un flujo de prueba aplicando exactamente un reintento en caso de fallo.
        Si falla en el intento 1 y aprueba en el intento 2, se clasifica como 'flaky'.
        """
        # Determinar comando según la capa
        if kind in ("unit", "api"):
            cmd = ["uv", "run", "pytest", flow.path, "-v"]
        elif kind == "web":
            cmd = ["npx", "playwright", "test", flow.path]
        else:
            cmd = ["maestro", "test", flow.path]

        # Intento 1
        res1 = await self.command_executor(cmd, env, out_dir)
        if res1.exit_code == 0:
            return TestCaseResultDTO(
                criterion_id=flow.criterion_id,
                kind=kind,
                status="passed",
                case_path=flow.path,
                duration_ms=res1.duration_ms,
                evidence_files=res1.evidence_files,
            )

        # Intento 2 (Reintento único para detección de flaky)
        logger.warning(
            "run_suite.flaky_retry_triggered",
            flow_id=flow.id,
            criterion_id=flow.criterion_id,
            kind=kind,
            initial_exit_code=res1.exit_code,
        )
        res2 = await self.command_executor(cmd, env, out_dir)
        total_duration = res1.duration_ms + res2.duration_ms
        combined_evidence = list(dict.fromkeys(res1.evidence_files + res2.evidence_files))

        if res2.exit_code == 0:
            logger.info(
                "run_suite.flaky_test_detected",
                flow_id=flow.id,
                criterion_id=flow.criterion_id,
            )
            return TestCaseResultDTO(
                criterion_id=flow.criterion_id,
                kind=kind,
                status="flaky",
                case_path=flow.path,
                duration_ms=total_duration,
                evidence_files=combined_evidence,
            )

        # Fallo permanente tras reintento
        error_summary = (res2.stderr or res2.stdout or "Test failed on retry").strip()
        return TestCaseResultDTO(
            criterion_id=flow.criterion_id,
            kind=kind,
            status="failed",
            case_path=flow.path,
            duration_ms=total_duration,
            error_summary=error_summary,
            evidence_files=combined_evidence,
        )

    async def run_suite(
        self,
        job_id: str,
        tenant_id: str,
        spec_id: str,
        impacted_modules: list[str],
        base_url: str,
        out_dir: Path,
        expected_criteria: list[str] | None = None,
        flows_override: list[TestFlowDTO] | None = None,
        clean_scratch: bool = False,
        scratch_dir: Path | None = None,
    ) -> SuiteExecutionReport:
        """
        Ejecuta la suite orquestada bajo el embudo por capas:
        API -> Web -> Mobile.
        Aplica principio de fallo cerrado (QA-F01) y aislamiento de entorno (QA-F02).
        """
        structlog.contextvars.bind_contextvars(tenant_id=tenant_id, job_id=job_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Paso 1: Regresión dirigida o flujos provistos
        if flows_override is not None:
            flows = flows_override
        elif self.flow_registry is not None:
            flows = await self.flow_registry.get_flows_for_modules(tenant_id, impacted_modules)
            if spec_id.strip():
                flows = [f for f in flows if f.spec_id == spec_id]
        else:
            flows = []

        # 2. Paso 4: Principio de Fallo Cerrado (QA-F01)
        # Rechaza ejecuciones con cero flujos o con matriz incompleta frente a criterios esperados
        criteria_present = {f.criterion_id for f in flows}
        missing_criteria: list[str] = []
        if expected_criteria:
            missing_criteria = sorted(set(expected_criteria) - criteria_present)

        if len(flows) == 0 or len(missing_criteria) > 0:
            error_msg = (
                f"QA-F01: Principio de fallo cerrado activado. Matriz de pruebas incompleta "
                f"(casos registrados: {len(flows)}, criterios faltantes: {missing_criteria or 'ninguno'})."
            )
            logger.error("run_suite.closed_failure_triggered", reason=error_msg)
            report = SuiteExecutionReport(
                job_id=job_id,
                tenant_id=tenant_id,
                total_cases=0,
                passed_count=0,
                failed_count=0,
                flaky_count=0,
                is_success=False,
                results=[],
                error_details=error_msg,
            )
            self._save_report(report, out_dir, job_id)
            return report

        # 3. Paso 5: Aislamiento de Entorno (QA-F02)
        env = build_isolated_env(
            base_url=base_url,
            tenant_id=tenant_id,
            job_id=job_id,
            spec_id=spec_id,
        )

        # 4. Paso 2: Embudo por Capas (API -> Web -> Mobile)
        api_flows = [f for f in flows if normalize_flow_kind(f.kind, f.path) in ("unit", "api")]
        web_flows = [f for f in flows if normalize_flow_kind(f.kind, f.path) == "web"]
        mobile_flows = [f for f in flows if normalize_flow_kind(f.kind, f.path) == "mobile"]

        executed_results: list[TestCaseResultDTO] = []
        api_failed = False

        # Ejecutar Capa 1: API
        for flow in api_flows:
            target_kind = normalize_flow_kind(flow.kind, flow.path)
            res = await self._execute_flow_with_retry(flow, target_kind, env, out_dir)
            executed_results.append(res)
            if res.status in ("failed", "error"):
                api_failed = True
                logger.error(
                    "run_suite.api_layer_gate_failed",
                    criterion_id=flow.criterion_id,
                    path=flow.path,
                )
                break  # Corto circuito inmediato ante fallo en API

        # Regla obligatoria: Si la capa API falla, interrumpir y NO levantar Web ni Mobile
        if api_failed:
            logger.warning("run_suite.circuit_breaker_active_skipping_web_mobile")
            # Los casos no ejecutados de API, Web y Mobile se marcan como skipped
            executed_flow_ids = {r.criterion_id for r in executed_results}
            pending_flows = [f for f in flows if f.criterion_id not in executed_flow_ids]
            for pending in pending_flows:
                executed_results.append(
                    TestCaseResultDTO(
                        criterion_id=pending.criterion_id,
                        kind=normalize_flow_kind(pending.kind, pending.path),
                        status="skipped",
                        case_path=pending.path,
                        duration_ms=0.0,
                        error_summary="Omitido por fallo previo en la capa API (short-circuit / circuit breaker)",
                    )
                )
        else:
            # Ejecutar Capa 2: Web (Playwright)
            web_failed = False
            for flow in web_flows:
                res = await self._execute_flow_with_retry(flow, "web", env, out_dir)
                executed_results.append(res)
                if res.status in ("failed", "error"):
                    web_failed = True
                    break

            if web_failed:
                # Omitir mobile si web falló
                executed_flow_ids = {r.criterion_id for r in executed_results}
                pending_flows = [f for f in flows if f.criterion_id not in executed_flow_ids]
                for pending in pending_flows:
                    executed_results.append(
                        TestCaseResultDTO(
                            criterion_id=pending.criterion_id,
                            kind=normalize_flow_kind(pending.kind, pending.path),
                            status="skipped",
                            case_path=pending.path,
                            duration_ms=0.0,
                            error_summary="Omitido por fallo previo en la capa Web",
                        )
                    )
            else:
                # Ejecutar Capa 3: Mobile (Maestro)
                for flow in mobile_flows:
                    res = await self._execute_flow_with_retry(flow, "mobile", env, out_dir)
                    executed_results.append(res)

        # Consolidación final de métricas
        passed_cnt = sum(1 for r in executed_results if r.status == "passed")
        failed_cnt = sum(1 for r in executed_results if r.status in ("failed", "error"))
        flaky_cnt = sum(1 for r in executed_results if r.status == "flaky")
        total_cnt = len(executed_results)
        is_success = (total_cnt > 0) and (failed_cnt == 0) and not api_failed

        report = SuiteExecutionReport(
            job_id=job_id,
            tenant_id=tenant_id,
            total_cases=total_cnt,
            passed_count=passed_cnt,
            failed_count=failed_cnt,
            flaky_count=flaky_cnt,
            is_success=is_success,
            results=executed_results,
            error_details="Fallo en compuerta de pruebas" if not is_success else None,
        )

        self._save_report(report, out_dir, job_id)

        if clean_scratch and scratch_dir is not None:
            self.cleanup_scratch(
                job_id=job_id,
                scratch_dir=scratch_dir,
                preserve_evidence_sealed=True,
            )

        return report

    def _save_report(self, report: SuiteExecutionReport, out_dir: Path, job_id: str) -> Path:
        """Persiste el informe autocontenido en disco."""
        report_file = out_dir / f"suite_report_{job_id}.json"
        report_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report_file

    @staticmethod
    def load_report(report_path: Path) -> SuiteExecutionReport:
        """
        Carga y valida un informe de ejecución persistido.
        Aplica principio de fallo cerrado (QA-F01) si el archivo no existe o está truncado.
        """
        if not report_path.exists():
            raise ValueError(f"QA-F01: Archivo de informe no encontrado en '{report_path}'.")
        raw = report_path.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError("QA-F01: El informe de pruebas está vacío (0 bytes).")
        try:
            data = json.loads(raw)
            return SuiteExecutionReport.model_validate(data)
        except Exception as exc:
            raise ValueError(f"QA-F01: Informe de pruebas corrupto o truncado: {exc}") from exc
