"""Utilidades para la normalización segura de rutas en el monorepo."""

from pathlib import Path


def resolve_safe_path(base_dir: str | Path, target_path: str | Path) -> Path:
    """
    Resuelve una ruta de forma absoluta y previene ataques de path-traversal.

    Garantiza que el target_path resultante resida dentro del directorio base_dir,
    incluso después de resolver symlinks, inyecciones absolutas o retrocesos ('..').
    """
    base = Path(base_dir).resolve()

    # Si target_path es absoluto (ej. /etc/passwd), pathlib descarta la base.
    # El resolve() evalúa la ruta canónica final.
    target = (base / Path(target_path)).resolve()

    # En Python 3.12 is_relative_to es la forma más rápida y segura de validar jerarquía
    if not target.is_relative_to(base):
        raise ValueError(f"Intento de Path-Traversal detectado o ruta fuera de base: {target_path}")

    return target
