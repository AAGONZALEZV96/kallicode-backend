"""
Ticket: KC-QA-B3-12
Módulo de Registro de Flujos de Prueba en el Grafo (flow_registry.py).
Registra de forma atómica e idempotente en PostgreSQL 16 local las relaciones
entre las pruebas autogeneradas (TestFlow) y los módulos o símbolos que ejercitan (COVERS),
utilizando las tablas cm.test_flows y cm.test_flow_covers para regresión dirigida por impacto.
"""

from __future__ import annotations

from typing import Literal, cast

import structlog.contextvars
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_testing.logger import get_logger

logger = get_logger()

TestFlowKind = Literal["unit", "api", "e2e"]


class TestFlowDTO(BaseModel):
    """Representa un flujo de prueba registrado en cm.test_flows y sus coberturas."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    id: str  # Formato: {criterion_id}:{kind}
    tenant_id: str
    spec_id: str
    criterion_id: str
    kind: TestFlowKind
    path: str
    modules: list[str] = Field(default_factory=list)


class FlowRegistry:
    """
    Gestor de registro atómico e idempotente de flujos de prueba (TestFlow)
    y aristas de cobertura (COVERS) en cm.test_flows y cm.test_flow_covers.
    Garantiza aislamiento multi-tenant con RLS y soporte para regresión dirigida por impacto.
    """

    def __init__(self, db_session: AsyncSession | None = None) -> None:
        self._db: AsyncSession | None = db_session

    def _get_session(self, session_override: AsyncSession | None = None) -> AsyncSession:
        """Obtiene la sesión activa o lanza ValueError si no está configurada."""
        session = session_override or self._db
        if session is None:
            raise ValueError("AsyncSession no configurada en FlowRegistry.")
        return session

    async def _ensure_tenant_context(self, tenant_id: str, session: AsyncSession) -> None:
        """Fija el tenant_id en contextvars y en la variable de sesión RLS de PostgreSQL."""
        cleaned_tenant = tenant_id.strip()
        if not cleaned_tenant:
            raise ValueError("tenant_id no puede ser vacío para FlowRegistry.")
        structlog.contextvars.bind_contextvars(tenant_id=cleaned_tenant)
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": cleaned_tenant},
        )

    async def register_test_flow(
        self,
        tenant_id: str,
        spec_id: str,
        criterion_id: str,
        kind: str,
        file_path: str,
        modules: list[str],
        db_session: AsyncSession | None = None,
    ) -> TestFlowDTO:
        """
        Registra o actualiza de forma atómica e idempotente un TestFlow en cm.test_flows
        y sincroniza sus coberturas en cm.test_flow_covers eliminando relaciones obsoletas.
        """
        session = self._get_session(db_session)
        cleaned_tenant = tenant_id.strip()
        cleaned_spec = spec_id.strip()
        cleaned_criterion = criterion_id.strip()
        cleaned_path = file_path.strip()

        if kind not in ("unit", "api", "e2e"):
            raise ValueError(
                f"kind '{kind}' inválido para TestFlow. Debe ser 'unit', 'api' o 'e2e'."
            )
        typed_kind = cast(TestFlowKind, kind)

        flow_id = f"{cleaned_criterion}:{typed_kind}"
        cleaned_modules = sorted({m.strip() for m in modules if m.strip()})

        await self._ensure_tenant_context(cleaned_tenant, session)

        # 1. Upsert idempotente en cm.test_flows
        upsert_flow_sql = text("""
            INSERT INTO cm.test_flows (
                id, tenant_id, spec_id, criterion_id, kind, path, actualizado_en
            ) VALUES (
                :id, :tenant_id, :spec_id, :criterion_id, :kind, :path, now()
            )
            ON CONFLICT (tenant_id, id) DO UPDATE SET
                spec_id = EXCLUDED.spec_id,
                criterion_id = EXCLUDED.criterion_id,
                kind = EXCLUDED.kind,
                path = EXCLUDED.path,
                actualizado_en = now()
        """)
        await session.execute(
            upsert_flow_sql,
            {
                "id": flow_id,
                "tenant_id": cleaned_tenant,
                "spec_id": cleaned_spec,
                "criterion_id": cleaned_criterion,
                "kind": typed_kind,
                "path": cleaned_path,
            },
        )

        # 2. Gestión transaccional de coberturas: purgar coberturas obsoletas
        delete_covers_sql = text("""
            DELETE FROM cm.test_flow_covers
            WHERE tenant_id = :tenant_id
              AND test_flow_id = :flow_id
        """)
        await session.execute(
            delete_covers_sql,
            {
                "tenant_id": cleaned_tenant,
                "flow_id": flow_id,
            },
        )

        # 3. Insertar la nueva lista de coberturas
        if cleaned_modules:
            insert_cover_sql = text("""
                INSERT INTO cm.test_flow_covers (
                    tenant_id, test_flow_id, module_path
                ) VALUES (
                    :tenant_id, :flow_id, :module_path
                )
                ON CONFLICT (tenant_id, test_flow_id, module_path) DO NOTHING
            """)
            for mod in cleaned_modules:
                await session.execute(
                    insert_cover_sql,
                    {
                        "tenant_id": cleaned_tenant,
                        "flow_id": flow_id,
                        "module_path": mod,
                    },
                )

        logger.info(
            "flow_registry.flow_registered",
            flow_id=flow_id,
            tenant_id=cleaned_tenant,
            spec_id=cleaned_spec,
            modules_count=len(cleaned_modules),
        )

        return TestFlowDTO(
            id=flow_id,
            tenant_id=cleaned_tenant,
            spec_id=cleaned_spec,
            criterion_id=cleaned_criterion,
            kind=typed_kind,
            path=cleaned_path,
            modules=cleaned_modules,
        )

    async def get_flows_for_modules(
        self,
        tenant_id: str,
        modules: list[str],
        db_session: AsyncSession | None = None,
    ) -> list[TestFlowDTO]:
        """
        Consulta cm.test_flows uniendo con cm.test_flow_covers para regresión dirigida por impacto,
        filtrando estrictamente por tenant_id y módulos afectados.
        """
        if not modules:
            return []

        session = self._get_session(db_session)
        cleaned_tenant = tenant_id.strip()
        cleaned_modules = [m.strip() for m in modules if m.strip()]
        if not cleaned_modules:
            return []

        await self._ensure_tenant_context(cleaned_tenant, session)

        query = text("""
            SELECT f.id,
                   f.tenant_id,
                   f.spec_id,
                   f.criterion_id,
                   f.kind,
                   f.path,
                   COALESCE(array_agg(all_c.module_path ORDER BY all_c.module_path), ARRAY[]::text[]) AS modules
            FROM cm.test_flows f
            JOIN cm.test_flow_covers all_c
              ON all_c.tenant_id = f.tenant_id
             AND all_c.test_flow_id = f.id
            WHERE f.tenant_id = :tenant_id
              AND f.id IN (
                  SELECT sub_c.test_flow_id
                  FROM cm.test_flow_covers sub_c
                  WHERE sub_c.tenant_id = :tenant_id
                    AND sub_c.module_path = ANY(:modules)
              )
            GROUP BY f.id, f.tenant_id, f.spec_id, f.criterion_id, f.kind, f.path
            ORDER BY f.id
        """)

        result = await session.execute(
            query,
            {
                "tenant_id": cleaned_tenant,
                "modules": cleaned_modules,
            },
        )
        rows = result.fetchall()

        flows: list[TestFlowDTO] = []
        for row in rows:
            raw_modules = row[6]
            mod_list: list[str] = (
                list(raw_modules) if isinstance(raw_modules, (list, tuple)) else []
            )
            flows.append(
                TestFlowDTO(
                    id=str(row[0]),
                    tenant_id=str(row[1]),
                    spec_id=str(row[2]),
                    criterion_id=str(row[3]),
                    kind=cast(TestFlowKind, str(row[4])),
                    path=str(row[5]),
                    modules=mod_list,
                )
            )

        return flows

    async def purge_flows_for_spec(
        self,
        tenant_id: str,
        spec_id: str,
        db_session: AsyncSession | None = None,
    ) -> int:
        """
        Elimina atómicamente todos los flujos y sus coberturas asociadas a una especificación
        retirada, garantizando consistencia y devolviendo la cantidad de flujos eliminados.
        """
        session = self._get_session(db_session)
        cleaned_tenant = tenant_id.strip()
        cleaned_spec = spec_id.strip()

        await self._ensure_tenant_context(cleaned_tenant, session)

        purge_sql = text("""
            WITH deleted AS (
                DELETE FROM cm.test_flows
                WHERE tenant_id = :tenant_id
                  AND spec_id = :spec_id
                RETURNING id
            )
            SELECT count(*)::int AS total FROM deleted
        """)

        result = await session.execute(
            purge_sql,
            {
                "tenant_id": cleaned_tenant,
                "spec_id": cleaned_spec,
            },
        )
        count_val = result.scalar()
        total_deleted = int(count_val) if count_val is not None else 0

        logger.info(
            "flow_registry.flows_purged",
            tenant_id=cleaned_tenant,
            spec_id=cleaned_spec,
            deleted_count=total_deleted,
        )

        return total_deleted
