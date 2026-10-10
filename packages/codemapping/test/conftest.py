"""Configuración global de entorno para la suite de pruebas de codemapping."""

from __future__ import annotations

import os

os.environ.setdefault("CM_TENANT_ID", "test_tenant")
os.environ.setdefault("CM_CLIENT_ID", "test_tenant")
os.environ.setdefault("CM_REPO_URL", "https://github.com/example/repo.git")
