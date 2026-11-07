"""
Ticket: KC-QA-B3-10
Suite de pruebas unitarias y adversarias para APITestGenerator (api_generator.py).
Verifica:
1. Generación exitosa de pruebas de integración API asíncronas con pytest-asyncio, httpx y @criterio("AC-n").
2. Rechazo en AST de funciones síncronas ('def test_*') forzando autocorrección a 'async def test_*'.
3. Confinamiento estricto contra Path Traversal (QA-F03) al derivar y guardar rutas de prueba.
4. Inyección visible de aislamiento multi-tenant en cabeceras HTTP ('X-Tenant-ID') o contexto RLS.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from kallicode_testing.api_generator import APITestGenerator
from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.cm_client import EndpointSignatureDTO
from kallicode_testing.code_evaluator import CodeEvaluator
from kallicode_testing.prompts.prompt_manager import PromptManager
from kallicode_testing.stubs.llm_stub import LLMStub

pytestmark = pytest.mark.asyncio


def _create_sample_endpoint() -> EndpointSignatureDTO:
    """Genera un EndpointSignatureDTO representativo de un router FastAPI."""
    return EndpointSignatureDTO(
        symbol_id="sym_api_tickets_create",
        name="crear_ticket",
        signature="async def crear_ticket(ticket: TicketIn, db: AsyncSession) -> TicketOut",
        file_path="apps/api/routers/tickets.py",
        http_method="POST",
        route_path="/api/v1/tickets",
        pydantic_schemas=["TicketIn", "TicketOut"],
        definition_summary="Endpoint para creación de tickets con deduplicación vectorial",
    )


async def test_generacion_test_api_async_exitoso(tmp_path: Path) -> None:
    """
    Prueba 1: Simula un criterio BDD sobre un endpoint FastAPI y verifica que el archivo
    generado compile en AST, sea async def, contenga decoradores y use httpx.AsyncClient.
    """
    prompt_manager = PromptManager()
    llm_stub = LLMStub()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator()

    valid_code = (
        "import httpx\n"
        "import pytest\n"
        "from kallicode_testing.gen_tests import criterio\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
        "@pytest.mark.asyncio\n"
        '@criterio("AC-01")\n'
        "async def test_crear_ticket_exitoso(\n"
        "    http_client: httpx.AsyncClient,\n"
        "    db_session: AsyncSession,\n"
        ") -> None:\n"
        '    headers = {"X-Tenant-ID": "tenant_api_success"}\n'
        '    resp = await http_client.post("/api/v1/tickets", json={"titulo": "Test"}, headers=headers)\n'
        "    assert resp.status_code == 200\n"
    )

    llm_stub.generate_test_code = AsyncMock(
        return_value=json.dumps(
            {
                "tests": [
                    {
                        "name": "test_crear_ticket_exitoso",
                        "code": valid_code,
                        "type": "api",
                    }
                ]
            }
        )
    )

    generator = APITestGenerator(
        prompt_manager=prompt_manager,
        llm_stub=llm_stub,
        ast_guard=ast_guard,
        code_evaluator=code_evaluator,
    )

    criterion = {
        "id": "AC-01",
        "slug": "crear_ticket_valido",
        "given": "un usuario del tenant tenant_api_success con rol 'member'",
        "when": "envía un payload JSON válido a POST /api/v1/tickets",
        "then": "recibe HTTP 200 con el ticket persistido",
    }
    endpoint_sig = _create_sample_endpoint()

    report = await generator.generate_api_test(
        criterion=criterion,
        endpoint_signature=endpoint_sig,
        tenant_id="tenant_api_success",
        out_dir=tmp_path,
    )

    assert report.status == "ok"
    assert len(report.artifacts) == 1
    artifact = report.artifacts[0]
    assert artifact.status == "ok"
    assert artifact.file_path is not None
    assert artifact.file_path.is_file()

    # Verificar compilación sintáctica en AST
    saved_code = artifact.file_path.read_text(encoding="utf-8")
    tree = ast.parse(saved_code)
    assert tree is not None

    # Verificar presencia de async def test_* y decoradores
    assert "async def test_crear_ticket_exitoso" in saved_code
    assert "@pytest.mark.asyncio" in saved_code
    assert '@criterio("AC-01")' in saved_code
    assert "httpx.AsyncClient" in saved_code
    assert 'headers = {"X-Tenant-ID": "tenant_api_success"}' in saved_code


async def test_verificacion_sintaxis_async_def_en_ast(tmp_path: Path) -> None:
    """
    Prueba 2: Verifica que _validate_async_api_ast rechace funciones síncronas 'def test_*'
    y que el bucle generate_with_repair reintente forzando 'async def test_*'.
    """
    prompt_manager = PromptManager()
    llm_stub = LLMStub()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator()

    generator = APITestGenerator(
        prompt_manager=prompt_manager,
        llm_stub=llm_stub,
        ast_guard=ast_guard,
        code_evaluator=code_evaluator,
    )

    # 1. Probar validación directa en AST
    sync_code = "def test_sync_invalido():\n    assert True\n"
    is_valid, errors = generator._validate_async_api_ast(sync_code)
    assert is_valid is False
    assert any("es síncrona" in err and "async def" in err for err in errors)

    # 2. Simular bucle de reparación donde el intento 1 falla por síncrono y el intento 2 pasa
    sync_mock_resp = json.dumps(
        {
            "tests": [
                {
                    "name": "test_sync",
                    "code": (
                        "import httpx\n"
                        "def test_sync_endpoint(http_client: httpx.AsyncClient) -> None:\n"
                        '    resp = http_client.get("/status")\n'
                        "    assert resp.status_code == 200\n"
                    ),
                    "type": "api",
                }
            ]
        }
    )
    repaired_async_resp = json.dumps(
        {
            "tests": [
                {
                    "name": "test_repaired_async",
                    "code": (
                        "import httpx\n"
                        "import pytest\n"
                        "from kallicode_testing.gen_tests import criterio\n"
                        "from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
                        "@pytest.mark.asyncio\n"
                        '@criterio("AC-02")\n'
                        "async def test_repaired_async(\n"
                        "    http_client: httpx.AsyncClient,\n"
                        "    db_session: AsyncSession,\n"
                        ") -> None:\n"
                        '    headers = {"X-Tenant-ID": "tenant_repaired"}\n'
                        '    resp = await http_client.get("/status", headers=headers)\n'
                        "    assert resp.status_code == 200\n"
                    ),
                    "type": "api",
                }
            ]
        }
    )

    llm_stub.generate_test_code = AsyncMock(side_effect=[sync_mock_resp, repaired_async_resp])

    criterion = {
        "id": "AC-02",
        "slug": "async_repair_flow",
        "given": "un servicio API corriendo",
        "when": "se envía GET /status",
        "then": "responde 200 de forma asíncrona",
    }
    endpoint_sig = _create_sample_endpoint()

    report = await generator.generate_api_test(
        criterion=criterion,
        endpoint_signature=endpoint_sig,
        tenant_id="tenant_repaired",
        out_dir=tmp_path,
    )

    assert report.status == "ok"
    artifact = report.artifacts[0]
    assert artifact.attempts == 2
    assert "async def test_repaired_async" in artifact.code
    assert any("es síncrona" in err for err in artifact.errors)


async def test_prevencion_path_traversal_al_guardar_test_api(tmp_path: Path) -> None:
    """
    Prueba 3: Simula nombres maliciosos ('../../../etc/passwd') y verifica que
    _sanitize_output_path confine el archivo estrictamente dentro de out_dir.
    """
    prompt_manager = PromptManager()
    llm_stub = LLMStub()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator()

    generator = APITestGenerator(
        prompt_manager=prompt_manager,
        llm_stub=llm_stub,
        ast_guard=ast_guard,
        code_evaluator=code_evaluator,
    )

    safe_path = generator._sanitize_output_path(
        criterion_id="../../../etc/passwd",
        slug="../../shadow",
        base_dir=tmp_path,
    )
    assert safe_path.is_relative_to(tmp_path.resolve())
    assert "etc" not in safe_path.parts
    assert "passwd" not in safe_path.parts
    assert "shadow" not in safe_path.parts
    assert safe_path.name.startswith("test_api_")
    assert safe_path.name.endswith(".py")

    # Prueba de generación completa con identificador hostil
    valid_code = (
        "import httpx\n"
        "import pytest\n"
        "from kallicode_testing.gen_tests import criterio\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
        "@pytest.mark.asyncio\n"
        '@criterio("AC-03")\n'
        "async def test_path_traversal_immune(\n"
        "    http_client: httpx.AsyncClient,\n"
        "    db_session: AsyncSession,\n"
        ") -> None:\n"
        '    headers = {"X-Tenant-ID": "tenant_safe"}\n'
        '    resp = await http_client.get("/api/v1/health", headers=headers)\n'
        "    assert resp.status_code == 200\n"
    )

    llm_stub.generate_test_code = AsyncMock(
        return_value=json.dumps(
            {
                "tests": [
                    {
                        "name": "test_path_traversal_immune",
                        "code": valid_code,
                        "type": "api",
                    }
                ]
            }
        )
    )

    criterion = {
        "id": "../../../etc/passwd",
        "slug": "../../../var/log/attack",
        "given": "un intento de path traversal",
        "when": "se deriva la ruta del archivo",
        "then": "se confina dentro del out_dir",
    }
    report = await generator.generate_api_test(
        criterion=criterion,
        endpoint_signature=_create_sample_endpoint(),
        tenant_id="tenant_safe",
        out_dir=tmp_path,
    )

    assert report.status == "ok"
    artifact = report.artifacts[0]
    assert artifact.file_path is not None
    assert artifact.file_path.is_relative_to(tmp_path.resolve())
    assert artifact.file_path.exists()


async def test_aislamiento_tenant_en_fixtures_api_generados(tmp_path: Path) -> None:
    """
    Prueba 4: Verifica que el código generado para un tenant específico
    incluya explícitamente el encabezado HTTP de tenant ('X-Tenant-ID').
    """
    prompt_manager = PromptManager()
    llm_stub = LLMStub()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator()

    tenant_id_target = "tenant_cyberdyne_systems_800"

    raw_test_without_header = (
        "import httpx\n"
        "import pytest\n"
        "from kallicode_testing.gen_tests import criterio\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
        "@pytest.mark.asyncio\n"
        '@criterio("AC-04")\n'
        "async def test_sin_cabecera(\n"
        "    http_client: httpx.AsyncClient,\n"
        "    db_session: AsyncSession,\n"
        ") -> None:\n"
        '    resp = await http_client.get("/api/v1/webhooks")\n'
        "    assert resp.status_code == 200\n"
    )

    llm_stub.generate_test_code = AsyncMock(
        return_value=json.dumps(
            {
                "tests": [
                    {
                        "name": "test_sin_cabecera",
                        "code": raw_test_without_header,
                        "type": "api",
                    }
                ]
            }
        )
    )

    generator = APITestGenerator(
        prompt_manager=prompt_manager,
        llm_stub=llm_stub,
        ast_guard=ast_guard,
        code_evaluator=code_evaluator,
    )

    criterion = {
        "id": "AC-04",
        "slug": "webhooks_isolation",
        "given": "el webhook receiver para tenant_cyberdyne_systems_800",
        "when": "se envía un evento firmado",
        "then": "se valida con aislamiento multi-tenant",
    }
    endpoint_sig = EndpointSignatureDTO(
        symbol_id="sym_api_webhook_receive",
        name="recibir_webhook",
        signature="async def recibir_webhook(request: Request) -> dict",
        file_path="apps/api/routers/webhooks.py",
        http_method="POST",
        route_path="/api/v1/webhooks",
    )

    report = await generator.generate_api_test(
        criterion=criterion,
        endpoint_signature=endpoint_sig,
        tenant_id=tenant_id_target,
        out_dir=tmp_path,
    )

    assert report.status == "ok"
    artifact = report.artifacts[0]
    assert artifact.file_path is not None
    code_text = artifact.file_path.read_text(encoding="utf-8")

    assert tenant_id_target in code_text
    assert f'"X-Tenant-ID": "{tenant_id_target}"' in code_text
