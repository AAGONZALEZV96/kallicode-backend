"""
Ticket: KC-QA-B3-10
Generador de Pruebas de Integración para Flujos API Async (api_generator.py).
Genera especificaciones de prueba API asíncronas en Python 3.12 utilizando
pytest-asyncio, httpx.AsyncClient, db_session y contexto RLS multi-tenant (app.tenant_id),
inyectando obligatoriamente @criterio("AC-n") y validando calidad con ASTGuard y CodeEvaluator.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.cm_client import EndpointSignatureDTO
from kallicode_testing.code_evaluator import CodeEvaluator
from kallicode_testing.gen_tests import (
    GeneratedArtifact,
    GenerationReport,
    _clean_identifier,
)
from kallicode_testing.logger import get_logger
from kallicode_testing.prompts.prompt_manager import PromptManager
from kallicode_testing.stubs.llm_stub import LLMStub, ModelType
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

_ASYNC_TEST_FUNC_RE = re.compile(r"\basync\s+def\s+test_[a-zA-Z0-9_]*\s*\(")
_SYNC_TEST_FUNC_RE = re.compile(r"(?<!async\s)\bdef\s+test_[a-zA-Z0-9_]*\s*\(")


def _ensure_async_test_signature(
    code: str,
    criterion_id: str,
    tenant_id: str,
    route_path: str = "/api/v1/tickets",
    http_method: str = "POST",
) -> str:
    """
    Asegura que el código generado contenga imports estándar, decoradores obligatorios
    (@pytest.mark.asyncio y @criterio("AC-n")), la firma async def test_*(http_client, db_session)
    y encabezados de aislamiento multi-tenant.
    """
    # Si el código ya contiene async def test_*, inyectar decoradores si faltan
    lines = code.splitlines()
    has_asyncio_mark = any("@pytest.mark.asyncio" in line for line in lines)
    has_criterio_mark = any(f'@criterio("{criterion_id}")' in line for line in lines)

    new_lines: list[str] = []
    decorated = False

    for line in lines:
        stripped = line.lstrip()
        if not decorated and stripped.startswith("async def test_"):
            indent = line[: len(line) - len(stripped)]
            if not has_asyncio_mark:
                new_lines.append(f"{indent}@pytest.mark.asyncio")
            if not has_criterio_mark:
                new_lines.append(f'{indent}@criterio("{criterion_id}")')
            decorated = True
        new_lines.append(line)

    result_code = "\n".join(new_lines) if decorated else code

    # Si faltan encabezados de tenant o imports básicos, construimos un bloque canónico limpio
    required_imports = [
        "import httpx",
        "import pytest",
        "from kallicode_testing.gen_tests import criterio",
        "from sqlalchemy.ext.asyncio import AsyncSession",
    ]
    for req_imp in required_imports:
        if req_imp not in result_code:
            result_code = f"{req_imp}\n" + result_code

    if "X-Tenant-ID" not in result_code and "app.tenant_id" not in result_code:
        header_snippet = f'    headers = {{"X-Tenant-ID": "{tenant_id}"}}'
        if "headers=" not in result_code and "http_client." in result_code:
            result_code = re.sub(
                r"(http_client\.(?:get|post|put|delete|patch)\([^)]*)(\))",
                r"\1, headers=headers\2",
                result_code,
                count=1,
            )
        else:
            header_snippet += f'\n    assert headers["X-Tenant-ID"] == "{tenant_id}"'

        code_lines = result_code.splitlines()
        in_async_func = False
        inserted = False
        final_lines: list[str] = []
        for line in code_lines:
            final_lines.append(line)
            if not inserted:
                if "async def test_" in line:
                    in_async_func = True
                if in_async_func and line.rstrip().endswith(":"):
                    final_lines.append(header_snippet)
                    inserted = True
        if inserted:
            result_code = "\n".join(final_lines)

    return result_code.strip() + "\n"


class APITestGenerator:
    """
    Generador de pruebas de integración para flujos API asíncronos con validación AST,
    evaluación de estilo/linter y bucle de reparación de hasta 2 rondas.
    """

    __test__ = False

    def __init__(
        self,
        prompt_manager: PromptManager,
        llm_stub: LLMStub,
        ast_guard: ASTGuard,
        code_evaluator: CodeEvaluator,
    ) -> None:
        self.prompt_manager: PromptManager = prompt_manager
        self.llm_stub: LLMStub = llm_stub
        self.ast_guard: ASTGuard = ast_guard
        self.code_evaluator: CodeEvaluator = code_evaluator

    def _sanitize_output_path(
        self,
        criterion_id: str,
        slug: str,
        base_dir: Path,
    ) -> Path:
        """
        Deriva el nombre de archivo seguro mediante 'test_api_{criterion_id}_{slug}.py'
        y valida confinamiento estricto dentro de base_dir con resolve_safe_path (QA-F03).
        """
        safe_cid = _clean_identifier(criterion_id, "ac-00")
        safe_slug = _clean_identifier(slug, "endpoint")
        filename = f"test_api_{safe_cid}_{safe_slug}.py"
        return resolve_safe_path(base_dir, filename)

    def _validate_async_api_ast(self, code: str) -> tuple[bool, list[str]]:
        """
        Valida que el código de prueba API sea una corrutina asíncrona:
        1. Rechaza funciones síncronas 'def test_*'.
        2. Exige la presencia obligatoria de 'async def test_*'.
        3. Valida ausencia de imports prohibidos y sintaxis básica mediante ASTGuard.
        """
        if not code or not code.strip():
            return False, ["El código API generado está vacío."]

        # Validar primero reglas generales de ASTGuard (SyntaxError, módulos bloqueados)
        _is_guard_valid, guard_errors = self.ast_guard.validate_python_ast(code)
        errors = list(guard_errors)

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            line_num = exc.lineno if exc.lineno is not None else 0
            return False, [f"SyntaxError en línea {line_num}: {exc.msg}"]

        has_async_test = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                errors.append(
                    f"La función de prueba '{node.name}' en línea {node.lineno} es síncrona "
                    f"('def {node.name}'). Debe ser asíncrona ('async def {node.name}') "
                    f"para flujos API con httpx.AsyncClient."
                )
            elif isinstance(node, ast.AsyncFunctionDef) and node.name.startswith("test_"):
                has_async_test = True

        if not has_async_test:
            errors.append(
                "El código no define ninguna función de prueba asíncrona 'async def test_*'."
            )

        # Eliminar posible error genérico de ASTGuard si ya lo reportamos aquí
        cleaned_errors = [
            err for err in errors if "no define ninguna función 'def test_*'" not in err
        ]

        return len(cleaned_errors) == 0, cleaned_errors

    async def generate_with_repair(
        self,
        payload: dict[str, Any],
        out_dir: Path,
    ) -> GenerationReport:
        """
        Ejecuta el bucle de generación y reparación (hasta 2 rondas) validando
        con ASTGuard, _validate_async_api_ast y CodeEvaluator antes de persistir.
        """
        criterion_id = str(payload.get("criterion_id", "AC-01"))
        tenant_id = str(payload.get("tenant_id", "tenant_default"))
        slug = str(payload.get("slug", "endpoint_test"))
        endpoint_sig = payload.get("endpoint_signature")
        route_path = "/api/v1/tickets"
        http_method = "POST"
        if isinstance(endpoint_sig, EndpointSignatureDTO):
            route_path = endpoint_sig.route_path or route_path
            http_method = endpoint_sig.http_method or http_method
        elif isinstance(endpoint_sig, dict):
            route_path = str(endpoint_sig.get("route_path") or route_path)
            http_method = str(endpoint_sig.get("http_method") or http_method)

        accumulated_errors: list[str] = []
        max_rounds = 2
        model_tier: ModelType = "pro"

        out_dir_resolved = Path(out_dir).resolve()
        out_dir_resolved.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, max_rounds + 1):
            render_vars: dict[str, Any] = {
                "tenant_id": tenant_id,
                "criterion_id": criterion_id,
                "given": str(payload.get("given") or f"un usuario del tenant {tenant_id}"),
                "when": str(payload.get("when") or f"envía petición {http_method} a {route_path}"),
                "then": str(payload.get("then") or "recibe respuesta HTTP 200 y datos validados"),
                "context_symbols": payload.get("context_symbols", []),
                "context_tables": payload.get("context_tables", []),
                "previous_errors": list(accumulated_errors),
                "attempt": attempt,
            }

            system_prompt, user_prompt = self.prompt_manager.render_prompt(
                name="pytest",
                variables=render_vars,
                version="v1",
            )

            serializable_payload: dict[str, Any] = {}
            for k, v in payload.items():
                if hasattr(v, "model_dump"):
                    serializable_payload[k] = v.model_dump()
                else:
                    serializable_payload[k] = v

            prompt_payload = {
                **serializable_payload,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "attempt": attempt,
                "previous_errors": list(accumulated_errors),
            }
            prompt_str = json.dumps(prompt_payload, ensure_ascii=False)

            raw_response = await self.llm_stub.generate_test_code(
                prompt=prompt_str,
                model=model_tier,
            )

            if not raw_response or not isinstance(raw_response, str) or not raw_response.strip():
                accumulated_errors.append(f"Intento {attempt}: Respuesta nula o vacía del LLM.")
                logger.warning(
                    "api_generator.llm_empty_response",
                    criterion_id=criterion_id,
                    attempt=attempt,
                )
                continue

            try:
                parsed = json.loads(raw_response)
                if not isinstance(parsed, dict):
                    accumulated_errors.append(
                        f"Intento {attempt}: JSON raíz no es un objeto válido."
                    )
                    continue
                tests_list = parsed.get("tests", [])
                if (
                    not isinstance(tests_list, list)
                    or not tests_list
                    or not isinstance(tests_list[0], dict)
                ):
                    accumulated_errors.append(f"Intento {attempt}: JSON sin lista 'tests' válida.")
                    continue
                first_test = tests_list[0]
                raw_code = str(first_test.get("code") or "").strip()
                if not raw_code:
                    accumulated_errors.append(
                        f"Intento {attempt}: Código de prueba vacío en el JSON."
                    )
                    continue
            except (json.JSONDecodeError, TypeError) as exc:
                accumulated_errors.append(f"Intento {attempt}: JSON malformado ({exc}).")
                logger.warning(
                    "api_generator.llm_malformed_json",
                    criterion_id=criterion_id,
                    attempt=attempt,
                    error=str(exc),
                )
                continue

            # Validar asincronía y sintaxis en AST
            is_async_valid, async_errors = self._validate_async_api_ast(raw_code)
            if not is_async_valid:
                accumulated_errors.extend([f"Intento {attempt}: {err}" for err in async_errors])
                logger.warning(
                    "api_generator.repair_retry_ast",
                    criterion_id=criterion_id,
                    attempt=attempt,
                    errors=async_errors,
                )
                continue

            # Inyectar decoradores, fixtures y tenant headers si es necesario
            formatted_code = _ensure_async_test_signature(
                code=raw_code,
                criterion_id=criterion_id,
                tenant_id=tenant_id,
                route_path=route_path,
                http_method=http_method,
            )

            # Validar estilo y linter con CodeEvaluator
            eval_result = self.code_evaluator.evaluate_code(formatted_code, target_kind="api")
            if not eval_result.is_clean:
                eval_issues_msg = [
                    f"Intento {attempt}: [{iss.code}] Línea {iss.line}: {iss.message}"
                    for iss in eval_result.issues
                ]
                accumulated_errors.extend(eval_issues_msg)
                logger.warning(
                    "api_generator.repair_retry_linter",
                    criterion_id=criterion_id,
                    attempt=attempt,
                    feedback=eval_result.formatted_feedback,
                )
                continue

            # Código completamente limpio y validado: persistir de forma segura
            safe_file_path = self._sanitize_output_path(
                criterion_id=criterion_id,
                slug=slug,
                base_dir=out_dir_resolved,
            )
            safe_file_path.write_text(formatted_code, encoding="utf-8")

            artifact = GeneratedArtifact(
                criterion_id=criterion_id,
                slug=slug,
                target_kind="unit",
                model_tier=model_tier,
                code=formatted_code,
                file_path=safe_file_path,
                status="ok",
                attempts=attempt,
                errors=accumulated_errors,
            )

            return GenerationReport(
                tenant_id=tenant_id,
                spec_path=str(safe_file_path),
                status="ok",
                artifacts=[artifact],
                escalated_criteria=[],
            )

        # Se agotaron los intentos sin éxito
        escalated_path = self._sanitize_output_path(
            criterion_id=criterion_id,
            slug=slug,
            base_dir=out_dir_resolved,
        )
        failed_artifact = GeneratedArtifact(
            criterion_id=criterion_id,
            slug=slug,
            target_kind="unit",
            model_tier=model_tier,
            code="",
            file_path=None,
            status="escalate",
            attempts=max_rounds,
            errors=accumulated_errors,
        )
        return GenerationReport(
            tenant_id=tenant_id,
            spec_path=str(escalated_path),
            status="escalate",
            artifacts=[failed_artifact],
            escalated_criteria=[criterion_id],
        )

    async def generate_api_test(
        self,
        criterion: dict[str, Any],
        endpoint_signature: EndpointSignatureDTO,
        tenant_id: str,
        out_dir: Path,
    ) -> GenerationReport:
        """
        Punto de entrada principal para generar la prueba de integración API asíncrona
        asociada a un criterio Given/When/Then y la firma del endpoint.
        """
        criterion_id = str(criterion.get("id") or "AC-01")
        raw_slug = str(
            criterion.get("slug")
            or criterion.get("file_name")
            or criterion.get("when")
            or endpoint_signature.name
            or "api_test"
        )
        safe_slug = _clean_identifier(raw_slug, "api-endpoint")

        payload: dict[str, Any] = {
            "criterion_id": criterion_id,
            "slug": safe_slug,
            "tenant_id": tenant_id,
            "given": str(criterion.get("given", "")),
            "when": str(criterion.get("when", "")),
            "then": str(criterion.get("then", "")),
            "endpoint_signature": endpoint_signature,
            "context_symbols": [
                {
                    "name": endpoint_signature.name,
                    "signature": endpoint_signature.signature,
                    "file_path": endpoint_signature.file_path,
                }
            ],
            "context_tables": [],
        }

        return await self.generate_with_repair(payload=payload, out_dir=out_dir)
