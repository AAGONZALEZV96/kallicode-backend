"""Configuración global de entorno para la suite de pruebas de codemapping."""

from __future__ import annotations

import os

# No colocar CM_TENANT_ID por defecto para permitir que test_codemapping_config pruebe su obligatoriedad
os.environ.setdefault("CM_REPO_URL", "https://github.com/example/repo.git")
