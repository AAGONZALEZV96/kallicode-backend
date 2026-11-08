"""
Ticket: KC-QA-B3-11
Suite de pruebas de resiliencia y recuperación ante fallos del motor de generación
(gen_tests.py y api_generator.py) ante respuestas nulas, vacías, truncadas o malformadas del LLM.
Garantiza:
1. Degradación controlada (graceful degradation) ante respuestas None, "", "{}" sin lanzar
   AttributeError, UnboundLocalError ni TypeError.
2. Captura limpia de JSONDecodeError ante JSON truncado, reintento en bucle de reparación
   y escalamiento sin persistir archivos incompletos o corruptos en disco.
3. Validación estricta de esquema ante omisión de campos requeridos ('tests', 'code')
   sin lanzar KeyError ni IndexError.
4. Continuidad de procesamiento en especificaciones multi-criterio donde la falla de un
   criterio no aborta la ejecución del resto, consolidando status 'partial'.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml
from kallicode_testing.api_generator import APITestGenerator
from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.cm_client import (
    CodeMappingClient,
    EndpointSignatureDTO,
    SubgraphDTO,
    SymbolDTO,
)
from kallicode_testing.code_evaluator import CodeEvaluator
from kallicode_testing.gen_tests import TestGenerator
from kallicode_testing.prompts.prompt_manager import PromptManager
from kallicode_testing.stubs.llm_stub import LLMStub
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

TENANT_RESILIENCE = "tenant_resilience_b3_11"


def _build_dummy_subgraph() -> SubgraphDTO:
    """Crea un SubgraphDTO base para pruebas unitarias de generación."""
    return SubgraphDTO(
        root_symbols=["procesar_pago"],
        symbols=[
            SymbolDTO(
                id="sym_pago_01",
                name="procesar_pago",
                kind="function",
                signature="async def procesar_pago(monto: float) -> bool",
                file_path="apps/api/services/pagos.py",
                start_line=15,
                end_line=45,
            )
        ],
    )


def _build_dummy_endpoint() -> EndpointSignatureDTO:
    """Crea un EndpointSignatureDTO representativo de un router FastAPI."""
    return EndpointSignatureDTO(
        symbol_id="sym_api_pagos_post",
        name="procesar_pago_api",
        signature="async def procesar_pago_api(payload: dict, db: AsyncSession) -> dict",
        file_path="apps/api/routers/pagos.py",
        http_method="POST",
        route_path="/api/v1/pagos",
        pydantic_schemas=["PagoIn", "PagoOut"],
        definition_summary="Endpoint de procesamiento asíncrono de pagos",
    )


async def test_respuesta_nula_o_vacia_llm(db_session: AsyncSession) -> None:
    """
    Escenario 1: Configurar el stub/mock del LLM para retornar None, cadena vacía
    y espacios en blanco. Verificar que el generador no lance excepciones no capturadas
    (AttributeError, UnboundLocalError, TypeError) y degrade a status='escalate'.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_RESILIENCE)
    ast_guard = ASTGuard()
    prompt_manager = PromptManager()
    code_evaluator = CodeEvaluator(ast_guard=ast_guard)
    subgraph = _build_dummy_subgraph()

    criterion: dict[str, Any] = {
        "id": "AC-01",
        "slug": "pago_nulo",
        "given": "un cliente con cuenta activa",
        "when": "el LLM retorna una respuesta nula",
        "then": "el sistema debe escalar sin crash",
    }

    # Probar retorno de None, cadena vacía y solo espacios
    adversarial_responses: list[Any] = [None, "", "    \n\t  "]

    for null_resp in adversarial_responses:
        llm_mock = AsyncMock(spec=LLMStub)
        llm_mock.generate_test_code.return_value = null_resp

        generator = TestGenerator(
            cm_client=cm_client,
            llm_stub=llm_mock,
            ast_guard=ast_guard,
            prompt_manager=prompt_manager,
            code_evaluator=code_evaluator,
        )

        artifact = await generator.generate_single_criterion_test(
            criterion=criterion,
            context=subgraph,
            target_kind="unit",
        )

        assert artifact.status == "escalate"
        assert artifact.code == ""
        assert artifact.file_path is None
        assert artifact.attempts == 2
        assert len(artifact.errors) >= 1
        assert any("Respuesta nula o vacía del LLM" in err for err in artifact.errors)


