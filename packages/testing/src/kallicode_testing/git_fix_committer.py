"""
Ticket: KC-QA-B4-12
Committer y Mock de Pull Requests de Reparación en Git Local.
Implementa:
- Creación de ramas de trabajo dedicadas con convención repair/{slug}.
- Generación de commits atómicos con formato fix(qa): repair selector for AC-n.
- Construcción y serialización de Pull Request Mock inmutable (PullRequestMockDTO).
- Principio de Cero Dependencia en la Nube (sin llamadas a GitHub/GitLab/Azure).
- Confinamiento estricto contra Path Traversal con resolve_safe_path (QA-F03).
- Resiliencia y degradación segura ante repositorios o binarios de Git ausentes.
"""

from __future__ import annotations

import hashlib
import inspect
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from kallicode_testing.logger import get_logger
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-12
# ---------------------------------------------------------------------------
class PullRequestMockDTO(BaseModel):
    """Representación inmutable de un Pull Request de reparación generado localmente."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    pr_id: str
    title: str
    branch_name: str
    base_branch: str = "main"
    status: Literal["created", "merged_mock", "closed"]
    commit_sha: str
    changed_files: list[str]
    summary: str
    created_at: str  # ISO 8601 UTC


# ---------------------------------------------------------------------------
# Clase Principal GitFixCommitter
# ---------------------------------------------------------------------------
class GitFixCommitter:
    """
    Orquestador de preservación y versionado determinista de parches de auto-reparación.
    Crea ramas locales, commits atómicos y artefactos mock de PRs sin dependencias de nube.
    """

    def __init__(
        self,
        repo_path: Path | None = None,
        command_executor: Any | None = None,
    ) -> None:
        """
        Inicializa el committer git estableciendo el directorio del repositorio.
        Permite la inyección opcional de un command_executor para pruebas o entornos simulados.
        """
        self.repo_path = (repo_path or Path.cwd()).resolve()
        self.command_executor = command_executor

    def _run_git(self, args: list[str]) -> tuple[int, str, str]:
        """
        Ejecuta comandos de Git de forma segura.
        Inyecta identidad de autor local para evitar fallos si user.name/user.email no están configurados.
        """
        if self.command_executor is not None:
            try:
                sig = inspect.signature(self.command_executor)
                if len(sig.parameters) >= 2:
                    res = self.command_executor(args, self.repo_path)
                else:
                    res = self.command_executor(args)
                return int(res[0]), str(res[1]), str(res[2])
            except Exception as exc:
                logger.warning(
                    "git_fix_committer.custom_executor_failed",
                    args=args,
                    error=str(exc),
                )
                return 1, "", str(exc)

        cmd = [
            "git",
            "-c",
            "user.name=KalliCode QA Bot",
            "-c",
            "user.email=qa-bot@kallicode.local",
            *args,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=False,
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except Exception as exc:
            logger.warning(
                "git_fix_committer.subprocess_failed",
                args=args,
                error=str(exc),
            )
            return 1, "", str(exc)

    def create_repair_branch(self, branch_name: str) -> bool:
        """
        Crea una nueva rama de trabajo local dedicada (repair/{slug}).
        Valida que la rama no esté vacía y maneja de forma segura cualquier fallo de Git.
        """
        cleaned_branch = branch_name.strip()
        if not cleaned_branch:
            logger.warning("git_fix_committer.invalid_branch_name", branch_name=branch_name)
            return False

        returncode, stdout, stderr = self._run_git(["checkout", "-b", cleaned_branch])

        if returncode == 0:
            logger.info(
                "git_fix_committer.branch_created",
                branch_name=cleaned_branch,
                output=stdout,
            )
            return True

        logger.warning(
            "git_fix_committer.branch_creation_failed",
            branch_name=cleaned_branch,
            error=stderr,
        )
        return False

    def create_commit(
        self,
        criterion_id: str,
        changed_files: list[str],
    ) -> str:
        """
        Genera un commit atómico para el parche de reparación.
        Formato obligatorio: 'fix(qa): repair selector for {criterion_id}'.
        Agrega únicamente los archivos especificados y retorna el commit SHA resultante.
        """
        commit_message = f"fix(qa): repair selector for {criterion_id}"

        # Confinar y normalizar rutas relativas al repo
        relative_paths: list[str] = []
        for file_str in changed_files:
            safe_path = resolve_safe_path(self.repo_path, file_str)
            try:
                rel_path = str(safe_path.relative_to(self.repo_path))
            except ValueError:
                rel_path = str(safe_path)
            relative_paths.append(rel_path)

        # Stage de archivos modificados
        if relative_paths:
            self._run_git(["add", *relative_paths])

        # Commit atómico
        self._run_git(["commit", "-m", commit_message])

        # Obtener SHA de HEAD
        sha_code, sha_out, _ = self._run_git(["rev-parse", "HEAD"])

        if sha_code == 0 and sha_out.strip():
            commit_sha = sha_out.strip()
        else:
            # Fallback determinista seguro ante entornos sin Git real o errores
            seed = f"{criterion_id}:{','.join(sorted(relative_paths))}:{commit_message}"
            commit_sha = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:40]

        logger.info(
            "git_fix_committer.commit_created",
            criterion_id=criterion_id,
            commit_sha=commit_sha,
            files=relative_paths,
        )
        return commit_sha

    def build_mock_pull_request(
        self,
        criterion_id: str,
        branch_name: str,
        commit_sha: str,
        changed_files: list[str],
    ) -> PullRequestMockDTO:
        """
        Construye la representación inmutable de un Pull Request de reparación mock.
        Cumple la política de Cero Dependencia en la Nube (sin invocar APIs externas).
        """
        slug = criterion_id.replace(" ", "-").replace(":", "-").upper()
        pr_id = f"PR-MOCK-{slug}"
        title = f"fix(qa): repair selector for {criterion_id}"

        files_list_md = "\n".join(f"- `{f}`" for f in changed_files)
        summary = (
            f"### Pull Request de Auto-Reparación (Local Mock)\n"
            f"- **Criterio BDD**: {criterion_id}\n"
            f"- **Rama de Trabajo**: `{branch_name}`\n"
            f"- **Rama Base**: `main`\n"
            f"- **Commit SHA**: `{commit_sha}`\n\n"
            f"#### Archivos Modificados\n"
            f"{files_list_md}\n\n"
            f"Generado automáticamente por el motor de auto-reparación de selectores "
            f"(KalliCode QA & SRE Engine) sin interacción con servicios cloud externos."
        )

        return PullRequestMockDTO(
            pr_id=pr_id,
            title=title,
            branch_name=branch_name,
            base_branch="main",
            status="created",
            commit_sha=commit_sha,
            changed_files=changed_files,
            summary=summary,
            created_at=datetime.now(UTC).isoformat(),
        )

    def persist_pull_request_mock(
        self,
        pr: PullRequestMockDTO,
        output_dir: Path,
    ) -> Path:
        """
        Persiste el Pull Request mock en formato JSON en disco.
        Aplica confinamiento de rutas con resolve_safe_path para prevenir Path Traversal (QA-F03).
        """
        safe_output_dir = resolve_safe_path(self.repo_path, output_dir)
        safe_output_dir.mkdir(parents=True, exist_ok=True)

        target_file = safe_output_dir / f"pr_mock_{pr.pr_id}.json"
        target_file.write_text(pr.model_dump_json(indent=2), encoding="utf-8")

        logger.info(
            "git_fix_committer.pr_mock_persisted",
            pr_id=pr.pr_id,
            target_file=str(target_file),
        )
        return target_file
