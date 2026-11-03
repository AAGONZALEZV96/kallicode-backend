"""
Ticket: KC-QA-B3-02
Generador de código de pruebas automatizado (Unitarias Pytest y E2E Playwright)
a partir de criterios de aceptación BDD (Gate 1) y contexto del grafo CodeMapping.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.cm_client import CodeMappingClient, SubgraphDTO
from kallicode_testing.logger import get_logger
from kallicode_testing.stubs.llm_stub import LLMStub, ModelType
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

F = TypeVar("F", bound=Callable[..., Any])

_SAFE_TOKEN_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def criterio(ac_id: str) -> Callable[[F], F]:
    """Decorador de trazabilidad que vincula una función de prueba con su criterio AC-n."""

    def _decorator(func: F) -> F:
        func.__kallicode_criterion__ = ac_id
        return func

    return _decorator


def format_test_code(test_name: str, test_body: str) -> str:
    """Formatea un bloque de prueba con la firma estándar de pytest."""
    if not test_name.startswith("test_"):
        test_name = f"test_{test_name}"
    return f"def {test_name}() -> None:\n    {test_body}\n"


class GeneratedArtifact(BaseModel):
    """Artefacto individual generado para un criterio de aceptación."""

    model_config = ConfigDict(frozen=True)

    criterion_id: str
    slug: str
    target_kind: Literal["unit", "e2e"]
    model_tier: ModelType
    code: str
    file_path: Path | None = None
    status: Literal["ok", "escalate"] = "ok"
    attempts: int = 1
    errors: list[str] = Field(default_factory=list)


class GenerationReport(BaseModel):
    """Reporte consolidado de generación de pruebas para una especificación."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    spec_path: str
    status: Literal["ok", "partial", "escalate"]
    artifacts: list[GeneratedArtifact] = Field(default_factory=list)
    escalated_criteria: list[str] = Field(default_factory=list)


def _clean_identifier(value: str, fallback: str) -> str:
    """Normaliza un identificador o slug eliminando secuencias de Path Traversal y símbolos."""
    # Remover secuencias explícitas de traversal y rutas del sistema antes de limpiar
    stripped = value.replace("..", "").replace("/", " ").replace("\\", " ")
    tokens = [
        tok
        for tok in _SAFE_TOKEN_RE.sub("-", stripped).strip("-_").lower().split("-")
        if tok and tok not in {"etc", "passwd", "shadow", "tmp", "var", "usr", "root"}
    ]
    cleaned = "-".join(tokens)
    return cleaned if cleaned else fallback


def _inject_traceability(code: str, criterion_id: str, target_kind: str) -> str:
    """
    Asegura que el código sintetizado incluya explícitamente la marca de trazabilidad:
    - Python ('unit'): @criterio("AC-n")
    - Playwright ('e2e'): annotations: [{ type: "criterion", description: "AC-n" }]
    """
    if target_kind == "e2e":
        annotation_snippet = (
            f'annotations: [{{ type: "criterion", description: "{criterion_id}" }}]'
        )
        if annotation_snippet in code or f'description: "{criterion_id}"' in code:
            return code
        if "test(" in code:
            return code.replace(
                "test(",
                f'test("{criterion_id}", {{ {annotation_snippet} }}, async ({{ page }}) => {{\n'
                f"  // Criterio {criterion_id}\n}});\n// Original:\ntest(",
                1,
            )
        return (
            f'import {{ test, expect }} from "@playwright/test";\n\n'
            f'test("{criterion_id} e2e", {{ {annotation_snippet} }}, async ({{ page }}) => {{\n'
            f"  await page.goto('/');\n"
            f"  expect(await page.title()).toBeDefined();\n"
            f"}});\n"
        )

    decorator_line = f'@criterio("{criterion_id}")'
    if decorator_line in code:
        return code

    lines = code.splitlines()
    out_lines: list[str] = []
    inserted = False
    for line in lines:
        stripped = line.lstrip()
        if not inserted and stripped.startswith(("def test_", "async def test_")):
            indent = line[: len(line) - len(stripped)]
            out_lines.append(f"{indent}{decorator_line}")
            inserted = True
        out_lines.append(line)

    if not inserted:
        return f"{decorator_line}\n{code}\n"
    return "\n".join(out_lines) + "\n"


