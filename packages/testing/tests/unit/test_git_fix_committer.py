"""
Pruebas unitarias para GitFixCommitter (KC-QA-B4-12).
Valida:
- Creación de ramas de reparación dedicadas (repair/{slug}).
- Commits atómicos con formato estricto fix(qa): repair selector for AC-n.
- Construcción y serialización de Pull Request Mock inmutable (PullRequestMockDTO).
- Prevención estricta de Path Traversal (QA-F03) en la persistencia con resolve_safe_path.
- Resiliencia y degradación segura ante repositorios no inicializados o fallos de Git.
- Integración desacoplada con SelectorRepairEngine.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from kallicode_testing.git_fix_committer import GitFixCommitter, PullRequestMockDTO
from kallicode_testing.selector_repair import SelectorRepairEngine
from pydantic import ValidationError


def _init_git_repo(repo_dir: Path) -> None:
    """Inicializa un repositorio Git local real y realiza un commit base."""
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "KalliCode Tester"], cwd=str(repo_dir), check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "tester@kallicode.local"], cwd=str(repo_dir), check=True
    )

    initial_file = repo_dir / "README.md"
    initial_file.write_text("# Initial Repo for QA Repair Tests\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial base commit"], cwd=str(repo_dir), check=True
    )


# ---------------------------------------------------------------------------
# Prueba 1: Creación de rama de reparación exitosa
# ---------------------------------------------------------------------------
def test_creacion_rama_reparacion_exitosa(tmp_path: Path) -> None:
    """Valida la creación y conmutación a una nueva rama repair/{slug} en un repo Git local."""
    repo_dir = tmp_path / "git_repo_1"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    committer = GitFixCommitter(repo_path=repo_dir)
    branch_name = "repair/ac-01-selector-fix"

    success = committer.create_repair_branch(branch_name)
    assert success is True

    # Verificar con git directo que la rama actual coincide
    res = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == branch_name

    # Intentar crear una rama con nombre vacío debe retornar False de forma controlada
    assert committer.create_repair_branch("") is False


# ---------------------------------------------------------------------------
# Prueba 2: Commit atómico con criterio BDD
# ---------------------------------------------------------------------------
def test_commit_atomico_con_criterio_bdd(tmp_path: Path) -> None:
    """Valida que el commit se genere con el mensaje obligatorio fix(qa): repair selector for AC-n."""
    repo_dir = tmp_path / "git_repo_2"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    committer = GitFixCommitter(repo_path=repo_dir)
    committer.create_repair_branch("repair/ac-01-checkout")

    # Modificar un archivo de especificación
    spec_file = repo_dir / "checkout.spec.ts"
    spec_file.write_text(
        "await page.locator('[data-testid=\"btn-submit\"]').click();", encoding="utf-8"
    )

    criterion_id = "AC-01"
    commit_sha = committer.create_commit(criterion_id, ["checkout.spec.ts"])

    assert isinstance(commit_sha, str)
    assert len(commit_sha) == 40

    # Verificar mensaje del commit en Git
    log_res = subprocess.run(
        ["git", "log", "-1", "--pretty=%B"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    expected_msg = f"fix(qa): repair selector for {criterion_id}"
    assert expected_msg in log_res.stdout


# ---------------------------------------------------------------------------
# Prueba 3: Generación de Pull Request Mock inmutable
# ---------------------------------------------------------------------------
def test_generacion_pull_request_mock_inmutable() -> None:
    """Valida la estructura, tipado, inmutabilidad y serialización de PullRequestMockDTO."""
    committer = GitFixCommitter()
    criterion_id = "AC-02"
    branch_name = "repair/ac-02-cart-fix"
    commit_sha = "a1b2c3d4e5f67890123456789abcdef012345678"
    changed_files = ["src/specs/cart.spec.ts"]

    pr_mock = committer.build_mock_pull_request(
        criterion_id=criterion_id,
        branch_name=branch_name,
        commit_sha=commit_sha,
        changed_files=changed_files,
    )

    assert isinstance(pr_mock, PullRequestMockDTO)
    assert pr_mock.pr_id == "PR-MOCK-AC-02"
    assert pr_mock.title == "fix(qa): repair selector for AC-02"
    assert pr_mock.branch_name == branch_name
    assert pr_mock.base_branch == "main"
    assert pr_mock.status == "created"
    assert pr_mock.commit_sha == commit_sha
    assert pr_mock.changed_files == changed_files
    assert "cart.spec.ts" in pr_mock.summary
    assert "T" in pr_mock.created_at

    # Inmutabilidad estricta (frozen=True)
    with pytest.raises(ValidationError):
        pr_mock.status = "closed"

    # Serialización JSON válida
    raw_json = pr_mock.model_dump_json()
    parsed = json.loads(raw_json)
    assert parsed["pr_id"] == "PR-MOCK-AC-02"


# ---------------------------------------------------------------------------
# Prueba 4: Prevención de Path Traversal en persistencia (QA-F03)
# ---------------------------------------------------------------------------
def test_prevencion_path_traversal_persistencia_mock(tmp_path: Path) -> None:
    """Valida que resolve_safe_path bloquee escapes relativos o absolutos al persistir."""
    base_dir = tmp_path / "safe_sandbox"
    base_dir.mkdir()

    committer = GitFixCommitter(repo_path=base_dir)
    pr_mock = committer.build_mock_pull_request(
        criterion_id="AC-03",
        branch_name="repair/ac-03",
        commit_sha="1234567890123456789012345678901234567890",
        changed_files=["file.ts"],
    )

    # 1. Escape relativo ../../etc
    with pytest.raises(ValueError, match="Path-Traversal|fuera de base"):
        committer.persist_pull_request_mock(pr_mock, Path("../../etc"))

    # 2. Escape absoluto fuera del sandbox
    with pytest.raises(ValueError, match="Path-Traversal|fuera de base"):
        committer.persist_pull_request_mock(pr_mock, Path("/tmp/malicious_out"))


# ---------------------------------------------------------------------------
# Prueba 5: Resiliencia sin Git real o repositorio inválido
# ---------------------------------------------------------------------------
def test_resiliencia_sin_git_real_o_repo_invalido(tmp_path: Path) -> None:
    """Valida degradación segura sin excepciones no capturadas cuando no hay repo Git."""
    non_git_dir = tmp_path / "empty_folder_no_git"
    non_git_dir.mkdir()

    committer = GitFixCommitter(repo_path=non_git_dir)

    # create_repair_branch debe retornar False limpiamente
    assert committer.create_repair_branch("repair/anything") is False

    # create_commit debe generar un SHA determinista de fallback sin colapsar
    fallback_sha = committer.create_commit("AC-99", ["missing_file.ts"])
    assert isinstance(fallback_sha, str)
    assert len(fallback_sha) == 40


# ---------------------------------------------------------------------------
# Prueba 6: Persistencia exitosa y lectura de PR Mock
# ---------------------------------------------------------------------------
def test_persistencia_exitosa_pull_request_mock(tmp_path: Path) -> None:
    """Valida la correcta escritura en disco del archivo pr_mock_{pr_id}.json."""
    repo_dir = tmp_path / "repo_pr"
    repo_dir.mkdir()
    out_dir = repo_dir / "mock_prs"

    committer = GitFixCommitter(repo_path=repo_dir)
    pr_mock = committer.build_mock_pull_request(
        criterion_id="AC-04",
        branch_name="repair/ac-04-filter",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
        changed_files=["filters.spec.ts"],
    )

    persisted_path = committer.persist_pull_request_mock(pr_mock, out_dir)
    assert persisted_path.exists()
    assert persisted_path.name == f"pr_mock_{pr_mock.pr_id}.json"

    data = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert data["pr_id"] == "PR-MOCK-AC-04"
    assert data["branch_name"] == "repair/ac-04-filter"


# ---------------------------------------------------------------------------
# Prueba 7: Integración con SelectorRepairEngine
# ---------------------------------------------------------------------------
def test_integracion_con_selector_repair(tmp_path: Path) -> None:
    """Valida que SelectorRepairEngine pueda orquestar GitFixCommitter transparentemente."""
    repo_dir = tmp_path / "engine_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    committer = GitFixCommitter(repo_path=repo_dir)
    engine = SelectorRepairEngine(git_committer=committer)

    # Crear archivo modificado
    patched_file = repo_dir / "login.spec.ts"
    patched_file.write_text(
        "await page.locator('[data-testid=\"btn-login\"]').click();", encoding="utf-8"
    )

    pr_dto = engine.commit_and_mock_pr(
        criterion_id="AC-05",
        branch_name="repair/ac-05-login-fix",
        changed_files=["login.spec.ts"],
    )

    assert pr_dto is not None
    assert pr_dto.title == "fix(qa): repair selector for AC-05"
    assert pr_dto.branch_name == "repair/ac-05-login-fix"
    assert len(pr_dto.commit_sha) == 40


# ---------------------------------------------------------------------------
# Prueba 8: Command Executor personalizado y manejo de excepciones
# ---------------------------------------------------------------------------
def test_command_executor_personalizado_y_manejo_excepciones(tmp_path: Path) -> None:
    """Valida el soporte de executors personalizados de 1 y 2 parámetros y captura de excepciones."""
    repo_dir = tmp_path / "mock_exec_ws"
    repo_dir.mkdir()

    # Executor de 1 parámetro simulando éxito
    def mock_exec_1_param(args: list[str]) -> tuple[int, str, str]:
        if "checkout" in args:
            return 0, "Switched to a new branch 'mock-branch'", ""
        return 0, "mock_sha_123456789012345678901234567890", ""

    committer_1 = GitFixCommitter(repo_path=repo_dir, command_executor=mock_exec_1_param)
    assert committer_1.create_repair_branch("mock-branch") is True

    # Executor de 2 parámetros que lanza excepción
    def mock_exec_failing(args: list[str], cwd: Path) -> tuple[int, str, str]:
        raise RuntimeError("Simulated process failure")

    committer_failing = GitFixCommitter(repo_path=repo_dir, command_executor=mock_exec_failing)
    assert committer_failing.create_repair_branch("failing-branch") is False
