"""
Ticket: KC-QA-B3-12
Suite de pruebas unitarias y de integración para FlowRegistry (flow_registry.py).
Valida:
1. Registro atómico e idempotente de TestFlow en cm.test_flows y cm.test_flow_covers,
   asegurando que re-ejecuciones no dupliquen filas y purguen coberturas obsoletas.
2. Consulta de regresión dirigida por impacto mediante get_flows_for_modules.
3. Purga transaccional y en cascada de flujos asociados a una especificación retirada.
4. Aislamiento multi-tenant estricto mediante RLS (Row Level Security) en PostgreSQL 16.
5. Validación rigurosa de parámetros y tipos permitidos (unit, api, e2e).
"""

from __future__ import annotations

import pytest
from kallicode_testing.flow_registry import FlowRegistry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _seed_organization(db_session: AsyncSession, tenant_id: str) -> None:
    """Inserta la organización del tenant para satisfacer la FK en cm.test_flows."""
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
    await db_session.execute(
        text("""
            INSERT INTO core.organizations (id, nombre)
            VALUES (:tenant_id, 'Organización de Prueba FlowRegistry')
            ON CONFLICT (id) DO NOTHING
        """),
        {"tenant_id": tenant_id},
    )


async def test_registro_flujo_prueba_idempotente(db_session: AsyncSession) -> None:
    """
    Prueba 1: Registrar inicialmente ['apps/api', 'packages/core'] para AC-01.
    Posteriormente reejecutar con ['apps/api'].
    Validaciones:
    - No se generan duplicados en cm.test_flows (exactamente 1 fila).
    - La cobertura antigua 'packages/core' es eliminada automáticamente de cm.test_flow_covers.
    - Se preserva únicamente la cobertura activa 'apps/api'.
    """
    tenant_id = "tenant_flow_reg_01"
    await _seed_organization(db_session, tenant_id)

    registry = FlowRegistry(db_session=db_session)

    # 1. Registro inicial con dos módulos
    flow_v1 = await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-AUTH-01",
        criterion_id="AC-01",
        kind="unit",
        file_path="packages/testing/tests/unit/test_ac01.py",
        modules=["apps/api", "packages/core"],
    )

    assert flow_v1.id == "AC-01:unit"
    assert flow_v1.tenant_id == tenant_id
    assert flow_v1.modules == ["apps/api", "packages/core"]

    # Verificar directamente en la base de datos
    res_flows = await db_session.execute(
        text("SELECT count(*) FROM cm.test_flows WHERE tenant_id = :tenant_id AND id = :id"),
        {"tenant_id": tenant_id, "id": "AC-01:unit"},
    )
    assert res_flows.scalar() == 1

    res_covers = await db_session.execute(
        text(
            "SELECT module_path FROM cm.test_flow_covers WHERE tenant_id = :tenant_id AND test_flow_id = :id ORDER BY module_path"
        ),
        {"tenant_id": tenant_id, "id": "AC-01:unit"},
    )
    covers_v1 = [r[0] for r in res_covers.fetchall()]
    assert covers_v1 == ["apps/api", "packages/core"]

    # 2. Re-ejecución con solo ['apps/api'] (debe eliminar 'packages/core' y no duplicar flow)
    flow_v2 = await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-AUTH-01",
        criterion_id="AC-01",
        kind="unit",
        file_path="packages/testing/tests/unit/test_ac01_updated.py",
        modules=["apps/api"],
    )

    assert flow_v2.id == "AC-01:unit"
    assert flow_v2.path == "packages/testing/tests/unit/test_ac01_updated.py"
    assert flow_v2.modules == ["apps/api"]

    # Verificar que cm.test_flows sigue teniendo exactamente 1 fila
    res_flows_after = await db_session.execute(
        text(
            "SELECT count(*), max(path) FROM cm.test_flows WHERE tenant_id = :tenant_id AND id = :id"
        ),
        {"tenant_id": tenant_id, "id": "AC-01:unit"},
    )
    row_flow = res_flows_after.fetchone()
    assert row_flow is not None
    assert row_flow[0] == 1
    assert row_flow[1] == "packages/testing/tests/unit/test_ac01_updated.py"

    # Verificar que cm.test_flow_covers ahora tiene ÚNICAMENTE ['apps/api']
    res_covers_after = await db_session.execute(
        text(
            "SELECT module_path FROM cm.test_flow_covers WHERE tenant_id = :tenant_id AND test_flow_id = :id ORDER BY module_path"
        ),
        {"tenant_id": tenant_id, "id": "AC-01:unit"},
    )
    covers_v2 = [r[0] for r in res_covers_after.fetchall()]
    assert covers_v2 == ["apps/api"]


