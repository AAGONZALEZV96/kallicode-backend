"""
Ticket: KC-QA-B3-02
Suite de pruebas unitarias y adversarias para TestGenerator (gen_tests.py).
Valida generación BDD con trazabilidad @criterio('AC-n') en < 2s, prevención
de Path Traversal (QA-F03), bucle de reparación AST y escalación tras agotar reintentos.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from kallicode_testing.cm_client import CodeMappingClient, SubgraphDTO, SymbolDTO
from kallicode_testing.gen_tests import ASTGuard, TestGenerator, criterio
from kallicode_testing.stubs.llm_stub import LLMStub
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

TENANT_GEN = "tenant_gen_tests_b3_02"


@criterio("AC-00")
def _sample_decorated_test() -> None:
    """Función auxiliar para verificar el atributo inyectado por el decorador @criterio."""


async def test_generacion_criterio_exitoso_con_decorador_trazabilidad(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """
    Prueba 1: Simula criterio BDD AC-01 y verifica que el código generado contenga
    @criterio("AC-01") (o anotación Playwright equivalente) y finalice en < 2 segundos.
    """
    assert getattr(_sample_decorated_test, "__kallicode_criterion__", None) == "AC-00"

    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_GEN)
    llm_stub = LLMStub()
    ast_guard = ASTGuard()
    generator = TestGenerator(cm_client=cm_client, llm_stub=llm_stub, ast_guard=ast_guard)

    context = SubgraphDTO(
        root_symbols=["crear_ticket"],
        symbols=[
            SymbolDTO(
                id="sym_1",
                name="crear_ticket",
                kind="function",
                signature="async def crear_ticket(datos: TicketIn) -> dict",
                file_path="apps/api/routers/tickets.py",
                start_line=10,
                end_line=30,
            )
        ],
    )

    criterion_ac01 = {
        "id": "AC-01",
        "slug": "login_exitoso",
        "given": "un usuario activo registrado en el tenant",
        "when": "envía credenciales válidas al endpoint de login",
        "then": "recibe un token JWT RS256 y código HTTP 200",
    }

    start_ts = time.perf_counter()
    artifact = await generator.generate_single_criterion_test(
        criterion=criterion_ac01,
        context=context,
        target_kind="unit",
    )
    elapsed_s = time.perf_counter() - start_ts

    assert elapsed_s < 2.0, f"La generación tardó {elapsed_s:.3f}s (límite < 2.0s)."
    assert artifact.status == "ok"
    assert artifact.attempts == 1
    assert '@criterio("AC-01")' in artifact.code

    # Verificar también la generación E2E Playwright con anotación de criterio
    e2e_stub = LLMStub()
    e2e_stub.generate_test_code = AsyncMock(
        return_value=json.dumps(
            {
                "tests": [
                    {
                        "name": "test_e2e_ac01",
                        "code": "test('flujo login', async ({ page }) => { await page.goto('/'); });",
                        "type": "e2e",
                    }
                ]
            }
        )
    )
    gen_e2e = TestGenerator(cm_client=cm_client, llm_stub=e2e_stub, ast_guard=ast_guard)
    e2e_artifact = await gen_e2e.generate_single_criterion_test(
        criterion=criterion_ac01,
        context=context,
        target_kind="e2e",
    )
    assert e2e_artifact.status == "ok"
    assert e2e_artifact.model_tier == "pro"
    assert 'description: "AC-01"' in e2e_artifact.code


async def test_prevencion_path_traversal_en_file_name(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """
    Prueba 2: Simula '../../../etc/passwd' como nombre de archivo sugerido en la spec
    y verifica que se ignore la ruta maliciosa y se fuerce el almacenamiento dentro de out_dir.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_GEN)
    llm_stub = LLMStub()
    ast_guard = ASTGuard()
    generator = TestGenerator(cm_client=cm_client, llm_stub=llm_stub, ast_guard=ast_guard)

    out_dir = tmp_path / "generated_suite"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Verificar directamente _sanitize_output_path contra intento de Path Traversal
    sanitized_path = generator._sanitize_output_path(
        criterion_id="../../../AC-02",
        slug="../../../etc/passwd",
        base_dir=out_dir,
        target_kind="unit",
    )
    assert sanitized_path.is_relative_to(out_dir.resolve())
    assert sanitized_path.parent == out_dir.resolve()
    assert ".." not in sanitized_path.name
    assert "passwd" not in sanitized_path.name

    # Verificar flujo completo generate_for_spec con YAML que incluye file_name malicioso
    spec_yaml = tmp_path / "spec_gate1.yaml"
    spec_yaml.write_text(
        """
scope_paths:
  - "apps/api/**"
criterios:
  - id: "AC-02"
    file_name: "../../../../etc/passwd"
    given: "un atacante sugiere un path traversal en el nombre del archivo"
    when: "se ejecuta generate_for_spec"
    then: "el archivo queda estrictamente confinado dentro de out_dir"
""",
        encoding="utf-8",
    )

    report = await generator.generate_for_spec(
        spec_yaml_path=spec_yaml,
        tenant_id=TENANT_GEN,
        out_dir=out_dir,
    )

    assert report.status == "ok"
    assert len(report.artifacts) == 1
    saved_file = report.artifacts[0].file_path
    assert saved_file is not None
    assert saved_file.exists()
    assert saved_file.resolve().parent == out_dir.resolve()
    assert '@criterio("AC-02")' in saved_file.read_text(encoding="utf-8")


