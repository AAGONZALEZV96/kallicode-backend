"""Pruebas unitarias para SyntheticRepoFixture Cero-Nube (KC-QA-B5-06)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from kallicode_testing.secrets_mock import LocalKeyVaultMock
from kallicode_testing.synthetic_repo_fixture import SyntheticRepoFixture


def test_create_local_repo_y_ramas_reales(tmp_path: Path) -> None:
    """Inicializa repositorio Git local con commit inicial, ramas `main` y `develop`."""
    fixture = SyntheticRepoFixture()
    repo_dir = tmp_path / "sample-repo"

    created = fixture.create_local_repo(repo_dir, branches=["main", "develop", "feature/demo"])

    assert created == repo_dir.resolve()
    assert (created / ".git").is_dir()

    branches = fixture.list_branches(created)
    assert branches == ["develop", "feature/demo", "main"]

    log_proc = subprocess.run(
        ["git", "-C", str(created), "log", "--oneline", "-n", "1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "initial architecture & app seed" in log_proc.stdout

    manifest = fixture.get_sample_files_manifest()
    assert len(manifest) >= 4
    assert "docs/arquitectura.md" in manifest
    for rel_path in manifest:
        assert (created / rel_path).is_file()


def test_cleanup_repo_es_completo_e_idempotente(tmp_path: Path) -> None:
    """Elimina el repositorio temporal en teardown y tolera doble limpieza."""
    fixture = SyntheticRepoFixture()
    repo_dir = tmp_path / "sample-repo-cleanup"
    fixture.create_local_repo(repo_dir, branches=["main", "develop"])
    assert repo_dir.exists()

    fixture.cleanup_repo(repo_dir)
    assert not repo_dir.exists()

    # Segunda invocación no debe lanzar error
    fixture.cleanup_repo(repo_dir)


def test_validacion_ramas_invalidas_y_boveda_local(tmp_path: Path) -> None:
    """Rechaza ramas vacías o inválidas e integra credenciales mock con LocalKeyVaultMock."""
    fixture = SyntheticRepoFixture()

    with pytest.raises(ValueError, match="no puede estar vacía"):
        fixture.create_local_repo(tmp_path / "empty-branches", branches=[])

    with pytest.raises(ValueError, match="inválido"):
        fixture.create_local_repo(tmp_path / "bad-branch", branches=["main", "rama .. invalida"])

    with pytest.raises(FileNotFoundError):
        fixture.list_branches(tmp_path / "no-git-repo")

    vault = LocalKeyVaultMock()
    try:
        secret_key = fixture.register_mock_credentials(
            vault,
            secret_name="test_b5_06_repo_secret",
            token_value="synthetic-test-value-local",
        )
        assert vault.get_secret(secret_key) == "synthetic-test-value-local"
    finally:
        vault.purge_vault()
