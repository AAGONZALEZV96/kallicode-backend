"""Entorno de la suite unitaria de codemapping.

El conftest del paquete (packages/codemapping/conftest.py) fija lo mismo para `tests/`; este se
conserva porque mutmut copia `test/` sin el conftest del paquete.
"""

from __future__ import annotations

import os

# No colocar CM_TENANT_ID por defecto para permitir que test_codemapping_config pruebe su obligatoriedad
os.environ.setdefault("CM_REPO_URL", "https://github.com/example/repo.git")