async def test_bucle_reparacion_ast_ante_codigo_malformado(
    db_session: AsyncSession,
) -> None:
    """
    Prueba 3: Simula una primera respuesta inválida desde llm_stub.py (SyntaxError),
    valida que ast_guard detecte el problema, confirme el reintento y entregue código válido.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_GEN)
    llm_stub = LLMStub()
    ast_guard = ASTGuard()

    # Primera llamada devuelve error de sintaxis; segunda llamada devuelve código válido
    broken_payload = await llm_stub.generate_test_code(
        prompt="init",
        model="flash",
        simulate_error="syntax_error",
    )
    valid_payload = await llm_stub.generate_test_code(
        prompt="retry",
        model="flash",
        simulate_error="none",
    )
    llm_stub.generate_test_code = AsyncMock(side_effect=[broken_payload, valid_payload])

    generator = TestGenerator(cm_client=cm_client, llm_stub=llm_stub, ast_guard=ast_guard)
    artifact = await generator.generate_single_criterion_test(
        criterion={
            "id": "AC-03",
            "given": "un modelo que falla en el primer intento",
            "when": "ast_guard detecta SyntaxError",
            "then": "reintenta y repara el código en la ronda 2",
        },
        context=SubgraphDTO(),
        target_kind="unit",
    )

    assert artifact.status == "ok"
    assert artifact.attempts == 2
    assert len(artifact.errors) == 1
    assert "SyntaxError" in artifact.errors[0]
    assert '@criterio("AC-03")' in artifact.code

    is_valid, errors = ast_guard.validate(artifact.code, "unit")
    assert is_valid is True
    assert errors == []


async def test_escalacion_por_exceso_de_reintentos_fallidos(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """
    Prueba 4: Simula un modelo que devuelve repetidamente código inválido/irreparable
    (incluyendo imports prohibidos o errores de sintaxis). Verifica que status == 'escalate'
    y que ningún archivo corrupto se guarde en out_dir.
    """
    cm_client = CodeMappingClient(db_session=db_session, tenant_id=TENANT_GEN)
    llm_stub = LLMStub()
    ast_guard = ASTGuard()

    # Intento 1: import prohibido (subprocess); Intento 2: error de sintaxis irreparable
    forbidden_code_json = json.dumps(
        {
            "tests": [
                {
                    "name": "test_malicious",
                    "code": "import subprocess\ndef test_hack():\n    subprocess.run(['id'])\n",
                    "type": "unit",
                }
            ]
        }
    )
    syntax_error_json = await llm_stub.generate_test_code(
        prompt="broken",
        model="flash",
        simulate_error="syntax_error",
    )
    llm_stub.generate_test_code = AsyncMock(side_effect=[forbidden_code_json, syntax_error_json])

    generator = TestGenerator(cm_client=cm_client, llm_stub=llm_stub, ast_guard=ast_guard)

    out_dir = tmp_path / "empty_on_escalate"
    spec_yaml = tmp_path / "spec_irreparable.yaml"
    spec_yaml.write_text(
        """
scope_paths: []
criterios:
  - id: "AC-04"
    slug: "irreparable_case"
    given: "un modelo que produce fallos de AST en los 2 intentos"
    when: "se agotan max_rounds=2"
    then: "escala a revisión humana sin guardar archivos corruptos"
""",
        encoding="utf-8",
    )

    report = await generator.generate_for_spec(
        spec_yaml_path=spec_yaml,
        tenant_id=TENANT_GEN,
        out_dir=out_dir,
    )

    assert report.status == "escalate"
    assert report.escalated_criteria == ["AC-04"]
    assert len(report.artifacts) == 1
    assert report.artifacts[0].status == "escalate"
    assert report.artifacts[0].file_path is None
    assert list(out_dir.iterdir()) == [], "No debe persistirse ningún archivo corrupto en out_dir."