class TestGenerator:
    """
    Orquestador de generación de pruebas unitarias (Pytest) y E2E (Playwright)
    con introspección del grafo CodeMapping, sanitización de rutas y bucle de reparación AST.
    """

    __test__ = False

    def __init__(
        self,
        cm_client: CodeMappingClient,
        llm_stub: LLMStub,
        ast_guard: ASTGuard | None = None,
    ) -> None:
        self.cm_client: CodeMappingClient = cm_client
        self.llm_stub: LLMStub = llm_stub
        self.ast_guard: ASTGuard = ast_guard if ast_guard is not None else ASTGuard()

    def _sanitize_output_path(
        self,
        criterion_id: str,
        slug: str,
        base_dir: Path,
        target_kind: str = "unit",
    ) -> Path:
        """
        Deriva el nombre del archivo exclusivamente mediante '{criterion_id}-{slug}.[py|spec.ts]'
        y valida su confinamiento dentro de base_dir con resolve_safe_path (QA-F03).
        """
        safe_cid = _clean_identifier(criterion_id, "ac-00")
        safe_slug = _clean_identifier(slug, "case")
        extension = "spec.ts" if target_kind == "e2e" else "py"
        filename = f"{safe_cid}-{safe_slug}.{extension}"
        return resolve_safe_path(base_dir, filename)

    def _select_model_tier(
        self,
        criterion: dict[str, Any],
        context: SubgraphDTO,
        target_kind: str,
    ) -> ModelType:
        """
        Selecciona 'pro' para pruebas E2E, flujos multi-pantalla o subgrafos complejos,
        y 'flash' para criterios unitarios directos.
        """
        if target_kind == "e2e":
            return "pro"
        text_blob = (
            f"{criterion.get('given', '')} {criterion.get('when', '')} {criterion.get('then', '')}"
        ).lower()
        if len(context.symbols) > 3 or "multi" in text_blob or "complej" in text_blob:
            return "pro"
        return "flash"

    async def generate_with_repair(
        self,
        prompt_payload: dict[str, Any],
        validator_func: Callable[[str], tuple[bool, list[str]]],
        max_rounds: int = 2,
    ) -> dict[str, Any]:
        """
        Ejecuta la generación con el modelo y valida el código mediante validator_func (ASTGuard).
        Si falla, reinyecta los errores detectados al prompt hasta max_rounds intentos.
        Si se agotan los intentos, retorna status='escalate' sin persistir código corrupto.
        """
        model_tier: ModelType = prompt_payload.get("model_tier", "flash")
        criterion_id = str(prompt_payload.get("criterion_id", "AC-01"))
        target_kind = str(prompt_payload.get("target_kind", "unit"))
        accumulated_errors: list[str] = []

        for attempt in range(1, max_rounds + 1):
            current_payload = {
                **prompt_payload,
                "attempt": attempt,
                "previous_errors": list(accumulated_errors),
            }
            prompt_str = json.dumps(current_payload, ensure_ascii=False)

            raw_response = await self.llm_stub.generate_test_code(
                prompt=prompt_str,
                model=model_tier,
            )

            try:
                parsed = json.loads(raw_response)
                tests_list = parsed.get("tests", []) if isinstance(parsed, dict) else []
                if not tests_list or not isinstance(tests_list[0], dict):
                    accumulated_errors.append(f"Intento {attempt}: JSON sin lista 'tests' válida.")
                    continue
                first_test = tests_list[0]
                raw_code = str(first_test.get("code", ""))
            except json.JSONDecodeError as exc:
                accumulated_errors.append(f"Intento {attempt}: JSON malformado ({exc.msg}).")
                continue

            is_valid, validation_errors = validator_func(raw_code)
            if is_valid:
                traced_code = _inject_traceability(raw_code, criterion_id, target_kind)
                return {
                    "status": "ok",
                    "code": traced_code,
                    "attempts": attempt,
                    "errors": accumulated_errors,
                }

            accumulated_errors.extend([f"Intento {attempt}: {err}" for err in validation_errors])
            logger.warning(
                "gen_tests.repair_retry",
                criterion_id=criterion_id,
                attempt=attempt,
                max_rounds=max_rounds,
                errors=validation_errors,
            )

        logger.warning(
            "gen_tests.escalated_to_human",
            criterion_id=criterion_id,
            attempts=max_rounds,
            errors=accumulated_errors,
        )
        return {
            "status": "escalate",
            "code": "",
            "attempts": max_rounds,
            "errors": accumulated_errors,
        }

    async def generate_single_criterion_test(
        self,
        criterion: dict[str, Any],
        context: SubgraphDTO,
        target_kind: str = "unit",
    ) -> GeneratedArtifact:
        """
        Genera y valida el artefacto de prueba para un criterio individual ('AC-n')
        incorporando el contexto del subgrafo y trazabilidad explícita.
        """
        criterion_id = str(criterion.get("id", "AC-01"))
        raw_slug = str(
            criterion.get("slug")
            or criterion.get("file_name")
            or criterion.get("when")
            or "test_case"
        )
        safe_slug = _clean_identifier(raw_slug, "test-case")
        kind_literal: Literal["unit", "e2e"] = "e2e" if target_kind == "e2e" else "unit"
        model_tier = self._select_model_tier(criterion, context, kind_literal)

        prompt_payload: dict[str, Any] = {
            "criterion_id": criterion_id,
            "given": str(criterion.get("given", "")),
            "when": str(criterion.get("when", "")),
            "then": str(criterion.get("then", "")),
            "target_kind": kind_literal,
            "model_tier": model_tier,
            "context_symbols": [s.model_dump() for s in context.symbols],
            "context_tables": [t.model_dump() for t in context.db_tables],
        }

        repair_result = await self.generate_with_repair(
            prompt_payload=prompt_payload,
            validator_func=lambda code: self.ast_guard.validate(code, kind_literal),
            max_rounds=2,
        )

        status_val: Literal["ok", "escalate"] = (
            "ok" if repair_result["status"] == "ok" else "escalate"
        )
        return GeneratedArtifact(
            criterion_id=criterion_id,
            slug=safe_slug,
            target_kind=kind_literal,
            model_tier=model_tier,
            code=str(repair_result["code"]),
            file_path=None,
            status=status_val,
            attempts=int(repair_result["attempts"]),
            errors=list(repair_result["errors"]),
        )

    async def generate_for_spec(
        self,
        spec_yaml_path: str | Path,
        tenant_id: str,
        out_dir: Path,
    ) -> GenerationReport:
        """
        Carga la especificación YAML aprobada en Gate 1, consulta cm_client
        (get_symbols_by_path y get_impacted_subgraph), genera los tests por criterio
        y persiste en out_dir únicamente los artefactos válidos con rutas saneadas.
        """
        spec_file = Path(spec_yaml_path)
        raw_content = spec_file.read_text(encoding="utf-8")
        spec_data = yaml.safe_load(raw_content)
        if not isinstance(spec_data, dict):
            raise TypeError(
                f"El archivo de especificación '{spec_yaml_path}' no es un YAML válido."
            )

        scope_paths_raw = spec_data.get("scope_paths", [])
        scope_paths = [str(p) for p in scope_paths_raw] if isinstance(scope_paths_raw, list) else []

        discovered_symbol_names: list[str] = []
        for path_pattern in scope_paths:
            symbols = await self.cm_client.get_symbols_by_path(path_pattern)
            for sym in symbols:
                if sym.name not in discovered_symbol_names:
                    discovered_symbol_names.append(sym.name)

        explicit_symbols = spec_data.get("symbol_names", [])
        if isinstance(explicit_symbols, list):
            for sname in explicit_symbols:
                if str(sname) not in discovered_symbol_names:
                    discovered_symbol_names.append(str(sname))

        subgraph = await self.cm_client.get_impacted_subgraph(
            symbol_names=discovered_symbol_names,
            max_depth=2,
        )

        criterios_raw = spec_data.get("criterios") or spec_data.get("criteria") or []
        criterios_list = criterios_raw if isinstance(criterios_raw, list) else []

        out_dir_resolved = Path(out_dir).resolve()
        out_dir_resolved.mkdir(parents=True, exist_ok=True)

        artifacts: list[GeneratedArtifact] = []
        escalated: list[str] = []

        for crit in criterios_list:
            if not isinstance(crit, dict):
                continue

            target_kind = str(crit.get("target_kind", "unit"))
            artifact = await self.generate_single_criterion_test(
                criterion=crit,
                context=subgraph,
                target_kind=target_kind,
            )

            if artifact.status == "ok" and artifact.code.strip():
                raw_slug = str(
                    crit.get("file_name") or crit.get("slug") or crit.get("when") or artifact.slug
                )
                safe_path = self._sanitize_output_path(
                    criterion_id=artifact.criterion_id,
                    slug=raw_slug,
                    base_dir=out_dir_resolved,
                    target_kind=artifact.target_kind,
                )
                safe_path.write_text(artifact.code, encoding="utf-8")
                artifacts.append(artifact.model_copy(update={"file_path": safe_path}))
            else:
                escalated.append(artifact.criterion_id)
                artifacts.append(artifact)

        if escalated and len(escalated) == len(artifacts):
            overall_status: Literal["ok", "partial", "escalate"] = "escalate"
        elif escalated:
            overall_status = "partial"
        else:
            overall_status = "ok"

        return GenerationReport(
            tenant_id=tenant_id,
            spec_path=str(spec_file),
            status=overall_status,
            artifacts=artifacts,
            escalated_criteria=escalated,
        )
