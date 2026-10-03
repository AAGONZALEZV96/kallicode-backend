"""UNIQUE (tenant_id, id) en las tablas padre (KC-DB-03-1, prepara DB-F01).

Requiere db/migrations/012_unicidad_tenant_id.sql aplicada. Las relaciones se
leen del catálogo (pg_constraint), igual que V21: una tabla padre nueva sin su
unicidad hace fallar estas pruebas hasta que la tenga.
"""

from __future__ import annotations

import pytest
from kallicode_core import db
from sqlalchemy import text

pytestmark = pytest.mark.integracion

TABLAS_PADRE = (
    "tickets",
    "users",
    "jobs",
    "specs",
    "qa_runs",
    "connections",
    "onboarding_documents",
)

# Padres de las FK escalares entre tablas con tenant_id (las que KC-DB-03-2 hará
# compuestas) y si ya tienen una unicidad exactamente sobre tenant_id + columnas.
_PADRES_SIN_UNICIDAD = """
WITH fk AS (
    SELECT c.confrelid,
           array_agg(a.attname::text ORDER BY a.attname) AS cols
      FROM pg_constraint c
      JOIN LATERAL unnest(c.confkey) AS k(n) ON true
      JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n
     WHERE c.contype = 'f'
       AND EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid = c.conrelid AND attname = 'tenant_id' AND NOT attisdropped)
       AND EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid = c.confrelid AND attname = 'tenant_id' AND NOT attisdropped)
       AND NOT EXISTS (SELECT 1 FROM unnest(c.conkey) AS h(n)
                         JOIN pg_attribute ha ON ha.attrelid = c.conrelid AND ha.attnum = h.n
                        WHERE ha.attname = 'tenant_id')
     GROUP BY c.oid, c.confrelid
)
SELECT DISTINCT fk.confrelid::regclass::text AS padre, fk.cols
  FROM fk
 WHERE NOT EXISTS (
        SELECT 1
          FROM pg_constraint u
         WHERE u.conrelid = fk.confrelid
           AND u.contype IN ('u', 'p')
           AND (SELECT array_agg(a.attname::text ORDER BY a.attname)
                  FROM unnest(u.conkey) AS k(n)
                  JOIN pg_attribute a ON a.attrelid = u.conrelid AND a.attnum = k.n)
               = (SELECT array_agg(x ORDER BY x) FROM unnest(fk.cols || 'tenant_id'::text) AS x)
       )
 ORDER BY 1
"""


async def test_cada_padre_de_una_fk_escalar_tiene_unicidad_con_tenant() -> None:
    async with db.sesion_sistema() as admin:
        faltan = await db.todos(admin, _PADRES_SIN_UNICIDAD)
    assert faltan == [], (
        "tablas padre sin UNIQUE (tenant_id, ...): KC-DB-03-2 no podrá crear sus FK "
        f"compuestas -> {faltan}"
    )


async def test_los_indices_de_unicidad_son_validos() -> None:
    """Un CREATE INDEX CONCURRENTLY interrumpido deja un índice inválido."""
    async with db.sesion_sistema() as admin:
        filas = await db.todos(
            admin,
            "SELECT c.relname AS indice, i.indisvalid AS valido "
            "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
            "WHERE c.relname = ANY(:nombres) ORDER BY 1",
            {"nombres": [f"{t}_tenant_id_uq" for t in TABLAS_PADRE]},
        )
    assert len(filas) == len(TABLAS_PADRE), "falta algún índice: aplica la migración 012"
    assert all(f["valido"] for f in filas), filas


async def test_la_fk_compuesta_de_kc_db_03_2_ya_es_posible() -> None:
    """Sin 012, PostgreSQL responde 'there is no unique constraint matching given keys'."""
    maker = await db._maker_admin()
    async with maker() as sesion:
        tx = await sesion.begin()
        try:
            await sesion.execute(
                text(
                    "ALTER TABLE core.specs ADD CONSTRAINT prueba_fk_compuesta "
                    "FOREIGN KEY (tenant_id, ticket_id) "
                    "REFERENCES core.tickets (tenant_id, id) NOT VALID"
                )
            )
        finally:
            await tx.rollback()
