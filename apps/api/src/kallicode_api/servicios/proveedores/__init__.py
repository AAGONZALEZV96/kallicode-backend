"""Conectores de verificación real de permisos por proveedor (KC-BE-07-1)."""

from ._base import ResultadoPrueba
from .github import probar_github
from .github_actions import probar_github_actions
from .gitlab import probar_gitlab
from .jira import probar_jira
from .postgresql import probar_postgresql

PROVEEDORES_SOPORTADOS = {
    "github": probar_github,
    "gitlab": probar_gitlab,
    "jira": probar_jira,
    "github_actions": probar_github_actions,
    "postgresql": probar_postgresql,
    # Usan la misma API y el mismo token que su repositorio: se verifica el acceso al repo.
    "github_issues": probar_github,
    "gitlab_ci": probar_gitlab,
}

__all__ = [
    "PROVEEDORES_SOPORTADOS",
    "ResultadoPrueba",
    "probar_github",
    "probar_github_actions",
    "probar_gitlab",
    "probar_jira",
    "probar_postgresql",
]
