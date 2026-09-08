# tests/conftest.py
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "repo_ref"
GOLDEN_DIR = Path(__file__).parent / "golden"

EXTENSION_TO_LANGUAGE = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".py": "python",
    ".java": "java",
    ".sql": "sql",  # no está en CAPTURE -> parse_file devuelve []
}


@pytest.fixture(scope="session")
def repo_ref_path() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def golden_loader():
    def _load(name: str):
        path = GOLDEN_DIR / f"{name}.json"
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return _load


@pytest.fixture
def source_file_factory(repo_ref_path):
    """Construye un objeto con atributos .path/.language/.content,
    que es lo que espera parse_file()."""
    def _make(relative_path: str):
        full_path = repo_ref_path / relative_path
        content = full_path.read_text(encoding="utf-8")
        language = EXTENSION_TO_LANGUAGE.get(full_path.suffix, "unknown")
        return SimpleNamespace(
            path=str(full_path),
            language=language,
            content=content,
        )
    return _make


@pytest.fixture
def symbols_to_dicts():
    """Convierte list[Symbol] (dataclass) a list[dict] para comparar con JSON."""
    def _convert(symbols):
        return [asdict(s) for s in symbols]
    return _convert