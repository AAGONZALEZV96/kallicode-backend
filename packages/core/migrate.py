"""Aplica las migraciones versionadas de db/migrations/ (KC-DB-01-1).

Uso:
    uv run python packages/core/migrate.py                 # usa KC_DATABASE_URL_ADMIN
    uv run python packages/core/migrate.py --dsn postgresql://...
    uv run python packages/core/migrate.py --adoptar       # solo entornos preexistentes

Código de salida: 0 si todo quedó aplicado; 1 ante cualquier error (el
pipeline de despliegue y `make db` dependen de ello).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
from kallicode_core.config import get_settings
from kallicode_core.logging import configurar_logging, log
from kallicode_core.migraciones import MigracionError, migrar

DIRECTORIO_POR_DEFECTO = Path(__file__).resolve().parents[2] / "db" / "migrations"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runner de migraciones de Kallicode")
    parser.add_argument("--dsn", help="URL de conexión (por defecto KC_DATABASE_URL_ADMIN)")
    parser.add_argument("--dir", type=Path, default=DIRECTORIO_POR_DEFECTO)
    parser.add_argument(
        "--adoptar",
        action="store_true",
        help="Registra las pendientes sin ejecutarlas (bases aplicadas antes del runner)",
    )
    args = parser.parse_args(argv)

    configurar_logging()
    dsn = args.dsn or get_settings().database_url_admin
    if not dsn:
        log.error("migraciones.error", motivo="Falta --dsn o KC_DATABASE_URL_ADMIN")
        return 1
    try:
        asyncio.run(migrar(dsn, args.dir, adoptar=args.adoptar))
    except MigracionError as e:
        log.error("migraciones.error", motivo=str(e))
        return 1
    except (OSError, asyncpg.PostgresError) as e:  # host inalcanzable, credenciales
        log.error("migraciones.error", motivo=f"No se pudo conectar: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
