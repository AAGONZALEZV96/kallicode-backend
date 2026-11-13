"""Pruebas unitarias del refresco incremental (refresh.py) sin base de datos (KC-CM-06-6)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kallicode_codemapping import refresh
from kallicode_codemapping.config import build_settings
from kallicode_codemapping.ingest_repo import SourceFile
from kallicode_codemapping.parser_treesitter import Symbol

MOD = "kallicode_codemapping.refresh"
TENANT = "org_refresh"
SNAP = "snap-r"


def _cfg() -> Any:
    return build_settings(tenant_id=TENANT, repo_url="https://example.com/r.git")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    ruta = tmp_path / "repo"
    ruta.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(ruta)], check=True)
    _git(ruta, "config", "user.email", "t@t.local")
    _git(ruta, "config", "user.name", "t")
    (ruta / "a.py").write_text("def a():\n    return 1\n")
    (ruta / "b.py").write_text("def b():\n    return 2\n")
    (ruta / "notas.txt").write_text("no es código")
    _git(ruta, "add", "-A")
    _git(ruta, "commit", "-q", "-m", "init")
    return ruta


# --------------------------------------------------------------------------
# git: rama y diff
# --------------------------------------------------------------------------
def test_rama_actual(repo: Path) -> None:
    assert refresh._rama_actual(str(repo)) == "main"


def test_changed_paths_separa_modificados_y_eliminados_e_ignora_otras_extensiones(
    repo: Path,
) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "a.py").write_text("def a():\n    return 99\n")
    (repo / "c.py").write_text("def c():\n    return 3\n")
    (repo / "notas.txt").write_text("cambió pero no es código")
    _git(repo, "rm", "-q", "b.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "cambios")
    head = _git(repo, "rev-parse", "HEAD")

    cambiados, eliminados = refresh.changed_paths(str(repo), base, head)

    assert sorted(cambiados) == ["a.py", "c.py"]
    assert eliminados == ["b.py"]


def test_load_file_calcula_hash_y_lenguaje(repo: Path) -> None:
    sf = refresh.load_file(str(repo), "a.py")
    assert sf is not None
    assert sf.language == "python"
    assert sf.sha256 == hashlib.sha256(sf.content.encode("utf-8")).hexdigest()
    assert "def a" in sf.content


def test_load_file_inexistente_devuelve_none(repo: Path) -> None:
    assert refresh.load_file(str(repo), "no_existe.py") is None


# --------------------------------------------------------------------------
# run_refresh: decisiones de alto nivel
# --------------------------------------------------------------------------
def _loader(ultimo_sha: str | None) -> MagicMock:
    loader = MagicMock(name="GraphLoader")
    loader.__enter__.return_value = loader
    loader.__exit__.return_value = False
    loader.get_last_sha.return_value = ultimo_sha
    loader.start_snapshot.return_value = SNAP
    loader.load_files.return_value = {"a.py": "file-a"}
    return loader


def test_sin_sha_previo_ejecuta_full_build() -> None:
    loader = _loader(None)
    with (
        patch(f"{MOD}.GraphLoader", return_value=loader),
        patch(f"{MOD}.full_build") as full,
        patch(f"{MOD}.sync_repo") as sync,
    ):
        refresh.run_refresh(_cfg())
    full.assert_called_once()
    sync.assert_not_called()


def test_divergencia_de_historia_cae_a_full_build() -> None:
    loader = _loader("sha-viejo")
    with (
        patch(f"{MOD}.GraphLoader", return_value=loader),
        patch(f"{MOD}.sync_repo", return_value=("/repo", "sha-nuevo", True)),
        patch(f"{MOD}.full_build") as full,
    ):
        refresh.run_refresh(_cfg())
    full.assert_called_once()
    loader.start_snapshot.assert_not_called()


def test_grafo_al_dia_no_hace_nada_con_el_codigo_pero_refresca_la_documentacion() -> None:
    """KC-CM-08-5: el código no cambió, pero los documentos pueden haber derivado."""
    loader = _loader("sha-igual")
    loader.obtener_ultimo_snapshot_completo.return_value = "snap-ultimo"
    cfg = _cfg()
    with (
        patch(f"{MOD}.GraphLoader", return_value=loader),
        patch(f"{MOD}.sync_repo", return_value=("/repo", "sha-igual", False)),
        patch(f"{MOD}.full_build") as full,
        patch(f"{MOD}.refrescar_capa_documental") as docs,
    ):
        refresh.run_refresh(cfg)
    full.assert_not_called()
    loader.start_snapshot.assert_not_called()
    docs.assert_called_once_with(loader, cfg, "snap-ultimo")


# --------------------------------------------------------------------------
# run_refresh: refresco incremental
# --------------------------------------------------------------------------
def _simbolo() -> Symbol:
    return Symbol(
        id="s1",
        file="pkg/a.py",
        language="python",
        kind="function",
        name="a",
        start_line=1,
        end_line=2,
        signature="def a()",
        snippet="def a(): ...",
    )


def _incremental(loader: MagicMock, **parches: Any) -> MagicMock:
    tei = MagicMock()
    tei.embed_texts_sync.side_effect = lambda textos: [[0.0] * 4 for _ in textos]
    base: dict[str, Any] = {
        "GraphLoader": MagicMock(return_value=loader),
        "sync_repo": MagicMock(return_value=("/repo", "sha-nuevo", False)),
        "_rama_actual": MagicMock(return_value="main"),
        "changed_paths": MagicMock(return_value=(["a.py"], ["viejo.py"])),
        "load_file": MagicMock(
            return_value=SourceFile(path="a.py", language="python", sha256="h", content="x")
        ),
        "parse_file": MagicMock(return_value=[_simbolo()]),
        "describe_symbols": MagicMock(return_value=[{"id": "s1", "description_en": "desc"}]),
        "TEIEmbeddingClient": MagicMock(return_value=tei),
        "refrescar_capa_documental": MagicMock(),
    }
    base.update(parches)
    from contextlib import ExitStack

    stack = ExitStack()
    for nombre, doble in base.items():
        stack.enter_context(patch(f"{MOD}.{nombre}", doble))
    with stack:
        refresh.run_refresh(_cfg())
    return tei


def test_refresco_incremental_purga_recarga_y_completa() -> None:
    loader = _loader("sha-viejo")
    tei = _incremental(loader)

    loader.delete_file_subgraph.assert_called_once_with(["viejo.py"])
    loader.load_files.assert_called_once()
    loader.load_symbols.assert_called_once()
    items = loader.upsert_vectors.call_args.args[0]
    assert len(items) == 1
    assert items[0]["node_ref"] == "s1"
    assert items[0]["payload"]["module"] == "pkg"
    assert "desc" in items[0]["text"]
    tei.embed_texts_sync.assert_called_once()
    loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")


def test_refresco_incluye_la_capa_documental_en_el_mismo_snapshot() -> None:
    """KC-CM-08-5: purga, hash y stats documentales se registran en el snapshot del refresco."""
    loader = _loader("sha-viejo")
    docs = MagicMock()
    docs.side_effect = lambda *_a, **_k: loader.complete_snapshot.assert_not_called()
    _incremental(loader, refrescar_capa_documental=docs)

    docs.assert_called_once()
    assert docs.call_args.args[0] is loader and docs.call_args.args[2] == SNAP
    loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")


def test_refresco_solo_con_eliminados_no_recarga_ni_vectoriza() -> None:
    loader = _loader("sha-viejo")
    tei = _incremental(loader, changed_paths=MagicMock(return_value=([], ["viejo.py"])))

    loader.delete_file_subgraph.assert_called_once_with(["viejo.py"])
    loader.load_files.assert_not_called()
    loader.upsert_vectors.assert_not_called()
    tei.embed_texts_sync.assert_not_called()
    loader.complete_snapshot.assert_called_once_with(SNAP, estado="completo")


def test_refresco_sin_eliminados_no_purga() -> None:
    loader = _loader("sha-viejo")
    _incremental(loader, changed_paths=MagicMock(return_value=(["a.py"], [])))
    loader.delete_file_subgraph.assert_not_called()


def test_un_error_durante_el_refresco_marca_fallido_y_se_propaga() -> None:
    loader = _loader("sha-viejo")
    with pytest.raises(RuntimeError, match="parser roto"):
        _incremental(loader, parse_file=MagicMock(side_effect=RuntimeError("parser roto")))
    loader.complete_snapshot.assert_called_once_with(SNAP, estado="fallido", motivo="parser roto")
