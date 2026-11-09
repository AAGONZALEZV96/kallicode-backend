"""
Ticket: KC-QA-B4-07
Suite de pruebas unitarias y de resiliencia para ProcessCleaner (process_cleaner.py).
Valida:
1. test_ejecucion_exitosa_dentro_de_timeout: Proceso normal finaliza con exit_code=0 y timed_out=False.
2. test_interrupcion_por_timeout_sigterm_sigkill: Interrupción determinista ante timeout con SIGTERM.
3. test_escalacion_a_sigkill_para_procesos_que_ignoran_sigterm: Escalación a SIGKILL si ignora SIGTERM.
4. test_sanitizacion_estricta_variables_entorno_host: Purga total de secretos del host y de extra_env (QA-F02).
5. test_limpieza_procesos_huerfanos_residuos: Identificación y terminación segura de procesos huérfanos.
6. test_manejo_errores_comando_inexistente_y_dto_inmutable: Resiliencia ante comando inválido e inmutabilidad frozen.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest
from kallicode_testing.process_cleaner import ProcessCleaner, ProcessExecutionResultDTO
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Prueba 1: test_ejecucion_exitosa_dentro_de_timeout
# ---------------------------------------------------------------------------
def test_ejecucion_exitosa_dentro_de_timeout() -> None:
    """
    Prueba 1: Ejecutar comando estándar python3 dentro del límite de tiempo.
    Validaciones:
    - timed_out is False.
    - exit_code == 0.
    - stdout capturado con el mensaje esperado.
    - duration_ms > 0.
    """
    cleaner = ProcessCleaner()
    cmd = [sys.executable, "-c", "print('clean_exec_ok')"]

    res = cleaner.run_with_timeout(cmd, timeout_sec=5.0)

    assert res.timed_out is False
    assert res.exit_code == 0
    assert "clean_exec_ok" in res.stdout
    assert res.stderr == ""
    assert res.terminated_signal is None
    assert res.duration_ms > 0.0


# ---------------------------------------------------------------------------
# Prueba 2: test_interrupcion_por_timeout_sigterm_sigkill
# ---------------------------------------------------------------------------
def test_interrupcion_por_timeout_sigterm_sigkill() -> None:
    """
    Prueba 2: Comando que excede timeout_sec debe ser interrumpido con SIGTERM.
    Validaciones:
    - timed_out is True.
    - exit_code != 0.
    - terminated_signal == 'SIGTERM'.
    """
    cleaner = ProcessCleaner(grace_period_sec=0.2)
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]

    res = cleaner.run_with_timeout(cmd, timeout_sec=0.4)

    assert res.timed_out is True
    assert res.exit_code != 0
    assert res.terminated_signal == "SIGTERM"
    assert res.duration_ms >= 350.0


# ---------------------------------------------------------------------------
# Prueba 3: test_escalacion_a_sigkill_para_procesos_que_ignoran_sigterm
# ---------------------------------------------------------------------------
def test_escalacion_a_sigkill_para_procesos_que_ignoran_sigterm() -> None:
    """
    Prueba 3: Proceso que ignora deliberadamente SIGTERM debe ser terminado
    forzosamente con SIGKILL tras agotar el período de gracia.
    Validaciones:
    - timed_out is True.
    - exit_code != 0.
    - terminated_signal == 'SIGKILL'.
    """
    cleaner = ProcessCleaner(grace_period_sec=0.3)
    # Proceso que ignora SIGTERM y entra en bucle
    cmd = [
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)",
    ]

    res = cleaner.run_with_timeout(cmd, timeout_sec=0.4)

    assert res.timed_out is True
    assert res.exit_code != 0
    assert res.terminated_signal == "SIGKILL"


# ---------------------------------------------------------------------------
# Prueba 4: test_sanitizacion_estricta_variables_entorno_host
# ---------------------------------------------------------------------------
def test_sanitizacion_estricta_variables_entorno_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Prueba 4: Sanitización exhaustiva contra fugas de secretos del host (QA-F02).
    Inyecta variables sensibles y valida que build_sanitized_env elimina el 100%.
    """
    # 1. Inyectar secretos en el host
    monkeypatch.setenv("PG_DSN", "postgresql://admin:secret@host:5432/db")
    monkeypatch.setenv("LLM_API_KEY", "sk-live-secret-key-12345")
    monkeypatch.setenv("SECRET_TOKEN", "jwt.token.val")
    monkeypatch.setenv("DATABASE_PASSWORD", "super_secret")
    monkeypatch.setenv("REDIS_URL", "redis://:secret@localhost:6379")
    monkeypatch.setenv("AZURE_STORAGE_KEY", "bWFsaWNpb3VzX2tleQ==")
    monkeypatch.setenv("BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("TENANT_ID", "tenant-test-123")

    cleaner = ProcessCleaner()
    extra = {
        "OPENAI_API_KEY": "leak_openai",
        "AWS_SECRET_KEY": "leak_aws",
        "JOB_ID": "job-cleaner-01",
    }

    sanitized = cleaner.build_sanitized_env(extra_env=extra)

    # Variables legítimas deben preservarse
    assert sanitized.get("BASE_URL") == "http://localhost:8000"
    assert sanitized.get("TENANT_ID") == "tenant-test-123"
    assert sanitized.get("JOB_ID") == "job-cleaner-01"

    # Variables sensibles DEBEN haber sido purgadas al 100%
    forbidden_keys = [
        "PG_DSN",
        "LLM_API_KEY",
        "SECRET_TOKEN",
        "DATABASE_PASSWORD",
        "REDIS_URL",
        "AZURE_STORAGE_KEY",
        "OPENAI_API_KEY",
        "AWS_SECRET_KEY",
    ]
    for key in forbidden_keys:
        assert key not in sanitized, f"Fuga de seguridad detectada: {key} presente en sanitized_env"


# ---------------------------------------------------------------------------
# Prueba 5: test_limpieza_procesos_huerfanos_residuos
# ---------------------------------------------------------------------------
def test_limpieza_procesos_huerfanos_residuos() -> None:
    """
    Prueba 5: Invocar cleanup_orphan_processes.
    Validaciones:
    - Retorna entero >= 0 sin excepciones.
    - No afecta al proceso actual ni a sus ancestros.
    - Mata subprocesos huérfanos que coincidan con el patrón objetivo.
    """
    cleaner = ProcessCleaner()

    # 1. Ejecución sobre sistema en reposo (sin procesos huérfanos)
    res_base = cleaner.cleanup_orphan_processes()
    assert isinstance(res_base, int)
    assert res_base >= 0

    # 2. Simular un subproceso huérfano con nombre o comando personalizado
    orphan_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30) # dummy_orphan_test_target"],
        start_new_session=True,
    )
    try:
        # Dar un breve instante para que el sistema operativo registre el proceso
        time.sleep(0.1)

        # Invocar cleanup buscando el patrón distintivo del proceso huérfano
        killed = cleaner.cleanup_orphan_processes(patterns=["dummy_orphan_test_target"])
        assert killed >= 1
    finally:
        # Asegurar limpieza defensiva
        try:
            orphan_proc.kill()
            orphan_proc.wait(timeout=1.0)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Prueba 6: test_manejo_errores_comando_inexistente_y_dto_inmutable
