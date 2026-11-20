"""Fixture de Repositorios Git Sintéticos Locales Cero-Nube (KC-QA-B5-06).

Ticket: KC-QA-B5-06
Épica: EQA4 — Contrato de API, Esquema y Migraciones (Bloque 5)
Propósito:
- Inicializar repositorios Git válidos en disco local (`git init`, commits iniciales,
  ramas `main`, `develop` y opcionales) sin conectarse jamás a APIs de GitHub/GitLab.
- Proveer un manifiesto determinista de archivos de código y arquitectura de muestra.
- Integrar credenciales mock locales en `LocalKeyVaultMock` (AES-256-GCM).
- Garantizar limpieza completa en teardown (`cleanup_repo`).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from kallicode_testing.logger import get_logger
from kallicode_testing.secrets_mock import LocalKeyVaultMock
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

DEFAULT_SAMPLE_BRANCHES: tuple[str, ...] = ("main", "develop")
_INVALID_BRANCH_RE = re.compile(r"[\s~^:?*\[\\]|\.\.|@\{|//")


class SyntheticRepoFixture:
    """Administrador de repositorios Git locales sintéticos para pruebas E2E e ingesta."""

    def get_sample_files_manifest(self) -> dict[str, str]:
        """Retorna el manifiesto determinista de archivos de código y arquitectura."""
        return {
            "README.md": (
                "# Sample Synthetic Repository\n\n"
                "Repositorio local Cero-Nube para validación E2E de Onboarding KalliCode.\n"
            ),
            "docs/arquitectura.md": (
                "# Arquitectura del Servicio de Órdenes\n\n"
                "- API REST FastAPI con aislamiento multi-tenant por `tenant_id`.\n"
                "- Persistencia transaccional PostgreSQL 16 y cache Redis.\n"
            ),
            "pyproject.toml": (
                '[project]\nname = "sample-synthetic-service"\nversion = "0.1.0"\n'
                'requires-python = ">=3.12"\n'
            ),
            "src/app.py": (
                '"""Punto de entrada del servicio sintético."""\n\n'
                "def health() -> dict[str, str]:\n"
                '    return {"status": "ok"}\n'
            ),
            "src/services/orders.py": (
                '"""Lógica sintética de dominio para CodeMapping."""\n\n'
                "def calculate_total(unit_price: float, quantity: int) -> float:\n"
                "    return round(unit_price * quantity, 2)\n"
            ),
        }

    def _run_git(self, repo_path: Path, args: list[str]) -> str:
        """Ejecuta comandos Git locales de forma determinista sin dependencias remotas."""
        cmd = [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "user.name=KalliCode Synthetic QA",
            "-c",
            "user.email=qa-synthetic@kallicode.local",
            *args,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Fallo ejecutando git {' '.join(args)} en {repo_path}: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _validate_branch_names(self, branches: list[str]) -> list[str]:
        """Valida nombres de rama evitando cadenas vacías o patrones inválidos de Git."""
        if not branches:
            raise ValueError("La lista de ramas no puede estar vacía.")
        cleaned: list[str] = []
        for raw in branches:
            name = raw.strip()
            if (
                not name
                or name.startswith("/")
                or name.endswith(("/", ".lock"))
                or _INVALID_BRANCH_RE.search(name)
            ):
                raise ValueError(f"Nombre de rama sintética inválido: {raw!r}")
            if name not in cleaned:
                cleaned.append(name)
        return cleaned

    def create_local_repo(
        self,
        repo_path: Path,
        branches: list[str] | None = None,
    ) -> Path:
        """Crea un repositorio Git local válido con commit inicial y ramas solicitadas."""
        target = Path(repo_path).resolve()
        branch_list = self._validate_branch_names(
            list(branches) if branches is not None else list(DEFAULT_SAMPLE_BRANCHES)
        )

        target.mkdir(parents=True, exist_ok=True)
        primary_branch = "main" if "main" in branch_list else branch_list[0]

        subprocess.run(
            ["git", "init", "-b", primary_branch, str(target)],
            capture_output=True,
            text=True,
            check=True,
        )
        self._run_git(target, ["config", "user.name", "KalliCode Synthetic QA"])
        self._run_git(target, ["config", "user.email", "qa-synthetic@kallicode.local"])

        for rel_path, content in self.get_sample_files_manifest().items():
            file_target = resolve_safe_path(target, rel_path)
            file_target.parent.mkdir(parents=True, exist_ok=True)
            file_target.write_text(content, encoding="utf-8")

        self._run_git(target, ["add", "."])
        self._run_git(target, ["commit", "-m", "feat(synthetic): initial architecture & app seed"])

        for branch_name in branch_list:
            if branch_name == primary_branch:
                continue
            self._run_git(target, ["branch", branch_name])

        logger.info(
            "synthetic_repo.created",
            repo_path=str(target),
            branches=branch_list,
        )
        return target

    def list_branches(self, repo_path: Path) -> list[str]:
        """Lista las ramas reales existentes en el repositorio Git local."""
        target = Path(repo_path).resolve()
        if not (target / ".git").exists():
            raise FileNotFoundError(f"No existe repositorio Git válido en: {target}")
        raw = self._run_git(target, ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def register_mock_credentials(
        self,
        vault: LocalKeyVaultMock,
        *,
        secret_name: str = "sample_repo_mock_token",
        token_value: str = "kallicode_local_synthetic_repo_cred",
    ) -> str:
        """Almacena credenciales sintéticas cifradas (AES-256-GCM) en `LocalKeyVaultMock`."""
        vault.set_secret(secret_name, token_value)
        logger.info("synthetic_repo.vault_credential_stored", secret_name=secret_name)
        return secret_name

    def cleanup_repo(self, repo_path: Path) -> None:
        """Elimina de forma segura e idempotente el repositorio sintético local."""
        target = Path(repo_path).resolve()
        if target.exists():
            shutil.rmtree(target)
            logger.info("synthetic_repo.cleaned", repo_path=str(target))
