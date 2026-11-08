"""
Ticket: KC-QA-B4-07
Sanitizador de Entorno y Manejo de Timeouts (process_cleaner.py).
Garantiza la estabilidad y seguridad del sandbox de ejecución en dos pilares:
1. Sanitización estricta de variables de entorno mediante lista blanca (cierra QA-F02).
2. Manejo riguroso de timeouts y limpieza en 2 fases (SIGTERM -> SIGKILL) sobre grupos
   de procesos completos para evitar procesos zombies o saturación de runners CI/CD.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import ClassVar

import psutil
from pydantic import BaseModel, ConfigDict

from kallicode_testing.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# DTO Oficial KC-QA-B4-07
# ---------------------------------------------------------------------------
class ProcessExecutionResultDTO(BaseModel):
    """
    Representa el resultado inmutable de la ejecución de un proceso dentro del sandbox.
    Captura código de salida, salida estándar, errores, tiempos y banderas de timeout/señal.
    """

    model_config = ConfigDict(frozen=True)

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool
    terminated_signal: str | None = None


# ---------------------------------------------------------------------------
# Clase Principal ProcessCleaner
# ---------------------------------------------------------------------------
class ProcessCleaner:
    """
    Controlador de aislamiento de procesos, sanitización de entorno contra fugas (QA-F02)
    y orquestador de timeouts deterministas en 2 etapas (SIGTERM -> SIGKILL).
    """

    DEFAULT_ALLOWED_ENV: ClassVar[frozenset[str]] = frozenset(
        {
            "BASE_URL",
            "TENANT_ID",
            "JOB_ID",
            "SPEC_ID",
            "PATH",
            "PYTHONPATH",
            "LANG",
            "LC_ALL",
            "CI",
            "HOME",
            "SYSTEMROOT",
            "TMPDIR",
            "TEMP",
            "TMP",
        }
    )

    DEFAULT_FORBIDDEN_PATTERNS: ClassVar[tuple[str, ...]] = (
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
        "AZURE_",
        "LLM_",
        "OPENAI_",
        "AWS_",
        "VAULT",
        "CREDENTIAL",
    )

    DEFAULT_ORPHAN_PATTERNS: ClassVar[tuple[str, ...]] = (
        "chromium",
        "node",
        "pytest",
        "playwright",
    )

    def __init__(
        self,
        allowed_env_vars: list[str] | None = None,
        grace_period_sec: float = 3.0,
    ) -> None:
        """
        Inicializa el limpiador de procesos con variables permitidas y tiempo de gracia.
        """
        self.grace_period_sec = max(0.1, float(grace_period_sec))

        allowed_set = set(self.DEFAULT_ALLOWED_ENV)
        if allowed_env_vars:
            allowed_set.update(allowed_env_vars)
        self.allowed_env_vars: frozenset[str] = frozenset(allowed_set)

    def build_sanitized_env(
        self,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Construye un entorno limpio bajo lista blanca estricta.
        Purga activamente cualquier variable que contenga patrones sensibles del host (QA-F02).
        """
        sanitized: dict[str, str] = {}

        # 1. Filtrar variables del host que coincidan con la lista blanca
        for key, value in os.environ.items():
            if key in self.allowed_env_vars:
                # Comprobar que no contenga patrones prohibidos
                if not self._is_forbidden(key):
                    sanitized[key] = value

        # 2. Incorporar variables extra si no violan los patrones prohibidos
        if extra_env:
            for key, value in extra_env.items():
                if not self._is_forbidden(key):
                    sanitized[key] = value
                else:
                    logger.warning(
                        "process_cleaner.sanitized_forbidden_var_discarded",
                        key=key,
                    )

        return sanitized

    def _is_forbidden(self, key: str) -> bool:
        """Verifica si la clave contiene algún substring sensible prohibido."""
        key_upper = key.upper()
        return any(pattern in key_upper for pattern in self.DEFAULT_FORBIDDEN_PATTERNS)

    def run_with_timeout(
        self,
        cmd: list[str],
        timeout_sec: float,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> ProcessExecutionResultDTO:
        """
        Ejecuta el comando en un grupo de procesos independiente (start_new_session=True).
        Aplica protocolo en dos etapas ante timeout:
        1. SIGTERM a todo el grupo (os.killpg).
        2. Espera grace_period_sec.
        3. Si persiste, escala a SIGKILL sobre el grupo.
        Retorna ProcessExecutionResultDTO con timed_out=True y exit_code!=0.
        """
        clean_env = self.build_sanitized_env(extra_env)
        start_time = time.perf_counter()

        try:
            # start_new_session=True crea un nuevo Process Group independiente (setsid)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
                env=clean_env,
                text=True,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("process_cleaner.cmd_not_found", cmd=cmd, error=str(exc))
            return ProcessExecutionResultDTO(
                command=cmd,
                exit_code=127,
                stdout="",
                stderr=f"Command not found: {exc}",
                duration_ms=duration_ms,
                timed_out=False,
                terminated_signal=None,
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("process_cleaner.execution_error", cmd=cmd, error=str(exc))
            return ProcessExecutionResultDTO(
                command=cmd,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                duration_ms=duration_ms,
                timed_out=False,
                terminated_signal=None,
            )

        pgid = proc.pid  # En start_new_session=True, pgid es igual al pid del líder

        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ProcessExecutionResultDTO(
                command=cmd,
                exit_code=proc.returncode,
                stdout=stdout or "",
                stderr=stderr or "",
                duration_ms=duration_ms,
                timed_out=False,
                terminated_signal=None,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "process_cleaner.timeout_exceeded",
                cmd=cmd,
                timeout_sec=timeout_sec,
                pid=proc.pid,
            )
            terminated_signal = "SIGTERM"

            # Fase 1: Enviar SIGTERM al grupo completo
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass

            # Esperar período de gracia
            try:
                stdout, stderr = proc.communicate(timeout=self.grace_period_sec)
            except subprocess.TimeoutExpired:
                # Fase 2: Escalar a SIGKILL si aún continúa activo
                logger.warning(
                    "process_cleaner.escalating_to_sigkill",
                    cmd=cmd,
                    pid=proc.pid,
                )
                terminated_signal = "SIGKILL"
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                stdout, stderr = proc.communicate()

            duration_ms = (time.perf_counter() - start_time) * 1000.0
            exit_code = proc.returncode if proc.returncode is not None else -9
            if exit_code == 0:
                exit_code = -15 if terminated_signal == "SIGTERM" else -9

            return ProcessExecutionResultDTO(
                command=cmd,
                exit_code=exit_code,
                stdout=stdout or "",
                stderr=stderr or "",
                duration_ms=duration_ms,
                timed_out=True,
                terminated_signal=terminated_signal,
            )

    def cleanup_orphan_processes(
        self,
        patterns: list[str] | None = None,
    ) -> int:
        """
        Identifica y termina procesos huérfanos asociados al usuario actual.
        Protege estrictamente el proceso actual y su árbol de procesos ancestros.
        Retorna la cantidad total de procesos eliminados.
        """
        target_patterns = [p.lower() for p in (patterns or list(self.DEFAULT_ORPHAN_PATTERNS))]

        current_pid = os.getpid()
        current_ppid = os.getppid()

        # Identificar ancestros del proceso actual para salvaguarda absoluta
        protected_pids: set[int] = {0, 1, current_pid, current_ppid}
        try:
            curr_proc = psutil.Process(current_pid)
            for parent in curr_proc.parents():
                protected_pids.add(parent.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        current_uid: int | None = None
        if hasattr(os, "getuid"):
            current_uid = os.getuid()

        killed_count = 0

        for proc in psutil.process_iter(["pid", "name", "cmdline", "uids"]):
            try:
                pid = proc.info["pid"]
                if pid in protected_pids:
                    continue

                # Filtrar únicamente procesos del usuario actual si está soportado
                if current_uid is not None:
                    uids = proc.info.get("uids")
                    if uids and hasattr(uids, "real") and uids.real != current_uid:
                        continue

                name = (proc.info.get("name") or "").lower()
                cmdline_list = proc.info.get("cmdline") or []
                cmdline_str = " ".join(cmdline_list).lower()

                # Comprobar coincidencia con patrones objetivo
                matches = any(pat in name or pat in cmdline_str for pat in target_patterns)
                if not matches:
                    continue

                logger.info(
                    "process_cleaner.terminating_orphan_process",
                    pid=pid,
                    name=name,
                )
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)

                killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except Exception as exc:
                logger.warning(
                    "process_cleaner.cleanup_process_error",
                    pid=proc.pid,
                    error=str(exc),
                )
                continue

        return killed_count
