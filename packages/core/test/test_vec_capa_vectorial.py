"""Suite de aislamiento, recall e idempotencia sobre la capa vectorial
(vec.*, KC-ONB-02-5).

Requiere Postgres real con pgvector (make up && make db). Si la base no
está disponible, la suite completa se salta con un motivo explícito en
vez de fallar — así `pytest -q` sigue siendo verde en una máquina sin
`docker compose up` corriendo, y basta con levantar la infra para que
esta suite se active sola.

Tres frentes, uno por indicador del ticket:
  * Aislamiento : RLS impide leer filas de vec.* de un tenant ajeno.
  * Recall      : la búsqueda por similitud coseno devuelve primero el
                   vecino más cercano real, no uno arbitrario.
  * Idempotencia: reinsertar el mismo contenido no crea filas duplicadas
                   ni reescribe la fila si el texto_hash no cambió.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from kallicode_core.config import get_settings
from kallicode_core.vectores import upsert_definition_embedding
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------
# Motores de ESTE archivo.
#
# pytest-anyio crea un event loop nuevo por fixture/test. Un pool de
# conexiones asyncpg queda atado al loop donde se abrió la conexión, y al
# reutilizarla en otro loop falla con "attached to a different loop".
# Solución: NullPool => cada sesión abre y cierra su propia conexión dentro
# del loop actual; no hay pool, no hay ping, no hay nada que cruce loops.
#
# Dos conexiones distintas:
#   * kallicode_dev  : rol SIN BYPASSRLS, para las partes sensibles a RLS
#                      (db/seeds/dev_role.sql). Si no, un superusuario pasa
#                      cualquier assert de aislamiento sin que RLS intervenga.
#   * admin (URL base): con bypass, solo para crear/borrar organizaciones y
#                      nodos de prueba (ver core.organizations en 04_rls.sql).
# --------------------------------------------------------------------------
def _url_kallicode_dev() -> str:
    url = make_url(get_settings().database_url).set(username="kallicode_dev")
    return url.render_as_string(hide_password=False)


def _url_kallicode_admin() -> str:
    database_url_admin = get_settings().database_url_admin
    if database_url_admin is None:
        raise RuntimeError("KC_DATABASE_URL_ADMIN no está configurada")
    return database_url_admin


def _crear_maker(
    url: str,
) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    engine = create_async_engine(url, poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False), engine


@asynccontextmanager
async def sesion_tenant_real(tenant_id: str) -> AsyncIterator[AsyncSession]:
    """Como kallicode_core.db.sesion_tenant, pero conectando como
    kallicode_dev (sin BYPASSRLS). Ver nota arriba."""
    maker, engine = _crear_maker(_url_kallicode_dev())
    try:
        async with maker() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": tenant_id},
            )
            yield db
    finally:
        await engine.dispose()


@asynccontextmanager
async def sesion_admin_local() -> AsyncIterator[AsyncSession]:
    """Reemplazo local de sesion_sistema() para este archivo (bypass RLS)."""
    maker, engine = _crear_maker(_url_kallicode_admin())
    try:
        async with maker() as db, db.begin():
            yield db
    finally:
        await engine.dispose()


def _vector(seed: float, dim: int = 1024) -> list[float]:
    """Vector determinista y simple, para aislamiento/idempotencia donde
    solo importa la presencia/igualdad de la fila, no su dirección."""
    v = [seed] * dim
    v[0] = seed + 1.0
    return v


def _vector_dir(peso_a: float, peso_b: float, dim: int = 1024) -> list[float]:
    """Vector con dirección controlada en 2 ejes ortogonales (dim 0 y 1),
    el resto en cero. Para el test de recall, donde SÍ importa el ángulo."""
    v = [0.0] * dim
    v[0] = peso_a
    v[1] = peso_b
    return v


# (nodo_ref, kind en cm.nodes) que usan las pruebas de esta suite.
_NODOS_DE_PRUEBA = (
    ("checkout.validate_order", "symbol"),
    ("checkout.compute_tax", "symbol"),
    ("billing.send_invoice_email", "symbol"),
    ("orders.Order", "definition"),
)


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()[:32]


@pytest.fixture
async def db_disponible():
    """Verifica que Postgres esté arriba Y que exista el rol kallicode_dev
    (db/seeds/dev_role.sql); si falta cualquiera de las dos, salta la suite
    con un motivo explícito en vez de fallar."""
    try:
        async with sesion_admin_local() as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Postgres no disponible — levantar con 'make up && make db' ({exc!r})")

    try:
        async with sesion_tenant_real("org_smoke_test_inexistente") as db:
            await db.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(
            f"Falta el rol kallicode_dev — corre db/seeds/dev_role.sql contra tu base local ({exc!r})"
        )


@pytest.fixture
async def dos_tenants(db_disponible):
    """Crea dos organizaciones efímeras para probar aislamiento cruzado."""
    tenant_a = f"org_test_{uuid.uuid4().hex[:10]}"
    tenant_b = f"org_test_{uuid.uuid4().hex[:10]}"
    async with sesion_admin_local() as db:
        for tid in (tenant_a, tenant_b):
            await db.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
                {"id": tid, "n": f"Tenant de prueba {tid}"},
            )
        # nodo_ref es FK compuesta a cm.nodes (KC-DB-04-4): los nodos referenciados
        # por las pruebas deben existir antes que su embedding.
        for tid in (tenant_a, tenant_b):
            for nodo_ref, kind in _NODOS_DE_PRUEBA:
                await db.execute(
                    text("INSERT INTO cm.nodes (tenant_id, id, kind) VALUES (:t, :i, :k)"),
                    {"t": tid, "i": nodo_ref, "k": kind},
                )
    try:
        yield tenant_a, tenant_b
    finally:
        async with sesion_admin_local() as db:
            await db.execute(
                text("DELETE FROM vec.definition_embeddings WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            await db.execute(
                text("DELETE FROM cm.nodes WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            await db.execute(
                text("DELETE FROM core.organizations WHERE id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )


# --------------------------------------------------------------------------
# 1. AISLAMIENTO
# --------------------------------------------------------------------------
class TestAislamiento:
    """RLS confina cada sesión a su propio tenant_id (04_rls.sql)."""

    async def test_tenant_no_ve_definiciones_de_otro_tenant(self, dos_tenants):
        tenant_a, tenant_b = dos_tenants

        async with sesion_tenant_real(tenant_a) as db:
            await upsert_definition_embedding(
                db,
                tenant_id=tenant_a,
                nodo_ref="checkout.validate_order",
                capa="symbol",
                nombre="validateOrder",
                descripcion="Valida el pedido antes de cobrar",
                embedding=_vector(0.1),
                texto_hash=_hash("v1"),
            )

        # tenant_b, en su propia sesión confinada por RLS, no debe ver nada
        async with sesion_tenant_real(tenant_b) as db:
            fila = await db.execute(
                text("SELECT count(*) FROM vec.definition_embeddings WHERE nodo_ref = :n"),
                {"n": "checkout.validate_order"},
            )
            assert fila.scalar() == 0, "RLS no debería dejar ver filas de otro tenant"

        # tenant_a sí ve su propia fila
        async with sesion_tenant_real(tenant_a) as db:
            fila = await db.execute(
                text("SELECT count(*) FROM vec.definition_embeddings WHERE nodo_ref = :n"),
                {"n": "checkout.validate_order"},
            )
            assert fila.scalar() == 1

    async def test_tenant_no_puede_escribir_con_tenant_id_ajeno(self, dos_tenants):
        """WITH CHECK de la política debe rechazar filas con tenant_id ajeno,
        aunque la sesión intente forzarlo explícitamente en el INSERT."""
        tenant_a, tenant_b = dos_tenants

        # Acotado a DBAPIError + mensaje de RLS: antes `Exception` se tragaba
        # cualquier fallo (incluido el error de event loop) y daba falso verde.
        with pytest.raises(DBAPIError, match="row-level security"):
            async with sesion_tenant_real(tenant_a) as db:
                await db.execute(
                    text("""
                        INSERT INTO vec.definition_embeddings
                            (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash)
                        VALUES ('de_forzado', :ajeno, 'x', 'symbol', 'x', 'x',
                                CAST(:e AS vector), 'h')
                        """),
                    {"ajeno": tenant_b, "e": str(_vector(0.2))},
                )


# --------------------------------------------------------------------------
# 2. RECALL
# --------------------------------------------------------------------------
class TestRecall:
    """La búsqueda por distancia coseno (<=>) devuelve primero el vecino
    semánticamente más cercano, no uno cualquiera de los candidatos."""

    async def test_orden_por_similitud_coseno_es_correcto(self, dos_tenants):
        tenant_a, _ = dos_tenants

        # Dos ejes ortogonales: eje "checkout" (dim 0) y eje "billing" (dim 1).
        # La consulta apunta casi puro al eje checkout (1.00, 0.05).
        candidatos = [
            ("checkout.validate_order", _vector_dir(0.98, 0.05)),  # casi igual a la consulta
            ("checkout.compute_tax", _vector_dir(0.90, 0.30)),  # mismo eje, más inclinado
            ("billing.send_invoice_email", _vector_dir(0.05, 0.98)),  # eje opuesto
        ]
        async with sesion_tenant_real(tenant_a) as db:
            for nodo_ref, vec in candidatos:
                await upsert_definition_embedding(
                    db,
                    tenant_id=tenant_a,
                    nodo_ref=nodo_ref,
                    capa="symbol",
                    nombre=nodo_ref,
                    descripcion=nodo_ref,
                    embedding=vec,
                    texto_hash=_hash(nodo_ref),
                )

            consulta = _vector_dir(1.00, 0.05)
            filas = await db.execute(
                text("""
                    SELECT nodo_ref
                    FROM vec.definition_embeddings
                    ORDER BY embedding <=> CAST(:q AS vector)
                    LIMIT 3
                    """),
                {"q": str(consulta)},
            )
            orden = [r[0] for r in filas.all()]

        assert orden[0] == "checkout.validate_order"
        assert orden[1] == "checkout.compute_tax"
        assert orden[2] == "billing.send_invoice_email"


# --------------------------------------------------------------------------
# 3. IDEMPOTENCIA
# --------------------------------------------------------------------------
class TestIdempotencia:
    """Reinsertar el mismo contenido no duplica filas ni reescribe cuando
    el texto_hash no cambió; sí actualiza cuando el contenido cambia."""

    async def test_reinsertar_mismo_contenido_no_duplica(self, dos_tenants):
        tenant_a, _ = dos_tenants
        hash_v1 = _hash("contenido v1")

        async with sesion_tenant_real(tenant_a) as db:
            escribio_1 = await upsert_definition_embedding(
                db,
                tenant_id=tenant_a,
                nodo_ref="orders.Order",
                capa="definition",
                nombre="Order",
                descripcion="Entidad orden",
                embedding=_vector(0.3),
                texto_hash=hash_v1,
            )
            escribio_2 = await upsert_definition_embedding(
                db,
                tenant_id=tenant_a,
                nodo_ref="orders.Order",
                capa="definition",
                nombre="Order",
                descripcion="Entidad orden",
                embedding=_vector(0.3),
                texto_hash=hash_v1,
            )
            total = await db.execute(
                text("SELECT count(*) FROM vec.definition_embeddings WHERE nodo_ref = :n"),
                {"n": "orders.Order"},
            )

        assert escribio_1 is True, "la primera escritura sí debe ejecutarse"
        assert escribio_2 is False, "texto_hash sin cambios: no debe reescribir"
        assert total.scalar() == 1, "no debe haber filas duplicadas para el mismo nodo_ref"

    async def test_contenido_distinto_si_actualiza(self, dos_tenants):
        tenant_a, _ = dos_tenants

        async with sesion_tenant_real(tenant_a) as db:
            await upsert_definition_embedding(
                db,
                tenant_id=tenant_a,
                nodo_ref="orders.Order",
                capa="definition",
                nombre="Order",
                descripcion="Entidad orden v1",
                embedding=_vector(0.3),
                texto_hash=_hash("v1"),
            )
            escribio = await upsert_definition_embedding(
                db,
                tenant_id=tenant_a,
                nodo_ref="orders.Order",
                capa="definition",
                nombre="Order",
                descripcion="Entidad orden v2",
                embedding=_vector(0.4),
                texto_hash=_hash("v2"),
            )
            fila = await db.execute(
                text("SELECT descripcion FROM vec.definition_embeddings WHERE nodo_ref = :n"),
                {"n": "orders.Order"},
            )

        assert escribio is True, "contenido distinto: sí debe reescribir"
        assert fila.scalar() == "Entidad orden v2"