async def test_respuesta_truncada_json_invalido(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """
    Escenario 2: El LLM retorna un JSON truncado/incompleto.
    Verificar que:
    1. Se capture limpiamente JSONDecodeError sin romper el flujo.
    2. Se reintente en el bucle de reparación (max_rounds = 2).
    3. Se degrade a status='escalate'.
    4. NO se escriba ningún archivo corrupto o truncado en disco.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_RESILIENCE)
    ast_guard = ASTGuard()
    prompt_manager = PromptManager()
    code_evaluator = CodeEvaluator(ast_guard=ast_guard)

    truncated_json = (
        '{"tests": [{"name": "test_pago_truncado", "code": "def test_pago():\n    assert 1 == '
    )

    llm_mock = AsyncMock(spec=LLMStub)
    llm_mock.generate_test_code.return_value = truncated_json

    generator = TestGenerator(
        cm_client=cm_client,
        llm_stub=llm_mock,
        ast_guard=ast_guard,
        prompt_manager=prompt_manager,
        code_evaluator=code_evaluator,
    )

    spec_data: dict[str, Any] = {
        "spec_id": "SPEC-RES-01",
        "symbol_names": ["procesar_pago"],
        "criterios": [
            {
                "id": "AC-01",
                "slug": "pago_truncado",
                "given": "un pago en proceso",
                "when": "el LLM se corta por límite de tokens",
                "then": "el sistema degrada y escala",
            }
        ],
    }

    out_dir = tmp_path / "tests_out_trunc"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_yaml = tmp_path / "spec_res_01.yaml"
    spec_yaml.write_text(yaml.safe_dump(spec_data), encoding="utf-8")

    report = await generator.generate_for_spec(
        spec_yaml_path=spec_yaml,
        tenant_id=TENANT_RESILIENCE,
        out_dir=out_dir,
    )

    assert report.status == "escalate"
    assert "AC-01" in report.escalated_criteria
    assert len(report.artifacts) == 1
    assert report.artifacts[0].status == "escalate"
    assert report.artifacts[0].attempts == 2
    assert any("JSON malformado" in err for err in report.artifacts[0].errors)

    # Verificación de integridad: ningún archivo corrupto debe haber sido persistido
    persisted_files = list(out_dir.glob("*.py"))
    assert len(persisted_files) == 0, (
        f"Se encontraron archivos corruptos en disco: {persisted_files}"
    )


async def test_respuesta_sin_campos_obligatorios(db_session: AsyncSession) -> None:
    """
    Escenario 3: El LLM retorna un JSON sintácticamente válido pero sin el esquema esperado:
    - Sin clave 'tests'
    - Clave 'tests' vacía
    - Elementos sin clave 'code'
    - Clave 'code' con cadena vacía o sólo espacios
    Verificar que no lance KeyError ni IndexError y escale a revisión humana.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_RESILIENCE)
    ast_guard = ASTGuard()
    subgraph = _build_dummy_subgraph()

    criterion: dict[str, Any] = {
        "id": "AC-02",
        "slug": "esquema_invalido",
        "given": "un contexto válido",
        "when": "el LLM omite campos obligatorios",
        "then": "el validador detecta la omisión y escala",
    }

    malformed_schemas = [
        json.dumps({"mensaje": "No se pudo generar el código"}),
        json.dumps({"tests": []}),
        json.dumps({"tests": [{"name": "test_sin_codigo"}]}),
        json.dumps({"tests": [{"name": "test_codigo_vacio", "code": "   \n"}]}),
        json.dumps("cadena_plana_no_objeto"),
    ]

    for schema_payload in malformed_schemas:
        llm_mock = AsyncMock(spec=LLMStub)
        llm_mock.generate_test_code.return_value = schema_payload

        generator = TestGenerator(
            cm_client=cm_client,
            llm_stub=llm_mock,
            ast_guard=ast_guard,
        )

        artifact = await generator.generate_single_criterion_test(
            criterion=criterion,
            context=subgraph,
            target_kind="unit",
        )

        assert artifact.status == "escalate"
        assert artifact.code == ""
        assert artifact.file_path is None
        assert len(artifact.errors) >= 1
        assert any(
            (
                "JSON sin lista 'tests' válida" in err
                or "Código de prueba vacío" in err
                or "JSON raíz no es un objeto válido" in err
            )
            for err in artifact.errors
        )