async def test_consulta_regresion_dirigida_por_modulos(db_session: AsyncSession) -> None:
    """
    Prueba 2: Registrar tres flujos asociados a distintos módulos:
    - Flow 1 (AC-01:unit): apps/api, packages/common
    - Flow 2 (AC-02:api): packages/core
    - Flow 3 (AC-03:e2e): apps/portal
    Consultar utilizando get_flows_for_modules(...) con un único módulo en el radio de impacto.
    Validaciones:
    - Solo se devuelven los flujos relevantes que ejercitan ese módulo.
    - Soporte para consulta de múltiples módulos simultáneos.
    """
    tenant_id = "tenant_flow_reg_02"
    await _seed_organization(db_session, tenant_id)

    registry = FlowRegistry(db_session=db_session)

    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-01",
        criterion_id="AC-01",
        kind="unit",
        file_path="tests/unit/test_flow_1.py",
        modules=["apps/api", "packages/common"],
    )
    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-01",
        criterion_id="AC-02",
        kind="api",
        file_path="tests/api/test_flow_2.py",
        modules=["packages/core"],
    )
    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-01",
        criterion_id="AC-03",
        kind="e2e",
        file_path="e2e/test_flow_3.spec.ts",
        modules=["apps/portal"],
    )

    # Consulta con impacto en 'apps/api'
    flows_api = await registry.get_flows_for_modules(tenant_id, ["apps/api"])
    assert len(flows_api) == 1
    assert flows_api[0].id == "AC-01:unit"
    assert flows_api[0].kind == "unit"
    assert "apps/api" in flows_api[0].modules
    assert "packages/common" in flows_api[0].modules

    # Consulta con impacto en 'packages/core' y 'apps/portal'
    flows_multi = await registry.get_flows_for_modules(tenant_id, ["packages/core", "apps/portal"])
    assert len(flows_multi) == 2
    flow_ids = {f.id for f in flows_multi}
    assert flow_ids == {"AC-02:api", "AC-03:e2e"}

    # Consulta con módulo no relacionado
    flows_none = await registry.get_flows_for_modules(tenant_id, ["infra/k8s"])
    assert flows_none == []

    # Consulta con lista vacía
    flows_empty = await registry.get_flows_for_modules(tenant_id, [])
    assert flows_empty == []


async def test_purga_transaccional_por_spec(db_session: AsyncSession) -> None:
    """
    Prueba 3: Registrar múltiples flujos para SPEC-ALPHA y SPEC-BETA.
    Ejecutar purge_flows_for_spec(...) sobre SPEC-ALPHA.
    Validaciones:
    - Retorna el conteo exacto de flujos purgados.
    - cm.test_flows y cm.test_flow_covers quedan completamente limpios para SPEC-ALPHA.
    - Los registros de SPEC-BETA permanecen intactos.
    """
    tenant_id = "tenant_flow_reg_03"
    await _seed_organization(db_session, tenant_id)

    registry = FlowRegistry(db_session=db_session)

    # Flujos para SPEC-ALPHA
    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-ALPHA",
        criterion_id="AC-01",
        kind="unit",
        file_path="tests/test_alpha_1.py",
        modules=["apps/api"],
    )
    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-ALPHA",
        criterion_id="AC-02",
        kind="api",
        file_path="tests/test_alpha_2.py",
        modules=["apps/api", "packages/core"],
    )

    # Flujo para SPEC-BETA
    await registry.register_test_flow(
        tenant_id=tenant_id,
        spec_id="SPEC-BETA",
        criterion_id="AC-01",
        kind="e2e",
        file_path="tests/test_beta.spec.ts",
        modules=["apps/portal"],
    )

    # Ejecutar purga de SPEC-ALPHA
    deleted_count = await registry.purge_flows_for_spec(tenant_id=tenant_id, spec_id="SPEC-ALPHA")
    assert deleted_count == 2

    # Verificar que no quedan flujos ni coberturas para SPEC-ALPHA
    res_alpha_flows = await db_session.execute(
        text(
            "SELECT count(*) FROM cm.test_flows WHERE tenant_id = :tenant_id AND spec_id = 'SPEC-ALPHA'"
        ),
        {"tenant_id": tenant_id},
    )
    assert res_alpha_flows.scalar() == 0

    res_alpha_covers = await db_session.execute(
        text("""
            SELECT count(*) FROM cm.test_flow_covers
            WHERE tenant_id = :tenant_id
              AND test_flow_id IN ('AC-01:unit', 'AC-02:api')
        """),
        {"tenant_id": tenant_id},
    )
    assert res_alpha_covers.scalar() == 0

    # Verificar que SPEC-BETA permanece intacto
    res_beta_flows = await db_session.execute(
        text(
            "SELECT count(*) FROM cm.test_flows WHERE tenant_id = :tenant_id AND spec_id = 'SPEC-BETA'"
        ),
        {"tenant_id": tenant_id},
    )
    assert res_beta_flows.scalar() == 1

    flows_beta = await registry.get_flows_for_modules(tenant_id, ["apps/portal"])
    assert len(flows_beta) == 1
    assert flows_beta[0].spec_id == "SPEC-BETA"


