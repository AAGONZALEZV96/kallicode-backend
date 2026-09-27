import asyncio
import hashlib
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Obtenemos la URL de la base de datos desde las variables de entorno
DB_URL = os.getenv(
    "KC_DATABASE_URL_ADMIN",
    "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode_test",
)


async def run_migrations():
    # Creamos el motor de conexión asíncrono
    engine = create_async_engine(DB_URL, echo=False)

    # Definimos la carpeta donde irán los futuros archivos de migración
    MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
    MIGRATIONS_DIR.mkdir(exist_ok=True)  # Crea la carpeta si no existe

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    async with engine.begin() as conn:
        # 1. Crear el esquema y la tabla inmutable del Ledger (si no existen)
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS core;"))
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS core.schema_migrations (
                id BIGSERIAL PRIMARY KEY,
                migration_name TEXT NOT NULL UNIQUE,
                checksum_sha256 TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)
        )

        # 2. Si es un entorno nuevo (sin archivos), insertamos un baseline.
        # Esto es clave para que el CI de GitHub Actions no falle al contar los registros.
        if not migration_files:
            print(
                f"No se encontraron archivos SQL en {MIGRATIONS_DIR}. Registrando baseline inicial."
            )
            await conn.execute(
                text("""
                    INSERT INTO core.schema_migrations (migration_name, checksum_sha256)
                    VALUES ('00_baseline', 'mock_hash_genesis_inicial')
                    ON CONFLICT (migration_name) DO NOTHING;
                """)
            )
            return

        # 3. Ejecutar las migraciones reales si existen archivos
        for file_path in migration_files:
            name = file_path.name
            content = file_path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # Verificar si esta migración ya se aplicó previamente
            res = await conn.execute(
                text(
                    "SELECT checksum_sha256 FROM core.schema_migrations WHERE migration_name = :name"
                ),
                {"name": name},
            )
            row = res.fetchone()

            if row:
                if row[0] != checksum:
                    raise ValueError(
                        f"SEGURIDAD: El archivo {name} cambió su contenido después de haber sido aplicado."
                    )
                print(f"Migración {name} ya estaba aplicada.")
                continue

            # Aplicar la nueva migración
            print(f"Aplicando migración {name}...")
            await conn.execute(text(content))

            # Registrar el hash en el ledger para inmutabilidad
            await conn.execute(
                text("""
                    INSERT INTO core.schema_migrations (migration_name, checksum_sha256)
                    VALUES (:name, :checksum)
                """),
                {"name": name, "checksum": checksum},
            )
            print(f"✔️ Migración {name} aplicada y registrada exitosamente.")


if __name__ == "__main__":
    print("Iniciando orquestador de migraciones Kallicode...")
    try:
        asyncio.run(run_migrations())
        print("Proceso de migraciones completado con éxito.")
        sys.exit(0)
    except Exception as e:
        print(f"Error crítico ejecutando migraciones: {e}")
        sys.exit(1)
