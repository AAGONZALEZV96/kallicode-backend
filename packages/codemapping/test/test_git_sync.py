"""Pruebas unitarias para robustez de Git, ramas tenant y fallbacks (KC-CM-04-2)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from kallicode_codemapping.ingest_repo import (
    resolver_rama_tenant,
    verificar_ancestro_comun,
)


def test_resolver_rama_tenant_usa_branch_map() -> None:
    settings = MagicMock(target_branch=None, default_branch="main")
    branch_map = {"cliente_a": "develop", "cliente_b": "release/v1.0"}

    assert resolver_rama_tenant("cliente_a", settings, branch_map) == "develop"
    assert resolver_rama_tenant("cliente_b", settings, branch_map) == "release/v1.0"
    assert resolver_rama_tenant("cliente_c", settings, branch_map) == "main"


@patch("subprocess.run")
def test_verificar_ancestro_comun_detecta_force_push(mock_run: MagicMock) -> None:
    # returncode = 1 simula que merge-base --is-ancestor falló (force push)
    mock_run.return_value = MagicMock(returncode=1)

    es_ancestro = verificar_ancestro_comun(Path("/fake/repo"), "sha_antiguo", "sha_nuevo")
    assert es_ancestro is False

    # returncode = 0 simula historia lineal válida
    mock_run.return_value = MagicMock(returncode=0)
    assert verificar_ancestro_comun(Path("/fake/repo"), "sha_antiguo", "sha_nuevo") is True
