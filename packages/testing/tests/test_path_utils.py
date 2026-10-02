"""Pruebas de unidad para la sanitización de rutas (Prevención Path-Traversal)."""

from pathlib import Path

import pytest
from kallicode_testing.utils.path_utils import resolve_safe_path


def test_resolve_safe_path_valid() -> None:
    """Verifica que una ruta relativa válida se resuelva correctamente dentro de la base."""
    base = Path("/app/workspace")
    target = "evidencias/test_1.png"
    resultado = resolve_safe_path(base, target)
    assert resultado == Path("/app/workspace/evidencias/test_1.png")


def test_resolve_safe_path_traversal_relative() -> None:
    """Verifica que un intento de path-traversal con '../' sea bloqueado."""
    base = Path("/app/workspace")
    target = "../../../etc/passwd"
    with pytest.raises(ValueError, match="Intento de Path-Traversal detectado"):
        resolve_safe_path(base, target)


def test_resolve_safe_path_absolute_injection() -> None:
    """Verifica que un target con ruta absoluta fuera de la base sea bloqueado."""
    base = Path("/app/workspace")
    target = "/etc/shadow"
    with pytest.raises(ValueError, match="Intento de Path-Traversal detectado"):
        resolve_safe_path(base, target)