async def test_resiliencia_continuidad_procesamiento(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """
    Escenario 4: Especificación con múltiples criterios (AC-01 y AC-02).
    El LLM falla/retorna nulo para AC-01, pero genera código válido para AC-02.
    Verificar que:
    1. La falla en AC-01 no interrumpe ni aborta el procesamiento de AC-02.
    2. AC-02 se genera y persiste exitosamente en disco con trazabilidad.
    3. El reporte consolidado tiene status='partial'.
    4. AC-01 se registra en escalated_criteria y AC-02 en artifacts válidos.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_RESILIENCE)
    ast_guard = ASTGuard()

    valid_test_json = json.dumps(
        {
            "tests": [
                {
                    "name": "test_pago_valido",
                    "code": (
                        "def test_pago_valido():\n"
                        '    """Prueba unitaria válida de pago."""\n'
                        "    monto = 100.0\n"
                        "    assert monto > 0\n"
                    ),
                    "type": "unit",
                }
            ]
        }
    )

    llm_mock = AsyncMock(spec=LLMStub)
    # AC-01 reintentará 2 veces (retornando None ambas veces); AC-02 retornará JSON válido
    llm_mock.generate_test_code.side_effect = [
        None,
        None,
        valid_test_json,
    ]

    generator = TestGenerator(
        cm_client=cm_client,
        llm_stub=llm_mock,
        ast_guard=ast_guard,
    )

    spec_data: dict[str, Any] = {
        "spec_id": "SPEC-MULTI-01",
        "symbol_names": ["procesar_pago"],
        "criterios": [
            {
                "id": "AC-01",
                "slug": "criterio_fallido",
                "given": "entrada inválida",
                "when": "el LLM retorna nulo",
                "then": "debe escalar",
            },
            {
                "id": "AC-02",
                "slug": "criterio_exitoso",
                "given": "entrada válida",
                "when": "el LLM retorna código correcto",
                "then": "debe persistir",
            },
        ],
    }

    out_dir = tmp_path / "tests_out_multi"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_yaml = tmp_path / "spec_multi_01.yaml"
    spec_yaml.write_text(yaml.safe_dump(spec_data), encoding="utf-8")

    report = await generator.generate_for_spec(
        spec_yaml_path=spec_yaml,
        tenant_id=TENANT_RESILIENCE,
        out_dir=out_dir,
    )

    # Estado consolidado debe ser 'partial' al haber un criterio exitoso y uno escalado
    assert report.status == "partial"
    assert report.escalated_criteria == ["AC-01"]
    assert len(report.artifacts) == 2

    # Inspeccionar artefacto 1 (AC-01 escalado)
    art_failed = next(a for a in report.artifacts if a.criterion_id == "AC-01")
    assert art_failed.status == "escalate"
    assert art_failed.file_path is None

    # Inspeccionar artefacto 2 (AC-02 exitoso y persistido)
    art_ok = next(a for a in report.artifacts if a.criterion_id == "AC-02")
    assert art_ok.status == "ok"
    assert art_ok.file_path is not None
    assert art_ok.file_path.exists()

    persisted_code = art_ok.file_path.read_text(encoding="utf-8")
    assert '@criterio("AC-02")' in persisted_code
    assert "def test_pago_valido" in persisted_code


async def test_respuesta_nula_o_vacia_api_generator(tmp_path: Path) -> None:
    """
    Escenario 5: APITestGenerator ante respuestas None, vacías o no-string.
    Verifica ausencia de crashes y reporte con status='escalate' sin archivos generados.
    """
    prompt_manager = PromptManager()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator(ast_guard=ast_guard)
    endpoint = _build_dummy_endpoint()

    criterion: dict[str, Any] = {
        "id": "AC-API-01",
        "slug": "api_nulo",
        "given": "un cliente API",
        "when": "el LLM no retorna contenido",
        "then": "escala elegantemente",
    }

    for empty_val in [None, "", "   "]:
        llm_mock = AsyncMock(spec=LLMStub)
        llm_mock.generate_test_code.return_value = empty_val

        api_gen = APITestGenerator(
            prompt_manager=prompt_manager,
            llm_stub=llm_mock,
            ast_guard=ast_guard,
            code_evaluator=code_evaluator,
        )

        report = await api_gen.generate_api_test(
            criterion=criterion,
            endpoint_signature=endpoint,
            tenant_id=TENANT_RESILIENCE,
            out_dir=tmp_path,
        )

        assert report.status == "escalate"
        assert report.escalated_criteria == ["AC-API-01"]
        assert len(report.artifacts) == 1
        assert report.artifacts[0].status == "escalate"
        assert any("Respuesta nula o vacía del LLM" in err for err in report.artifacts[0].errors)

    # Validar que ningún archivo fue escrito
    assert len(list(tmp_path.glob("*.py"))) == 0


async def test_respuesta_truncada_api_generator(tmp_path: Path) -> None:
    """
    Escenario 6: APITestGenerator ante JSON truncado.
    Verifica captura de parse error, reintentos y que no se persistan archivos incompletos.
    """
    prompt_manager = PromptManager()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator(ast_guard=ast_guard)
    endpoint = _build_dummy_endpoint()

    criterion: dict[str, Any] = {
        "id": "AC-API-02",
        "slug": "api_truncado",
        "given": "un flujo API asíncrono",
        "when": "la respuesta del LLM queda truncada",
        "then": "debe escalar sin persistir código corrupto",
    }

    truncated_json = (
        '{"tests": [{"name": "test_api_truncado", "code": "async def test_api(http_client):'
    )

    llm_mock = AsyncMock(spec=LLMStub)
    llm_mock.generate_test_code.return_value = truncated_json

    api_gen = APITestGenerator(
        prompt_manager=prompt_manager,
        llm_stub=llm_mock,
        ast_guard=ast_guard,
        code_evaluator=code_evaluator,
    )

    report = await api_gen.generate_api_test(
        criterion=criterion,
        endpoint_signature=endpoint,
        tenant_id=TENANT_RESILIENCE,
        out_dir=tmp_path,
    )

    assert report.status == "escalate"
    assert report.escalated_criteria == ["AC-API-02"]
    assert report.artifacts[0].attempts == 2
    assert any("JSON malformado" in err for err in report.artifacts[0].errors)

    # No se deben generar archivos en tmp_path
    assert len(list(tmp_path.glob("*.py"))) == 0


async def test_respuesta_sin_campos_obligatorios_api_generator(tmp_path: Path) -> None:
    """
    Escenario 7: APITestGenerator ante JSON sin clave 'tests' o sin código de prueba.
    Verifica manejo elegante y reporte de escalación.
    """
    prompt_manager = PromptManager()
    ast_guard = ASTGuard()
    code_evaluator = CodeEvaluator(ast_guard=ast_guard)
    endpoint = _build_dummy_endpoint()

    criterion: dict[str, Any] = {
        "id": "AC-API-03",
        "slug": "api_sin_campos",
        "given": "un endpoint FastAPI",
        "when": "el LLM retorna JSON sin clave tests",
        "then": "debe escalar limpiamente",
    }

    invalid_json = json.dumps({"status": "no_tests_available", "details": []})

    llm_mock = AsyncMock(spec=LLMStub)
    llm_mock.generate_test_code.return_value = invalid_json

    api_gen = APITestGenerator(
        prompt_manager=prompt_manager,
        llm_stub=llm_mock,
        ast_guard=ast_guard,
        code_evaluator=code_evaluator,
    )

    report = await api_gen.generate_api_test(
        criterion=criterion,
        endpoint_signature=endpoint,
        tenant_id=TENANT_RESILIENCE,
        out_dir=tmp_path,
    )

    assert report.status == "escalate"
    assert report.escalated_criteria == ["AC-API-03"]
    assert any("JSON sin lista 'tests' válida" in err for err in report.artifacts[0].errors)
    assert len(list(tmp_path.glob("*.py"))) == 0
