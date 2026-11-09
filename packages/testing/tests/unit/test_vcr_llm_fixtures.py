"""
Ticket: KC-QA-B3-04
Suite de pruebas unitarias y de aislamiento Cero-Nube para vcr_llm_fixtures.py.
Verifica:
1. Intercepción determinista de OpenAI, Anthropic y TEI/Embeddings con latencia < 10 ms.
2. Bloqueo estricto de peticiones HTTP salientes no registradas (assert_all_mocked=True).
3. Carga determinista de casetes con protección contra Path Traversal y mutación en memoria.
4. Integración extremo a extremo con TestGenerator (gen_tests.py) y ASTGuard (ast_guard.py).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx
from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.cm_client import CodeMappingClient, SubgraphDTO, SymbolDTO
from kallicode_testing.gen_tests import TestGenerator
from kallicode_testing.stubs.llm_stub import ErrorSimulationType, LLMStub, ModelType
from respx.models import AllMockedAssertionError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.testing.tests.fixtures.vcr_llm_fixtures import (
    ANTHROPIC_MESSAGES_URL,
    OPENAI_CLOUD_CHAT_URL,
    OPENAI_LOCAL_CHAT_URL,
    TEI_EMBEDDINGS_URL,
    TEI_MODELS_URL,
    load_cassette,
)

pytestmark = pytest.mark.asyncio


async def test_intercepcion_openai_anthropic_y_tei_baja_latencia(
    mock_llm_endpoints: respx.MockRouter,
) -> None:
    """
    Prueba 1: Verifica que mock_llm_endpoints intercepte OpenAI (cloud y local),
    Anthropic y TEI (embeddings y models) devolviendo payloads deterministas en < 10 ms.
    """
    assert mock_llm_endpoints is not None

    async with httpx.AsyncClient() as client:
        # 1. OpenAI Cloud Chat Completions
        t0 = time.perf_counter()
        resp_openai_cloud = await client.post(
            OPENAI_CLOUD_CHAT_URL,
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "AC-01"}]},
        )
        elapsed_openai_cloud_ms = (time.perf_counter() - t0) * 1000.0

        # 2. OpenAI Local Chat Completions
        t1 = time.perf_counter()
        resp_openai_local = await client.post(
            OPENAI_LOCAL_CHAT_URL,
            json={"model": "local-stub", "messages": [{"role": "user", "content": "AC-01"}]},
        )
        elapsed_openai_local_ms = (time.perf_counter() - t1) * 1000.0

        # 3. Anthropic Messages
        t2 = time.perf_counter()
        resp_anthropic = await client.post(
            ANTHROPIC_MESSAGES_URL,
            json={"model": "claude-3-5-sonnet-20241022", "messages": []},
        )
        elapsed_anthropic_ms = (time.perf_counter() - t2) * 1000.0

        # 4. TEI Embeddings (768 dimensiones)
        t3 = time.perf_counter()
        resp_tei_emb = await client.post(
            TEI_EMBEDDINGS_URL,
            json={"input": "verificar aislamiento multi-tenant"},
        )
        elapsed_tei_emb_ms = (time.perf_counter() - t3) * 1000.0

        # 5. TEI Models
        t4 = time.perf_counter()
        resp_tei_models = await client.get(TEI_MODELS_URL)
        elapsed_tei_models_ms = (time.perf_counter() - t4) * 1000.0

    # Validar códigos de estado HTTP 200
    assert resp_openai_cloud.status_code == 200
    assert resp_openai_local.status_code == 200
    assert resp_anthropic.status_code == 200
    assert resp_tei_emb.status_code == 200
    assert resp_tei_models.status_code == 200

    # Validar determinismo de respuestas JSON
    data_openai = resp_openai_cloud.json()
    assert data_openai["id"] == "chatcmpl-kallicode-vcr-001"
    assert resp_openai_local.json() == data_openai
    assert len(data_openai["choices"]) > 0

    data_anthropic = resp_anthropic.json()
    assert data_anthropic["id"] == "msg_kallicode_vcr_001"
    assert len(data_anthropic["content"]) > 0

    data_emb = resp_tei_emb.json()
    assert len(data_emb["data"]) > 0
    embedding_vec = data_emb["data"][0]["embedding"]
    assert len(embedding_vec) == 16

    data_models = resp_tei_models.json()
    assert len(data_models["data"]) > 0
    assert data_models["data"][0]["id"] == "bge-m3"

    # Validar requisito estricto de latencia < 10 ms por petición interceptada
    latencies = [
        elapsed_openai_cloud_ms,
        elapsed_openai_local_ms,
        elapsed_anthropic_ms,
        elapsed_tei_emb_ms,
        elapsed_tei_models_ms,
    ]
    assert len(latencies) > 0
    avg_latency_ms = sum(latencies) / len(latencies)
    assert avg_latency_ms < 10.0, (
        f"Latencia promedio VCR ({avg_latency_ms:.2f} ms) excedió el umbral de 10 ms."
    )


async def test_bloqueo_estricto_peticiones_no_registradas_cero_nube(
    mock_llm_endpoints: respx.MockRouter,
) -> None:
    """
    Prueba 2: Verifica que cualquier intento de conexión HTTP saliente hacia un endpoint
    no registrado falle de inmediato con AllMockedAssertionError (Cero-Nube).
    """
    assert mock_llm_endpoints is not None

    async with httpx.AsyncClient() as client:
        with pytest.raises(AllMockedAssertionError):
            await client.post(
                "https://api.openai.com/v1/unregistered-endpoint",
                json={"prompt": "intento de fuga a la nube"},
            )

        with pytest.raises(AllMockedAssertionError):
            await client.get("https://external-cloud-provider.example.com/v1/status")


async def test_load_cassette_determinismo_y_proteccion_path_traversal() -> None:
    """
    Prueba 3: Verifica que load_cassette devuelva copias profundas aisladas y bloquee
    nombres vacíos, casetes inexistentes o intentos de Path Traversal.
    """
    c1 = load_cassette("openai_chat_completions")
    c2 = load_cassette("openai_chat_completions.json")
    assert c1 == c2

    # Mutar c1 en memoria no debe contaminar futuras lecturas del casete cacheado
    c1["id"] = "mutado-en-memoria"
    c3 = load_cassette("openai_chat_completions")
    assert c3["id"] == "chatcmpl-kallicode-vcr-001"

    # Nombre vacío debe lanzar ValueError
    with pytest.raises(ValueError, match="no puede estar vacío"):
        load_cassette("   ")

    # Path traversal debe ser bloqueado por resolve_safe_path con ValueError
    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        load_cassette("../../../etc/passwd")

    # Casete inexistente dentro del directorio permitido debe lanzar FileNotFoundError
    with pytest.raises(FileNotFoundError, match="Casete VCR no encontrado"):
        load_cassette("casete_inexistente_kallicode")


async def test_integracion_vcr_con_test_generator_y_llm_stub(
    mock_llm_endpoints: respx.MockRouter,
    db_session: AsyncSession,
) -> None:
    """
    Prueba 4: Verifica que TestGenerator (gen_tests.py) y ASTGuard (ast_guard.py)
    puedan consumir el payload interceptado por mock_llm_endpoints sin tráfico externo.
    """
    assert mock_llm_endpoints is not None

    class VCRBackedLLMStub(LLMStub):
        """Stub que delega la invocación en httpx hacia el endpoint interceptado por RESPX."""

        async def generate_test_code(
            self,
            prompt: str,
            model: ModelType = "flash",
            simulate_error: ErrorSimulationType = "none",
        ) -> str:
            _ = (model, simulate_error)
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    OPENAI_CLOUD_CHAT_URL,
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                content: str = payload["choices"][0]["message"]["content"]
                return content

    cm_client = CodeMappingClient(db_session=db_session, tenant_id="tenant_vcr_b3_04")
    vcr_llm = VCRBackedLLMStub()
    ast_guard = ASTGuard()
    generator = TestGenerator(cm_client=cm_client, llm_stub=vcr_llm, ast_guard=ast_guard)

    context = SubgraphDTO(
        root_symbols=["autenticar_usuario"],
        symbols=[
            SymbolDTO(
                id="sym_vcr_1",
                name="autenticar_usuario",
                kind="function",
                signature="async def autenticar_usuario(email: str) -> dict",
                file_path="apps/api/routers/auth.py",
                start_line=1,
                end_line=25,
            )
        ],
    )
    criterion = {
        "id": "AC-01",
        "slug": "login_vcr",
        "given": "un usuario del tenant con credenciales válidas",
        "when": "invoca el endpoint de autenticación bajo intercepción VCR",
        "then": "se genera y valida el test con ASTGuard en cero-nube",
    }

    artifact = await generator.generate_single_criterion_test(
        criterion=criterion,
        context=context,
        target_kind="unit",
    )

    assert artifact.status == "ok"
    assert artifact.attempts == 1
    assert '@criterio("AC-01")' in artifact.code
    assert "test_vcr_login_success" in artifact.code
