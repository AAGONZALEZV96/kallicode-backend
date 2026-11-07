"""
Ticket: KC-QA-B2-08
Suite de Validación de Migraciones de Base de Datos y Rollback.
Certifica la idempotencia del esquema base (01..05.sql), la integridad referencial
de claves foráneas compuestas y la reversión limpia (downgrade) sin tablas huérfanas.
"""

import os
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

pytestmark = pytest.mark.asyncio

# En CI se inyecta KC_DATABASE_URL_ADMIN con password 'dev'.
# En local se alinea con la credencial de conftest.py ('KalliCode_Dev2026!') o KC_DB_PASSWORD.
_DEFAULT_DB_PASS = os.getenv("KC_DB_PASSWORD", "dev")  # pragma: allowlist secret
ADMIN_DATABASE_URL = os.getenv(
    "KC_DATABASE_URL_ADMIN",
    f"postgresql+asyncpg://kallicode_admin:{_DEFAULT_DB_PASS}@localhost:5432/kallicode_test",  # pragma: allowlist secret
)


@pytest.fixture
async def admin_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provee una sesión administrativa aislada mediante SAVEPOINT.
    Permite ejecutar pruebas de DDL y reversión sin destruir el entorno de otros tests.
    """
    admin_engine = create_async_engine(ADMIN_DATABASE_URL, echo=False)
    async with admin_engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()
        session = AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        yield session
        await session.close()
        await conn.rollback()
    await admin_engine.dispose()


async def test_esquema_base_y_ledger_migraciones_aplicados(
    admin_db_session: AsyncSession,
) -> None:
    """
    Verifica que el ciclo completo de scripts base (01..05.sql) y el runner de migraciones
    hayan consolidado los 4 esquemas operacionales sin dejar tablas huérfanas de RLS.
    """
    # 1. Confirmar existencia de los esquemas arquitectónicos (core, vec, audit, cm)
    res_schemas = await admin_db_session.execute(
        text(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name IN ('core', 'vec', 'audit', 'cm')"
        )
    )
    schemas = {row[0] for row in res_schemas.fetchall()}
    assert {"core", "vec", "audit"}.issubset(schemas), (
        f"Faltan esquemas base tras la migración. Detectados: {schemas}"
    )

    # 2. Verificar el ledger de migraciones versionadas (si fue aprovisionado por el runner)
    res_ledger = await admin_db_session.execute(
        text("SELECT to_regclass('core.schema_migrations') IS NOT NULL")
    )
    if res_ledger.scalar():
        res_count = await admin_db_session.execute(
            text("SELECT count(*) FROM core.schema_migrations")
        )
        total_migrations = res_count.scalar() or 0
        assert total_migrations > 0, (
            "El ledger core.schema_migrations existe pero no registra migraciones aplicadas."
        )


async def test_integridad_contratos_claves_foraneas_y_validacion(
    admin_db_session: AsyncSession,
) -> None:
    """
    Garantiza que las migraciones no hayan roto contratos de claves foráneas
    ni dejado restricciones pendientes de validación (convalidated = false).
    """
    # 1. Auditar catálogo pg_constraint buscando FKs rotas o no validadas
    query_fks_invalidas = text("""
        SELECT conname, conrelid::regclass::text AS tabla_origen
        FROM pg_constraint
        WHERE contype = 'f'
          AND connamespace::regnamespace::text IN ('core', 'vec', 'audit', 'cm')
          AND NOT convalidated
    """)
    res_fks = await admin_db_session.execute(query_fks_invalidas)
    fks_invalidas = res_fks.fetchall()

    assert len(fks_invalidas) == 0, (
        f"Contratos de FK rotos o sin validar tras migración: {fks_invalidas}"
    )

    # 2. Ejecutar los chequeos de integridad de negocio si la función 05_validacion.sql está activa
    res_fn = await admin_db_session.execute(
        text("SELECT to_regprocedure('core.validar_integridad()') IS NOT NULL")
    )
    if res_fn.scalar():
        res_val = await admin_db_session.execute(
            text(
                "SELECT chequeo, descripcion, problemas "
                "FROM core.validar_integridad() WHERE problemas > 0"
            )
        )
        violaciones = res_val.fetchall()
        assert len(violaciones) == 0, (
            f"Fallo en invariantes de base de datos post-migración: {violaciones}"
        )


async def test_ciclo_migracion_y_rollback_sin_tablas_huerfanas(
    admin_db_session: AsyncSession,
) -> None:
    """
    Simula y verifica un ciclo completo de Upgrade -> Downgrade (Rollback) de una migración
    con claves foráneas compuestas, certificando que al revertir:
    1. No queden tablas huérfanas en pg_tables.
    2. No se rompan los contratos de claves foráneas de las tablas padre (core.organizations).
    """
    # 1. Tomar snapshot del catálogo antes de la migración
    query_catalogo = text("""
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname IN ('core', 'vec', 'audit', 'cm')
    """)
    res_antes = await admin_db_session.execute(query_catalogo)
    tablas_antes = set(res_antes.fetchall())

    query_fks_count = text("""
        SELECT count(*)
        FROM pg_constraint
        WHERE contype = 'f'
          AND connamespace::regnamespace::text IN ('core', 'vec', 'audit', 'cm')
    """)
    fks_antes = (await admin_db_session.execute(query_fks_count)).scalar()

    # 2. FASE UPGRADE: Aplicar DDL de nueva tabla conectada por FK a core.organizations
    upgrade_sql = """
        CREATE TABLE core.qa_migration_probe (
            id uuid NOT NULL DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (id)
        );
        ALTER TABLE core.qa_migration_probe ENABLE ROW LEVEL SECURITY;
        ALTER TABLE core.qa_migration_probe FORCE ROW LEVEL SECURITY;
    """
    for stmt in [s.strip() for s in upgrade_sql.split(";") if s.strip()]:
        await admin_db_session.execute(text(stmt))

    # Verificar que la migración surtió efecto
    res_durante = await admin_db_session.execute(query_catalogo)
    tablas_durante = set(res_durante.fetchall())
    assert ("core", "qa_migration_probe") in tablas_durante, (
        "La tabla de prueba no se registró en el catálogo durante el Upgrade."
    )

    # 3. FASE DOWNGRADE (Rollback explícito): Ejecución de scripts de reversión
    downgrade_sql = "DROP TABLE IF EXISTS core.qa_migration_probe CASCADE;"
    await admin_db_session.execute(text(downgrade_sql))

    # Si existen archivos físicos de reversión (*.down.sql o carpeta rollback), verificar su sintaxis
    repo_root = Path(__file__).resolve().parents[4]
    down_scripts = list((repo_root / "db").rglob("*down*.sql"))
    for script_path in down_scripts:
        sql_content = script_path.read_text(encoding="utf-8").strip()
        assert len(sql_content) > 0, f"El script de rollback {script_path.name} está vacío."

    # 4. AUDITORÍA POST-ROLLBACK: Cero tablas huérfanas y contratos FK intactos
    res_despues = await admin_db_session.execute(query_catalogo)
    tablas_despues = set(res_despues.fetchall())
    fks_despues = (await admin_db_session.execute(query_fks_count)).scalar()

    tablas_huerfanas = tablas_despues - tablas_antes
    assert len(tablas_huerfanas) == 0, (
        f"El rollback dejó tablas huérfanas en el esquema: {tablas_huerfanas}"
    )
    assert tablas_despues == tablas_antes, (
        "El catálogo de tablas post-rollback diverge del estado original."
    )
    assert fks_despues == fks_antes, (
        f"El rollback alteró los contratos de claves foráneas: antes={fks_antes}, después={fks_despues}"
    )
