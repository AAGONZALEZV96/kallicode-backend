import sys
from pathlib import Path

# Resolver la raíz del repositorio
ROOT_DIR = Path(__file__).resolve().parent.parent

# Inyectar los paquetes del monorepo al path de Python
sys.path.insert(0, str(ROOT_DIR / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "core" / "src"))

from kallicode_api.main import app