async def test_aislamiento_multi_tenant_en_cm_test_flows(db_session: AsyncSession) -> None:
    """
    Prueba 4: Registrar flujos idénticos para Tenant A y Tenant B.
    Consultar únicamente con Tenant A.
    Validaciones:
    - No existe filtración cruzada de datos.
    - Las consultas bajo el contexto de Tenant A no devuelven flujos de Tenant B.
    - Verificación de cumplimiento de políticas RLS en PostgreSQL 16.
    """
    tenant_a = "tenant_alpha_rls_flow"
    tenant_b = "tenant_beta_rls_flow"
    await _seed_organization(db_session, tenant_a)
    await _seed_organization(db_session, tenant_b)

    registry = FlowRegistry(db_session=db_session)

    # Registrar para Tenant A
    await registry.register_test_flow(
        tenant_id=tenant_a,
        spec_id="SPEC-COMMON",
        criterion_id="AC-01",
        kind="unit",
        file_path="tests/tenant_a/test.py",
        modules=["apps/api"],
    )

    # Registrar para Tenant B con el mismo criterion_id pero distinta ruta
    await registry.register_test_flow(
        tenant_id=tenant_b,
        spec_id="SPEC-COMMON",
        criterion_id="AC-01",
        kind="unit",
        file_path="tests/tenant_b/test.py",
        modules=["apps/api"],
    )

    # Consultar desde FlowRegistry para Tenant A
    flows_a = await registry.get_flows_for_modules(tenant_a, ["apps/api"])
    assert len(flows_a) == 1
    assert flows_a[0].tenant_id == tenant_a
    assert flows_a[0].path == "tests/tenant_a/test.py"

    # Consultar desde FlowRegistry para Tenant B
    flows_b = await registry.get_flows_for_modules(tenant_b, ["apps/api"])
    assert len(flows_b) == 1
    assert flows_b[0].tenant_id == tenant_b
    assert flows_b[0].path == "tests/tenant_b/test.py"

    # Verificar RLS a nivel de consulta SQL directa:
    # Bajo app.tenant_id = tenant_a, un SELECT sin filtro WHERE de tenant_id solo ve tenant_a
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_a},
    )
    direct_query_a = await db_session.execute(
        text("SELECT tenant_id, path FROM cm.test_flows WHERE id = 'AC-01:unit'")
    )
    rows_a = direct_query_a.fetchall()
    assert len(rows_a) == 1
    assert rows_a[0][0] == tenant_a
    assert rows_a[0][1] == "tests/tenant_a/test.py"


async def test_validacion_parametros_invalidos(db_session: AsyncSession) -> None:
    """
    Prueba 5: Valida que tipos inválidos de prueba o tenant_id vacíos
    lancen ValueError de forma defensiva antes de interactuar con la base de datos.
    """
    registry = FlowRegistry(db_session=db_session)

    with pytest.raises(ValueError, match="kind 'invalid_kind' inválido"):
        await registry.register_test_flow(
            tenant_id="tenant_err",
            spec_id="SPEC-01",
            criterion_id="AC-01",
            kind="invalid_kind",
            file_path="tests/test.py",
            modules=["apps/api"],
        )

    with pytest.raises(ValueError, match="tenant_id no puede ser vacío"):
        await registry.register_test_flow(
            tenant_id="   ",
            spec_id="SPEC-01",
            criterion_id="AC-01",
            kind="unit",
            file_path="tests/test.py",
            modules=["apps/api"],
        )

    # Registry sin sesión lanza ValueError
    empty_registry = FlowRegistry(db_session=None)
    with pytest.raises(ValueError, match="AsyncSession no configurada"):
        await empty_registry.register_test_flow(
            tenant_id="tenant_err",
            spec_id="SPEC-01",
            criterion_id="AC-01",
            kind="unit",
            file_path="tests/test.py",
            modules=["apps/api"],
        )