# ---------------------------------------------------------------------------
def test_manejo_errores_comando_inexistente_y_dto_inmutable() -> None:
    """
    Prueba 6: Resiliencia ante comando inválido / no encontrado y validación
    de inmutabilidad Pydantic (frozen=True).
    """
    cleaner = ProcessCleaner()

    # 1. Comando inexistente no debe lanzar excepciones al llamador
    res = cleaner.run_with_timeout(["/non_existent_binary_xyz_12345"], timeout_sec=2.0)
    assert res.exit_code == 127
    assert res.timed_out is False
    assert "Command not found" in res.stderr

    # 2. Inmutabilidad de ProcessExecutionResultDTO (frozen=True)
    dto = ProcessExecutionResultDTO(
        command=["echo", "test"],
        exit_code=0,
        stdout="test\n",
        stderr="",
        duration_ms=10.0,
        timed_out=False,
        terminated_signal=None,
    )
    with pytest.raises(ValidationError):
        dto.exit_code = 1


# ---------------------------------------------------------------------------
# Prueba 7: test_allowed_env_personalizado_y_excepcion_generica
# ---------------------------------------------------------------------------
def test_allowed_env_personalizado_y_excepcion_generica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Prueba 7: Cobertura de variables de entorno personalizadas y manejo
    de excepciones inesperadas del sistema operativo en subprocess.Popen.
    """
    # 1. Allowed env personalizado en __init__
    monkeypatch.setenv("CUSTOM_FEATURE_FLAG", "enabled")
    cleaner = ProcessCleaner(allowed_env_vars=["CUSTOM_FEATURE_FLAG"])
    env = cleaner.build_sanitized_env()
    assert env.get("CUSTOM_FEATURE_FLAG") == "enabled"

    # 2. Excepción inesperada del SO en Popen
    def mock_failing_popen(*args: object, **kwargs: object) -> None:
        raise OSError("Resource temporarily unavailable")

    monkeypatch.setattr(subprocess, "Popen", mock_failing_popen)

    res = cleaner.run_with_timeout(["echo", "hello"], timeout_sec=2.0)
    assert res.exit_code == 1
    assert "Resource temporarily unavailable" in res.stderr
    assert res.timed_out is False
