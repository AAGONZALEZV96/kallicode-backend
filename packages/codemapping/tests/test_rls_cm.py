"""Suite de no-fuga RLS para las 11 tablas de cm (KC-ONB-03-4, CM-F01).

Verifica, tabla por tabla, que un tenant no puede leer, actualizar ni borrar
filas de otro tenant, y que no puede insertar una fila fingiendo ser otro
tenant. Siempre conectando como el rol de aplicación (kallicode_app), nunca
como superusuario -- un superusuario pasa por encima de RLS sin importar
FORCE ROW LEVEL SECURITY (ver db/migrations/002_esquema_cm.sql).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, OperationalError

PLATFORM_URL = "postgresql+psycopg://postgres:dev@localhost:5432/kallicode"
TENANT_A = "org_test_rls_a"
TENANT_B = "org_test_rls_b"

TABLAS_CM = (
    "repo_snapshots",
    "files",
    "symbols",
    "db_tables",
    "db_columns",
    "definitions",
    "concepts",
    "edges",
    "curation_queue",
    "nuggets",
    "incidents",
)


def _db_disponible() -> bool:
    try:
        create_engine(PLATFORM_URL).connect().close()
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_disponible(), reason="Postgres no disponible (make up && make db)")


def _limpiar(tenant_id: str) -> None:
    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        for tabla in reversed(TABLAS_CM):
            db.execute(text(f"DELETE FROM cm.{tabla} WHERE tenant_id = :t"), {"t": tenant_id})
        db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant_id})


@pytest.fixture
def dos_tenants() -> Iterator[tuple[str, str]]:
    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        for t in (TENANT_A, TENANT_B):
            db.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n) ON CONFLICT DO NOTHING"),
                {"id": t, "n": f"tenant de prueba {t}"},
            )
    yield TENANT_A, TENANT_B
    _limpiar(TENANT_A)
    _limpiar(TENANT_B)


def _sembrar_una_fila_por_tabla(tenant_id: str) -> None:
    """Inserta una fila válida (respeta FKs y CHECKs) en cada una de las 11
    tablas de cm, como kallicode_app confinado a tenant_id -- el WITH CHECK
    de la política exige que tenant_id de la fila coincida con la sesión."""
    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        db.execute(text("SET LOCAL ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})

        db.execute(
            text(
                "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) VALUES ('snap_rls', :t, 'sha', 'main')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
                "VALUES ('file_rls', :t, 'a.py', 'python', 'x', 'snap_rls')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) "
                "VALUES ('sym_rls', :t, 'file_rls', 'function', 'f', 1, 2, 'def f()')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre) VALUES ('dbt_rls', :t, 'public', 'invoices')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.db_columns (id, tenant_id, table_id, nombre, tipo, nullable) "
                "VALUES ('dbc_rls', :t, 'dbt_rls', 'id', 'text', false)"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion) "
                "VALUES ('def_rls', :t, 'm', 'n', 'k', 'd')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion) "
                "VALUES ('con_rls', :t, 'C', 'C', 'd')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, props) "
                "VALUES (:t, 'DEFINES', 'file', 'file_rls', 'symbol', 'sym_rls', '{}')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload) VALUES ('cq_rls', :t, 'concepto', '{}')"
            ),
            {"t": tenant_id},
        )
        db.execute(
            text("INSERT INTO cm.nuggets (id, tenant_id, regla, evidencia_ref) VALUES ('ng_rls', :t, 'r', 'ref')"),
            {"t": tenant_id},
        )
        db.execute(
            text("INSERT INTO cm.incidents (id, tenant_id, resumen) VALUES ('inc_rls', :t, 'r')"),
            {"t": tenant_id},
        )


def test_cada_tenant_ve_su_propia_fila(dos_tenants: tuple[str, str]) -> None:
    """Control: antes de probar que no hay fuga, confirma que sembrar y leer
    dentro del MISMO tenant funciona -- si esto fallara, un "0 filas" en el
    test de fuga no probaría nada."""
    tenant_a, _ = dos_tenants
    _sembrar_una_fila_por_tabla(tenant_a)

    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        db.execute(text("SET LOCAL ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_a})
        for tabla in TABLAS_CM:
            visibles = db.execute(text(f"SELECT count(*) FROM cm.{tabla}")).scalar_one()
            assert visibles == 1, f"cm.{tabla}: el propio tenant no ve la fila que sembró"


def test_no_fuga_de_lectura_en_las_11_tablas(dos_tenants: tuple[str, str]) -> None:
    """CM-F01: el tenant B no debe ver ninguna fila del tenant A, en ninguna
    de las 11 tablas de cm."""
    tenant_a, tenant_b = dos_tenants
    _sembrar_una_fila_por_tabla(tenant_a)

    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        db.execute(text("SET LOCAL ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
        for tabla in TABLAS_CM:
            visibles = db.execute(text(f"SELECT count(*) FROM cm.{tabla}")).scalar_one()
            assert visibles == 0, f"cm.{tabla}: el tenant B ve filas del tenant A"


def test_no_fuga_de_escritura_en_las_11_tablas(dos_tenants: tuple[str, str]) -> None:
    """El tenant B no puede modificar ni borrar filas del tenant A -- el
    UPDATE/DELETE sin WHERE explícito queda acotado por la política RLS a
    "lo que ve", que para el tenant B es nada."""
    tenant_a, tenant_b = dos_tenants
    _sembrar_una_fila_por_tabla(tenant_a)

    engine = create_engine(PLATFORM_URL)
    with engine.begin() as db:
        db.execute(text("SET LOCAL ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
        for tabla in TABLAS_CM:
            actualizadas = db.execute(text(f"UPDATE cm.{tabla} SET tenant_id = tenant_id")).rowcount
            assert actualizadas == 0, f"cm.{tabla}: el UPDATE del tenant B afectó filas del tenant A"
            borradas = db.execute(text(f"DELETE FROM cm.{tabla}")).rowcount
            assert borradas == 0, f"cm.{tabla}: el DELETE del tenant B afectó filas del tenant A"

    # La fila del tenant A sigue intacta después de los intentos del tenant B.
    with engine.begin() as db:
        db.execute(text("SET LOCAL ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_a})
        for tabla in TABLAS_CM:
            visibles = db.execute(text(f"SELECT count(*) FROM cm.{tabla}")).scalar_one()
            assert visibles == 1, f"cm.{tabla}: la fila del tenant A no sobrevivió"


def test_insert_suplantando_otro_tenant_lo_bloquea_with_check(dos_tenants: tuple[str, str]) -> None:
    """El tenant B no puede insertar una fila declarando tenant_id = A -- el
    WITH CHECK de la política lo rechaza. Un solo caso representativo
    (cm.concepts, sin FKs propias) alcanza: la política es idéntica en las
    11 tablas, generada por el mismo bucle en la migración."""
    tenant_a, tenant_b = dos_tenants

    engine = create_engine(PLATFORM_URL)
    with pytest.raises(DBAPIError, match="row-level security"), engine.begin() as db:
        db.execute(text("SET LOCAL ROLE kallicode_app"))
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_b})
        db.execute(
            text(
                "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion) "
                "VALUES ('con_forjado', :otro, 'X', 'X', 'x')"
            ),
            {"otro": tenant_a},
        )
