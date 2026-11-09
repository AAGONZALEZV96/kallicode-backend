"""
Ticket: KC-QA-B4-01
Suite de pruebas unitarias y de integración para SuiteRunner (run_suite.py).
Valida:
1. test_ejecucion_suite_completa_exitosa: Ejecución de capas API y Web con éxito.
2. test_principio_fallo_cerrado_matriz_vacia_o_informe_truncado: Activación de QA-F01 ante casos vacíos o reporte corrupto.
3. test_deteccion_flaky_test_en_reintento_unico: Detección y clasificación rigurosa de flaky tests en reintento único.
4. test_aislamiento_variables_entorno_host: Verificación de lista blanca y bloqueo de secretos del host (QA-F02).
5. test_embudo_capas_falla_api_interrumpe_ejecucion: Corto circuito inmediato que impide levantar navegadores si API falla.
6. test_reintento_unico_ambos_fallan_marca_failed: Clasificación en 'failed' si ambos intentos fallan.
7. test_retrocompatibilidad_parse_test_results: Preservación de contratos legados.
8. test_integracion_flujos_con_flow_registry: Integración con PostgreSQL 16 local y FlowRegistry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.flow_registry import FlowRegistry, TestFlowDTO
from kallicode_testing.run_suite import (
    CommandExecutionResult,
    SuiteResult,
    SuiteRunner,
    build_isolated_env,
    parse_test_results,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_organization(db_session: AsyncSession, tenant_id: str) -> None:
    """Inserta la organización del tenant para satisfacer la FK en cm.test_flows."""
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO core.organizations (id, nombre)
            VALUES (:tenant_id, 'Org Test Runner')
            ON CONFLICT (id) DO NOTHING
        """),
        {"tenant_id": tenant_id},
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Prueba 1: test_ejecucion_suite_completa_exitosa
# ---------------------------------------------------------------------------
async def test_ejecucion_suite_completa_exitosa(tmp_path: Path) -> None:
    """
    Prueba 1: Simular ejecuciones exitosas para API y Playwright (Web).
    Validaciones:
    - is_success es True.
    - passed_count es igual al total de casos.
    - failed_count == 0 y flaky_count == 0.
    - Reporte JSON persistido correctamente en out_dir.
    """
    flows = [
        TestFlowDTO(
            id="AC-01:api",
            tenant_id="tenant_qa_runner",
            spec_id="SPEC-AUTH",
            criterion_id="AC-01",
            kind="api",
            path="tests/api/test_login.py",
            modules=["apps/api"],
        ),
        TestFlowDTO(
            id="AC-02:e2e",
            tenant_id="tenant_qa_runner",
            spec_id="SPEC-AUTH",
            criterion_id="AC-02",
            kind="e2e",
            path="e2e/portal/login.spec.ts",
            modules=["apps/portal"],
        ),
    ]

    executed_cmds: list[list[str]] = []

    async def mock_executor(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        executed_cmds.append(cmd)
        return CommandExecutionResult(
            exit_code=0,
            stdout="1 passed in 0.05s",
            stderr="",
            duration_ms=45.0,
            evidence_files=["reports/test_login.xml"],
        )

    runner = SuiteRunner(command_executor=mock_executor)
    report = await runner.run_suite(
        job_id="job-success-01",
        tenant_id="tenant_qa_runner",
        spec_id="SPEC-AUTH",
        impacted_modules=["apps/api", "apps/portal"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=flows,
    )

    assert report.is_success is True
    assert report.total_cases == 2
    assert report.passed_count == 2
    assert report.failed_count == 0
    assert report.flaky_count == 0
    assert len(report.results) == 2
    assert report.results[0].status == "passed"
    assert report.results[0].kind == "api"
    assert report.results[1].status == "passed"
    assert report.results[1].kind == "web"

    # Verificar invocación de comandos correspondientes
    assert len(executed_cmds) == 2
    assert "pytest" in executed_cmds[0]
    assert "playwright" in executed_cmds[1]

    # Verificar persistencia del archivo
    persisted = SuiteRunner.load_report(tmp_path / "suite_report_job-success-01.json")
    assert persisted.job_id == "job-success-01"
    assert persisted.is_success is True


# ---------------------------------------------------------------------------
# Prueba 2: test_principio_fallo_cerrado_matriz_vacia_o_informe_truncado
# ---------------------------------------------------------------------------
async def test_principio_fallo_cerrado_matriz_vacia_o_informe_truncado(tmp_path: Path) -> None:
    """
    Prueba 2: Simular regresión con cero casos, criterios faltantes y reporte corrupto/truncado.
    Validaciones:
    - is_success es False.
    - Se registra explícitamente el código de hallazgo QA-F01.
    - Carga de reporte corrupto/truncado lanza ValueError con QA-F01.
    """
    runner = SuiteRunner()

    # Caso A: Flujos vacíos (0 casos)
    report_empty = await runner.run_suite(
        job_id="job-empty-01",
        tenant_id="tenant_qa_runner",
        spec_id="SPEC-EMPTY",
        impacted_modules=["apps/api"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=[],
    )
    assert report_empty.is_success is False
    assert report_empty.total_cases == 0
    assert report_empty.error_details is not None
    assert "QA-F01" in report_empty.error_details

    # Caso B: Criterios faltantes respecto a la especificación
    flows_partial = [
        TestFlowDTO(
            id="AC-01:api",
            tenant_id="tenant_qa_runner",
            spec_id="SPEC-CRIT",
            criterion_id="AC-01",
            kind="api",
            path="tests/api/test_ac1.py",
            modules=["apps/api"],
        )
    ]
    report_missing = await runner.run_suite(
        job_id="job-missing-01",
        tenant_id="tenant_qa_runner",
        spec_id="SPEC-CRIT",
        impacted_modules=["apps/api"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        expected_criteria=["AC-01", "AC-02", "AC-03"],  # Faltan AC-02 y AC-03
        flows_override=flows_partial,
    )
    assert report_missing.is_success is False
    assert report_missing.error_details is not None
    assert "QA-F01" in report_missing.error_details

    # Caso C: Archivo de informe inexistente
    non_existent = tmp_path / "non_existent.json"
    with pytest.raises(ValueError, match="QA-F01.*Archivo de informe no encontrado"):
        SuiteRunner.load_report(non_existent)

    # Caso D: Archivo de informe vacío (0 bytes)
    empty_file = tmp_path / "empty_report.json"
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="QA-F01.*está vacío"):
        SuiteRunner.load_report(empty_file)

    # Caso E: Archivo de informe corrupto / truncado
    corrupt_file = tmp_path / "corrupt_report.json"
    corrupt_file.write_text('{"job_id": "job-1", "is_success": true, ', encoding="utf-8")
    with pytest.raises(ValueError, match="QA-F01.*corrupto o truncado"):
        SuiteRunner.load_report(corrupt_file)


# ---------------------------------------------------------------------------
# Prueba 3: test_deteccion_flaky_test_en_reintento_unico
# ---------------------------------------------------------------------------
async def test_deteccion_flaky_test_en_reintento_unico(tmp_path: Path) -> None:
    """
    Prueba 3: Simular fallo en la primera ejecución y éxito en el reintento único.
    Validaciones:
    - El caso se clasifica estrictamente como 'flaky'.
    - NUNCA se clasifica como 'passed'.
    - El contador flaky_count se incrementa a 1.
    - Se ejecutó exactamente dos veces el caso problemático.
    """
    flow = TestFlowDTO(
        id="AC-FLAKY:api",
        tenant_id="tenant_qa_runner",
        spec_id="SPEC-PAY",
        criterion_id="AC-FLAKY",
        kind="api",
        path="tests/api/test_payment.py",
        modules=["apps/api"],
    )

    attempt_counts: dict[str, int] = {"count": 0}

    async def flaky_executor(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        attempt_counts["count"] += 1
        if attempt_counts["count"] == 1:
            # Primer intento: Falla
            return CommandExecutionResult(
                exit_code=1,
                stdout="FAILED tests/api/test_payment.py - NetworkTimeout",
                stderr="Timeout connecting to gateway",
                duration_ms=60.0,
            )
        # Segundo intento (reintento único): Pasa
        return CommandExecutionResult(
            exit_code=0,
            stdout="PASSED tests/api/test_payment.py",
            stderr="",
            duration_ms=35.0,
        )

    runner = SuiteRunner(command_executor=flaky_executor)
    report = await runner.run_suite(
        job_id="job-flaky-01",
        tenant_id="tenant_qa_runner",
        spec_id="SPEC-PAY",
        impacted_modules=["apps/api"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=[flow],
    )

    assert attempt_counts["count"] == 2
    assert report.total_cases == 1
    assert report.flaky_count == 1
    assert report.passed_count == 0  # NO debe contabilizarse como passed
    assert report.failed_count == 0
    assert report.is_success is True
    assert report.results[0].status == "flaky"
    assert report.results[0].criterion_id == "AC-FLAKY"


# ---------------------------------------------------------------------------
# Prueba 4: test_aislamiento_variables_entorno_host
# ---------------------------------------------------------------------------
async def test_aislamiento_variables_entorno_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Prueba 4: Verificar que run_suite utiliza una lista blanca estricta de variables.
    Validaciones:
    - NO se propagan credenciales de base de datos (PG_DSN, KC_DATABASE_URL).
    - NO se propagan claves API (LLM_API_KEY, OPENAI_API_KEY).
    - NO se propagan secretos del sistema (JWT_SECRET, AWS_SECRET_ACCESS_KEY).
    - Cumplimiento del hallazgo QA-F02.
    """
    # Inyectar variables sensibles en el host
    monkeypatch.setenv("PG_DSN", "postgresql://admin:supersecret@db.internal:5432/prod")
    monkeypatch.setenv("KC_DATABASE_URL", "postgresql+asyncpg://admin:pass123@localhost/prod")
    monkeypatch.setenv("LLM_API_KEY", "sk-proj-super-secret-api-key-999")
    monkeypatch.setenv("JWT_SECRET", "ultra_confidential_jwt_key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret_aws_creds")
    monkeypatch.setenv("AZURE_STORAGE_KEY", "azure_key_data")
    monkeypatch.setenv("REDIS_PASSWORD", "redis_secret")

    # Inyectar variable permitida
    monkeypatch.setenv("CI", "true")

    captured_env: dict[str, str] = {}

    async def capturing_executor(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        captured_env.update(env)
        return CommandExecutionResult(
            exit_code=0,
            stdout="OK",
            stderr="",
            duration_ms=10.0,
        )

    flow = TestFlowDTO(
        id="AC-01:api",
        tenant_id="tenant_iso_01",
        spec_id="SPEC-ISO",
        criterion_id="AC-01",
        kind="api",
        path="tests/api/test_iso.py",
        modules=["apps/api"],
    )

    runner = SuiteRunner(command_executor=capturing_executor)
    await runner.run_suite(
        job_id="job-iso-01",
        tenant_id="tenant_iso_01",
        spec_id="SPEC-ISO",
        impacted_modules=["apps/api"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=[flow],
    )

    assert len(captured_env) > 0
    # Validar presencia de variables permitidas y contextuales
    assert captured_env["BASE_URL"] == "http://localhost:8000"
    assert captured_env["TENANT_ID"] == "tenant_iso_01"
    assert captured_env["JOB_ID"] == "job-iso-01"
    assert captured_env["SPEC_ID"] == "SPEC-ISO"
    assert captured_env.get("CI") == "true"

    # Validar ausencia estricta de secretos prohibidos (QA-F02)
    assert "PG_DSN" not in captured_env
    assert "KC_DATABASE_URL" not in captured_env
    assert "LLM_API_KEY" not in captured_env
    assert "JWT_SECRET" not in captured_env
    assert "AWS_SECRET_ACCESS_KEY" not in captured_env
    assert "AZURE_STORAGE_KEY" not in captured_env
    assert "REDIS_PASSWORD" not in captured_env

    # Verificar build_isolated_env directamente
    isolated = build_isolated_env("http://base", "tenant_x", "job_x")
    for key in isolated:
        upper = key.upper()
        for forbidden in (
            "KEY",
            "SECRET",
            "TOKEN",
            "PASSWORD",
            "DSN",
            "PG_",
            "DATABASE",
            "LLM_",
            "AZURE_",
        ):
            assert forbidden not in upper


# ---------------------------------------------------------------------------
# Prueba 5: test_embudo_capas_falla_api_interrumpe_ejecucion
# ---------------------------------------------------------------------------
async def test_embudo_capas_falla_api_interrumpe_ejecucion(tmp_path: Path) -> None:
    """
    Prueba 5: Regla obligatoria de corto circuito.
    Si la capa API falla, interrumpir inmediatamente la ejecución.
    Validaciones:
    - No se levanta Playwright (Web) ni Maestro (Mobile).
    - Los casos posteriores quedan marcados como 'skipped'.
    - is_success es False y failed_count == 1.
    """
    flows = [
        TestFlowDTO(
            id="AC-01:api",
            tenant_id="tenant_circuit",
            spec_id="SPEC-FUNNEL",
            criterion_id="AC-01",
            kind="api",
            path="tests/api/test_crit_api.py",
            modules=["apps/api"],
        ),
        TestFlowDTO(
            id="AC-02:e2e",
            tenant_id="tenant_circuit",
            spec_id="SPEC-FUNNEL",
            criterion_id="AC-02",
            kind="e2e",
            path="e2e/portal/checkout.spec.ts",
            modules=["apps/portal"],
        ),
        TestFlowDTO(
            id="AC-03:mobile",
            tenant_id="tenant_circuit",
            spec_id="SPEC-FUNNEL",
            criterion_id="AC-03",
            kind="unit",
            path="mobile/flows/login.yaml",
            modules=["apps/mobile"],
        ),
    ]

    commands_called: list[list[str]] = []

    async def api_failing_executor(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        commands_called.append(cmd)
        # La API falla en intento 1 y reintento 2
        return CommandExecutionResult(
            exit_code=1,
            stdout="FAILED tests/api/test_crit_api.py",
            stderr="AssertionError: 500 != 200",
            duration_ms=50.0,
        )

    runner = SuiteRunner(command_executor=api_failing_executor)
    report = await runner.run_suite(
        job_id="job-funnel-01",
        tenant_id="tenant_circuit",
        spec_id="SPEC-FUNNEL",
        impacted_modules=["apps/api", "apps/portal", "apps/mobile"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=flows,
    )

    # Solo se debe haber ejecutado la capa API (intento 1 + reintento 2 = 2 invocaciones)
    assert len(commands_called) == 2
    for cmd in commands_called:
        assert "pytest" in cmd
        assert "playwright" not in cmd
        assert "maestro" not in cmd

    assert report.is_success is False
    assert report.failed_count == 1
    assert report.total_cases == 3

    # Los casos web y mobile deben quedar en skipped
    results_by_id = {r.criterion_id: r for r in report.results}
    assert results_by_id["AC-01"].status == "failed"
    assert results_by_id["AC-02"].status == "skipped"
    assert results_by_id["AC-03"].status == "skipped"


# ---------------------------------------------------------------------------
# Prueba 6: test_reintento_unico_ambos_fallan_marca_failed
# ---------------------------------------------------------------------------
async def test_reintento_unico_ambos_fallan_marca_failed(tmp_path: Path) -> None:
    """
    Prueba 6: Si el intento 1 falla y el reintento único también falla,
    el caso se clasifica como 'failed' y failed_count incrementa.
    """
    flow = TestFlowDTO(
        id="AC-FAIL:api",
        tenant_id="tenant_fail",
        spec_id="SPEC-FAIL",
        criterion_id="AC-FAIL",
        kind="api",
        path="tests/api/test_fail.py",
        modules=["apps/api"],
    )

    async def double_fail_executor(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        return CommandExecutionResult(
            exit_code=1,
            stdout="FAILED",
            stderr="Hard failure",
            duration_ms=20.0,
        )

    runner = SuiteRunner(command_executor=double_fail_executor)
    report = await runner.run_suite(
        job_id="job-fail-01",
        tenant_id="tenant_fail",
        spec_id="SPEC-FAIL",
        impacted_modules=["apps/api"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=[flow],
    )

    assert report.is_success is False
    assert report.failed_count == 1
    assert report.flaky_count == 0
    assert report.results[0].status == "failed"
    assert report.results[0].error_summary == "Hard failure"


# ---------------------------------------------------------------------------
# Prueba 7: test_retrocompatibilidad_parse_test_results
# ---------------------------------------------------------------------------
async def test_retrocompatibilidad_parse_test_results() -> None:
    """
    Prueba 7: Asegura que parse_test_results y SuiteResult preserven
    el comportamiento esperado por test_testing_module.py.
    """
    res_ok: SuiteResult = parse_test_results("1 passed in 0.01s")
    assert res_ok["passed"] == 1
    assert res_ok["failed"] == 0

    res_fail: SuiteResult = parse_test_results("FAILED tests/test_login.py")
    assert res_fail["failed"] == 1
    assert res_fail["passed"] == 0


# ---------------------------------------------------------------------------
# Prueba 8: test_integracion_flujos_con_flow_registry
# ---------------------------------------------------------------------------
async def test_integracion_flujos_con_flow_registry(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """
    Prueba 8: Integración real con FlowRegistry sobre PostgreSQL 16 local.
    Verifica que SuiteRunner consulte los flujos registrados en cm.test_flows
    filtrando por radio de impacto.
    """
    tenant_id = "tenant_runner_db_01"
    await _seed_organization(db_session, tenant_id)

    registry = FlowRegistry(db_session=db_session)
    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-INTEG",
        criterion_id="AC-01",
        kind="api",
        file_path="tests/api/test_integ.py",
        modules=["apps/api"],
    )

    async def mock_pass(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        return CommandExecutionResult(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            duration_ms=15.0,
        )

    runner = SuiteRunner(flow_registry=registry, command_executor=mock_pass)
    report = await runner.run_suite(
        job_id="job-db-01",
        tenant_id=tenant_id,
        spec_id="SPEC-INTEG",
        impacted_modules=["apps/api"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
    )

    assert report.is_success is True
    assert report.total_cases == 1
    assert report.passed_count == 1
    assert report.results[0].criterion_id == "AC-01"


# ---------------------------------------------------------------------------
# Prueba 9: test_default_command_executor_real
# ---------------------------------------------------------------------------
async def test_default_command_executor_real(tmp_path: Path) -> None:
    """
    Prueba 9: Valida que default_command_executor ejecute subprocesos asíncronos reales.
    """
    from kallicode_testing.run_suite import default_command_executor

    res = await default_command_executor(
        ["python3", "-c", "print('runner_async_ok')"],
        {"CI": "true"},
        tmp_path,
    )
    assert res.exit_code == 0
    assert "runner_async_ok" in res.stdout
    assert res.duration_ms > 0.0


# ---------------------------------------------------------------------------
# Prueba 10: test_build_isolated_env_con_extra_env
# ---------------------------------------------------------------------------
async def test_build_isolated_env_con_extra_env() -> None:
    """
    Prueba 10: Valida que extra_env aplique filtro de seguridad estricto.
    """
    env = build_isolated_env(
        base_url="http://localhost:8000",
        tenant_id="tenant_extra",
        job_id="job_extra",
        extra_env={
            "CI": "true",
            "FORBIDDEN_SECRET_KEY": "leak",
            "PASSWORD_DB": "leak",
        },
    )
    assert env["BASE_URL"] == "http://localhost:8000"
    assert env["CI"] == "true"
    assert "FORBIDDEN_SECRET_KEY" not in env
    assert "PASSWORD_DB" not in env


# ---------------------------------------------------------------------------
# Prueba 11: test_embudo_capas_falla_web_omite_mobile
# ---------------------------------------------------------------------------
async def test_embudo_capas_falla_web_omite_mobile(tmp_path: Path) -> None:
    """
    Prueba 11: Valida que si la capa Web falla tras aprobar API,
    la capa Mobile es omitida (short-circuit).
    """
    flows = [
        TestFlowDTO(
            id="AC-API:api",
            tenant_id="tenant_web_fail",
            spec_id="SPEC-WFAIL",
            criterion_id="AC-API",
            kind="api",
            path="tests/api/test_ok.py",
            modules=["apps/api"],
        ),
        TestFlowDTO(
            id="AC-WEB:e2e",
            tenant_id="tenant_web_fail",
            spec_id="SPEC-WFAIL",
            criterion_id="AC-WEB",
            kind="e2e",
            path="e2e/portal/test_fail.spec.ts",
            modules=["apps/portal"],
        ),
        TestFlowDTO(
            id="AC-MOB:mobile",
            tenant_id="tenant_web_fail",
            spec_id="SPEC-WFAIL",
            criterion_id="AC-MOB",
            kind="e2e",
            path="mobile/flows/test_mob.yaml",
            modules=["apps/mobile"],
        ),
    ]

    async def web_fail_executor(
        cmd: list[str],
        env: dict[str, str],
        cwd: Path,
    ) -> CommandExecutionResult:
        if "playwright" in cmd:
            return CommandExecutionResult(
                exit_code=1,
                stdout="Playwright FAILED",
                stderr="SelectorNotFound",
                duration_ms=10.0,
            )
        return CommandExecutionResult(
            exit_code=0,
            stdout="OK",
            stderr="",
            duration_ms=10.0,
        )

    runner = SuiteRunner(command_executor=web_fail_executor)
    report = await runner.run_suite(
        job_id="job-wfail-01",
        tenant_id="tenant_web_fail",
        spec_id="SPEC-WFAIL",
        impacted_modules=["apps/api", "apps/portal", "apps/mobile"],
        base_url="http://localhost:8000",
        out_dir=tmp_path,
        flows_override=flows,
    )

    assert report.is_success is False
    assert report.failed_count == 1
    results_by_id = {r.criterion_id: r for r in report.results}
    assert results_by_id["AC-API"].status == "passed"
    assert results_by_id["AC-WEB"].status == "failed"
    assert results_by_id["AC-MOB"].status == "skipped"
