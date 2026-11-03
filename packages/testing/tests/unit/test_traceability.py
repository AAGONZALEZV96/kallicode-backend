"""
Ticket: KC-QA-B3-05
Suite de pruebas unitarias y adversarias para TraceabilityMapper (traceability.py).
Verifica:
1. Extracción estática AST/Regex de criterios BDD ('AC-n') en Python (.py) y Playwright (.spec.ts).
2. Reconciliación exitosa cuando todos los criterios de la especificación están cubiertos.
3. Reconciliación fallida por criterios faltantes y bloqueo Gate 2 con 422 MATRIZ_INCOMPLETA.
4. Aislamiento estricto multi-tenant en memoria y sobre core.spec_criteria con RLS en PostgreSQL 16.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kallicode_testing.traceability import (
    IncompleteTraceabilityMatrixError,
    TenantTraceabilityIsolationError,
    TraceabilityMapper,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_extraccion_criterios_python_y_playwright(tmp_path: Path) -> None:
    """
    Prueba 1: Verifica la extracción exacta de IDs de criterios ('AC-n') desde:
    - Archivos Python (.py) mediante ast.parse (@criterio("AC-01"), @pytest.mark.criterio("AC-02")).
    - Archivos Playwright (.spec.ts) mediante annotations: [...] y títulos test("AC-n: ...").
    """
    mapper = TraceabilityMapper()

    py_file = tmp_path / "test_auth_generated.py"
    py_file.write_text(
        "import pytest\n"
        "from kallicode_testing.gen_tests import criterio\n\n"
        '@criterio("AC-01")\n'
        "def test_login_exitoso() -> None:\n"
        "    assert True\n\n"
        '@pytest.mark.criterio("AC-02")\n'
        "async def test_mfa_requerido() -> None:\n"
        "    assert 1 == 1\n",
        encoding="utf-8",
    )

    ts_file = tmp_path / "gate_approval.spec.ts"
    ts_file.write_text(
        'import { test, expect } from "@playwright/test";\n\n'
        'test("aprobacion gate 1", {\n'
        "  annotations: [\n"
        "    {\n"
        '      type: "criterion",\n'
        '      description: "AC-03"\n'
        "    }\n"
        "  ]\n"
        "}, async ({ page }) => {\n"
        '  await page.goto("/gates");\n'
        "});\n\n"
        'test("AC-04: rechazo con motivo obligatorio", async ({ page }) => {\n'
        '  await page.goto("/gates/reject");\n'
        "});\n",
        encoding="utf-8",
    )

    py_criteria = mapper.extract_criteria_from_file(py_file, test_kind="unit")
    assert py_criteria == ["AC-01", "AC-02"]

    api_criteria = mapper.extract_criteria_from_file(py_file, test_kind="api")
    assert api_criteria == ["AC-01", "AC-02"]

    e2e_criteria = mapper.extract_criteria_from_file(ts_file, test_kind="e2e")
    assert e2e_criteria == ["AC-03", "AC-04"]

    # Verificar errores en archivo inexistente o test_kind inválido
    with pytest.raises(FileNotFoundError):
        mapper.extract_criteria_from_file(tmp_path / "no_existe.py", test_kind="unit")

    with pytest.raises(ValueError, match="test_kind no soportado"):
        mapper.extract_criteria_from_file(py_file, test_kind="invalid_kind")


async def test_reconciliacion_cobertura_spec_completa_exitoso(tmp_path: Path) -> None:
    """
    Prueba 2: Simula una especificación con AC-01, AC-02 y AC-03 y reportes ejecutados
    que cubren los tres criterios, verificando is_complete is True y exportación JSON.
    """
    mapper = TraceabilityMapper()
    tenant_id = "tenant_qa_b3_05"
    spec_id = "spec_gate1_001"

    rec1 = mapper.register_mapping(
        tenant_id=tenant_id,
        spec_id=spec_id,
        criterion_id="AC-01",
        test_path="tests/unit/test_ac01.py",
        test_kind="unit",
        node_id="sym_login",
    )
    mapper.register_mapping(
        tenant_id=tenant_id,
        spec_id=spec_id,
        criterion_id="AC-02",
        test_path="tests/api/test_ac02.py",
        test_kind="api",
        node_id="sym_token",
    )
    mapper.register_mapping(
        tenant_id=tenant_id,
        spec_id=spec_id,
        criterion_id="AC-03",
        test_path="e2e/portal/ac03.spec.ts",
        test_kind="e2e",
        node_id="sym_portal_gate",
    )

    assert rec1.criterion_id == "AC-01"
    assert len(mapper.get_mappings_for_spec(tenant_id, spec_id)) == 3
    assert len(mapper.get_tests_for_criterion(tenant_id, "AC-01")) == 1
    assert len(mapper.get_criteria_for_test_path(tenant_id, "tests/unit/test_ac01.py")) == 1

    spec_criteria_ids = ["AC-01", "AC-02", "AC-03"]
    executed_reports = [
        {"criterion_id": "AC-01", "status": "passed"},
        {"criterio_id": "AC-02", "resultado": "pasa"},
        {
            "status": "passed",
            "annotations": [{"type": "criterion", "description": "AC-03"}],
        },
    ]

    is_complete, missing_criteria_ids, extra_unmapped_ids = mapper.reconcile_spec_coverage(
        spec_criteria_ids=spec_criteria_ids,
        executed_reports=executed_reports,
    )

    assert is_complete is True
    assert missing_criteria_ids == []
    assert extra_unmapped_ids == []

    # Gate 2 debe aprobar sin lanzar excepción
    gate2_ok, gate2_missing, _ = mapper.enforce_gate2_coverage(
        spec_criteria_ids=spec_criteria_ids,
        executed_reports=executed_reports,
    )
    assert gate2_ok is True
    assert gate2_missing == []

    # Verificar exportación segura a JSON
    json_path = mapper.export_json(tenant_id, spec_id, tmp_path, "traceability_map.json")
    exported_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported_data["tenant_id"] == tenant_id
    assert len(exported_data["mappings"]) == 3


async def test_reconciliacion_falla_por_criterios_faltantes() -> None:
    """
    Prueba 3: Simula una especificación con AC-01..AC-05 y reportes que solo cubren
    AC-01 y AC-02. Verifica is_complete is False, missing_criteria_ids == ["AC-03", "AC-04", "AC-05"]
    y el bloqueo duro 422 MATRIZ_INCOMPLETA al validar Gate 2.
    """
    mapper = TraceabilityMapper()
    spec_criteria_ids = ["AC-01", "AC-02", "AC-03", "AC-04", "AC-05"]
    executed_reports = [
        {"criterion_id": "AC-01", "status": "passed"},
        {"criterion_id": "AC-02", "status": "passed"},
        {"criterion_id": "AC-03", "status": "failed"},
    ]

    is_complete, missing_criteria_ids, extra_unmapped_ids = mapper.reconcile_spec_coverage(
        spec_criteria_ids=spec_criteria_ids,
        executed_reports=executed_reports,
    )

    assert is_complete is False
    assert missing_criteria_ids == ["AC-03", "AC-04", "AC-05"]
    assert extra_unmapped_ids == []

    # Al exigir cobertura para Gate 2 debe lanzar 422 MATRIZ_INCOMPLETA
    with pytest.raises(IncompleteTraceabilityMatrixError) as exc_info:
        mapper.enforce_gate2_coverage(
            spec_criteria_ids=spec_criteria_ids,
            executed_reports=executed_reports,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == "MATRIZ_INCOMPLETA"
    assert exc_info.value.missing_criteria_ids == ["AC-03", "AC-04", "AC-05"]
    assert "422 MATRIZ_INCOMPLETA" in str(exc_info.value)


async def test_aislamiento_tenant_en_trazabilidad(db_session: AsyncSession) -> None:
    """
    Prueba 4: Verifica el aislamiento multi-tenant tanto en el registro de trazabilidad
    en memoria (rechazo estricto con TenantTraceabilityIsolationError) como en consultas
    sobre core.spec_criteria en PostgreSQL 16 con RLS activo.
    """
    mapper = TraceabilityMapper(db_session=db_session)
    tenant_a = "org_demo"
    tenant_b = "org_intruder_b"
    spec_id = "sp_b305_rls_test"

    # 1. Registrar mapeo para Tenant A
    mapper.register_mapping(
        tenant_id=tenant_a,
        spec_id=spec_id,
        criterion_id="AC-01",
        test_path="tests/unit/test_tenant_a.py",
        test_kind="unit",
        node_id="sym_tenant_a",
    )

    # 2. Intentar leer o asociar criterios en el mismo spec_id desde Tenant B debe fallar
    with pytest.raises(TenantTraceabilityIsolationError, match="aislamiento multi-tenant"):
        mapper.get_mappings_for_spec(tenant_id=tenant_b, spec_id=spec_id)

    with pytest.raises(TenantTraceabilityIsolationError, match="aislamiento multi-tenant"):
        mapper.register_mapping(
            tenant_id=tenant_b,
            spec_id=spec_id,
            criterion_id="AC-02",
            test_path="tests/unit/test_intruder.py",
            test_kind="unit",
            node_id="sym_intruder",
        )

    # 3. Verificar aislamiento RLS en PostgreSQL 16 sobre core.spec_criteria
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_a},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO core.specs (id, tenant_id, ticket_id, version, estado, spec_md_ref, scope_paths)
            VALUES (:spec_id, :tenant_id, 'tk_d1', 99, 'aprobada', 'docs/spec_b305.md', ARRAY['apps/api/**'])
            """
        ),
        {"spec_id": spec_id, "tenant_id": tenant_a},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO core.spec_criteria (spec_id, tenant_id, criterio_id, given_txt, when_txt, then_txt)
            VALUES
              (:spec_id, :tenant_id, 'AC-01', 'dado tenant A', 'cuando ejecuta', 'entonces pasa'),
              (:spec_id, :tenant_id, 'AC-02', 'dado tenant A', 'cuando audita', 'entonces registra')
            """
        ),
        {"spec_id": spec_id, "tenant_id": tenant_a},
    )

    # Tenant A obtiene sus criterios legítimos desde PostgreSQL
    db_criteria_a = await mapper.fetch_spec_criteria_from_db(tenant_id=tenant_a, spec_id=spec_id)
    assert db_criteria_a == ["AC-01", "AC-02"]

    # Un nuevo mapper bajo el contexto de Tenant B no puede ver filas de Tenant A por RLS
    mapper_tenant_b = TraceabilityMapper(db_session=db_session)
    db_criteria_b = await mapper_tenant_b.fetch_spec_criteria_from_db(
        tenant_id=tenant_b,
        spec_id=spec_id,
    )
    assert db_criteria_b == []
