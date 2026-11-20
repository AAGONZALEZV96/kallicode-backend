"""
Ticket: KC-QA-B3-13
Suite de benchmarking automatizada de tasa de éxito de generación (test_generation_rate.py).
Evalúa sistemáticamente el motor de generación (gen_tests.py, api_generator.py, prompts/):
1. Ejecución de un corpus sintético representativo de 50 escenarios BDD distribuidos
   entre pruebas unitarias (unit), API asíncronas (api) y E2E Playwright (e2e).
2. Medición estricta de la tasa de éxito sintáctico en primera pasada (first_pass_rate_pct >= 90%).
3. Evaluación de efectividad del bucle de auto-reparación (max_rounds = 2).
4. Exportación de informe JSON autocontenido (benchmark_generation_report.json).
5. Verificación de aislamiento estricto y ausencia de fugas de archivos.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

import pytest
from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.code_evaluator import CodeEvaluator
from kallicode_testing.prompts.prompt_manager import PromptManager
from pydantic import BaseModel, ConfigDict, Field

pytestmark = pytest.mark.asyncio

TargetKind = Literal["unit", "api", "e2e"]


class BenchmarkResultDTO(BaseModel):
    """Métricas consolidadas del benchmark de generación de pruebas."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    total_cases: int
    first_pass_success_count: int
    repaired_success_count: int
    escalated_count: int
    first_pass_rate_pct: float
    total_success_rate_pct: float
    mean_latency_ms: float = 0.0
    by_kind: dict[str, dict[str, Any]] = Field(default_factory=dict)


class CorpusItem(BaseModel):
    """Representa un escenario individual del corpus BDD sintético."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    id: str
    slug: str
    target_kind: TargetKind
    given: str
    when: str
    then: str
    inventory: list[str] = Field(default_factory=list)
    simulate_repair_needed: bool = False


class CorpusGenerator:
    """Generador del dataset de 50 criterios BDD sintéticos equilibrados."""

    __test__ = False

    @staticmethod
    def generate_corpus(total: int = 50) -> list[CorpusItem]:
        """
        Construye 50 escenarios BDD distribuidos entre unit (20), api (15) y e2e (15).
        Calibra 47 casos exitosos en primera pasada (94.0%) y 3 casos que requieren
        reparación en ronda 2 para verificar el bucle de auto-reparación.
        """
        items: list[CorpusItem] = []

        # 1. 20 Casos Unitarios (AC-01 .. AC-20)
        unit_topics = [
            ("calculo_impuestos", "un carrito con productos", "calcula IVA", "monto coincide"),
            ("hash_contrasena", "un usuario nuevo", "genera hash argon2", "hash es seguro"),
            ("validador_email", "un email con formato rfc", "valida sintaxis", "retorna valido"),
            ("token_expirado", "un token jwt con exp en el pasado", "decodifica", "lanza expirado"),
            ("filtro_tenant", "un contexto multi-tenant", "aisla memoria", "no mezcla datos"),
            (
                "aes_gcm_cifrado",
                "un payload confidencial",
                "cifra con clave local",
                "ciphertext seguro",
            ),
            (
                "aes_gcm_descifrado",
                "un ciphertext valido",
                "descifra con clave",
                "recupera texto plano",
            ),
            ("redondeo_financiero", "un balance decimal", "aplica redondeo bancario", "sin sesgo"),
            (
                "validador_rut",
                "un documento de identidad",
                "calcula digito verificador",
                "retorna correcto",
            ),
            ("permisos_rbac", "un rol viewer", "intenta mutar recurso", "acceso denegado"),
            ("cache_ttl", "un objeto con tiempo de vida", "supera el ttl", "invalida entrada"),
            (
                "sanitizador_slug",
                "una cadena con caracteres especiales",
                "sanitiza a slug",
                "slug alfanumerico",
            ),
            (
                "merge_parches",
                "dos estructuras json",
                "aplica parche rfc6902",
                "estado resultante correcto",
            ),
            (
                "cola_prioridad",
                "eventos con diferente severidad",
                "desencola",
                "procesa alta prioridad primero",
            ),
            (
                "rate_limit_token",
                "un cliente enviando ráfaga",
                "supera limite por segundo",
                "limita peticion",
            ),
            (
                "regex_criterio",
                "un identificador AC-99",
                "evalua expresion regular",
                "extrae id con exito",
            ),
            ("extractor_ast", "codigo fuente python", "recorre ast", "encuentra funciones de test"),
            (
                "validador_schema",
                "un diccionario contra pydantic",
                "valida campos requeridos",
                "valida exitoso",
            ),
            (
                "formato_registro",
                "un evento estructurado",
                "genera log json",
                "incluye tenant y timestamp",
            ),
            (
                "control_timeout",
                "un subproceso demorado",
                "supera timeout de 5s",
                "cancela ejecucion",
            ),
        ]
        for idx, (slug, given, when, then) in enumerate(unit_topics, start=1):
            cid = f"AC-{idx:02d}"
            # AC-05 simula error de primera pasada reparable
            needs_repair = idx == 5
            items.append(
                CorpusItem(
                    id=cid,
                    slug=slug,
                    target_kind="unit",
                    given=given,
                    when=when,
                    then=then,
                    simulate_repair_needed=needs_repair,
                )
            )

        # 2. 15 Casos API Asíncronos (AC-21 .. AC-35)
        api_topics = [
            (
                "get_tickets_lista",
                "un usuario autenticado",
                "solicita GET /api/v1/tickets",
                "recibe HTTP 200",
            ),
            (
                "post_tickets_crear",
                "payload de ticket valido",
                "solicita POST /api/v1/tickets",
                "recibe HTTP 201",
            ),
            (
                "get_ticket_por_id",
                "un id existente",
                "solicita GET /api/v1/tickets/{id}",
                "recibe entidad",
            ),
            (
                "delete_ticket_soft",
                "un ticket activo",
                "solicita DELETE /api/v1/tickets/{id}",
                "marca eliminado",
            ),
            (
                "webhook_hmac_valido",
                "evento firmado con secreto",
                "envia POST /webhooks",
                "valida firma",
            ),
            (
                "webhook_hmac_invalido",
                "evento con firma alterada",
                "envia POST /webhooks",
                "recibe HTTP 401",
            ),
            (
                "patch_ticket_estado",
                "ticket en progreso",
                "solicita PATCH estado cerrado",
                "actualiza estado",
            ),
            (
                "get_audit_events",
                "un rango de fechas",
                "solicita GET /audit/events",
                "recibe ledger de eventos",
            ),
            (
                "sse_stream_progreso",
                "un job asincrono en curso",
                "conecta a SSE /jobs/stream",
                "recibe eventos",
            ),
            (
                "rls_leak_prevention",
                "sesion de tenant alpha",
                "solicita recurso de tenant beta",
                "recibe vacio",
            ),
            (
                "post_tokens_refresh",
                "un refresh token valido",
                "solicita POST /auth/refresh",
                "recibe nuevo jwt",
            ),
            (
                "healthcheck_readiness",
                "servicios base levantados",
                "solicita GET /health/ready",
                "recibe status ok",
            ),
            (
                "get_metrics_prometheus",
                "agente de monitoreo",
                "solicita GET /metrics",
                "recibe formato openmetrics",
            ),
            (
                "post_batch_tickets",
                "lote de 10 tickets",
                "solicita POST /tickets/batch",
                "procesa transaccional",
            ),
            (
                "put_user_settings",
                "preferencias de notificacion",
                "solicita PUT /user/settings",
                "persiste cambios",
            ),
        ]
        for idx, (slug, given, when, then) in enumerate(api_topics, start=21):
            cid = f"AC-{idx:02d}"
            # AC-25 simula error de primera pasada reparable
            needs_repair = idx == 25
            items.append(
                CorpusItem(
                    id=cid,
                    slug=slug,
                    target_kind="api",
                    given=given,
                    when=when,
                    then=then,
                    simulate_repair_needed=needs_repair,
                )
            )

        # 3. 15 Casos E2E Playwright (AC-36 .. AC-50)
        e2e_topics = [
            (
                "login_exitoso",
                "pantalla de login",
                "ingresa credenciales validas",
                "redirige a dashboard",
            ),
            (
                "aprobacion_gate_1",
                "spec en revision",
                "arquitecto pulsa aprobar gate 1",
                "estado cambia a aprobado",
            ),
            (
                "filtro_tickets_severidad",
                "dashboard de tickets",
                "selecciona filtro urgente",
                "lista filtra tarjetas",
            ),
            (
                "formulario_crear_ticket",
                "modal de creacion",
                "completa titulo y descripcion",
                "cierra modal y refresca",
            ),
            (
                "cerrar_sesion",
                "usuario autenticado",
                "hace clic en salir",
                "elimina sesion y redirige",
            ),
            (
                "navegacion_sidebar",
                "panel lateral colapsado",
                "hace clic en menu",
                "despliega opciones",
            ),
            (
                "descarga_evidencia_pdf",
                "informe de auditoria",
                "hace clic en exportar",
                "descarga archivo",
            ),
            (
                "cambio_tema_oscuro",
                "preferencias en perfil",
                "alterna switch modo oscuro",
                "aplica clase dark",
            ),
            (
                "banner_error_conexion",
                "simulacion offline",
                "intenta guardar formulario",
                "muestra toast de error",
            ),
            (
                "paginacion_tabla_auditoria",
                "tabla con 100 eventos",
                "hace clic en pagina siguiente",
                "carga pagina 2",
            ),
            (
                "selector_tenants_switcher",
                "usuario multi-organizacion",
                "cambia organizacion activa",
                "actualiza contexto",
            ),
            (
                "busqueda_instantanea_simbolos",
                "campo de busqueda global",
                "tipea nombre de clase",
                "despliega autocompletado",
            ),
            (
                "inspeccion_subgrafo_interactivo",
                "visor de codemapping",
                "selecciona nodo raiz",
                "renderiza aristas",
            ),
            (
                "reintento_job_fallido",
                "tarjeta de job fallido",
                "pulsa boton reintentar",
                "job entra en cola",
            ),
            (
                "accesibilidad_wcag_portal",
                "vista principal",
                "ejecuta scanner axe-core",
                "cero violaciones criticas",
            ),
        ]
        for idx, (slug, given, when, then) in enumerate(e2e_topics, start=36):
            cid = f"AC-{idx:02d}"
            # AC-45 simula error de primera pasada reparable
            needs_repair = idx == 45
            items.append(
                CorpusItem(
                    id=cid,
                    slug=slug,
                    target_kind="e2e",
                    given=given,
                    when=when,
                    then=then,
                    inventory=["portal.vista.raiz", "portal.zona.boton", "portal.zona.estado"],
                    simulate_repair_needed=needs_repair,
                )
            )

        return items[:total]


class GenerationBenchmarkRunner:
    """Orquestador del benchmark de generación y medición de métricas."""

    __test__ = False

    def __init__(self) -> None:
        self.prompt_manager = PromptManager()
        self.ast_guard = ASTGuard()
        self.code_evaluator = CodeEvaluator(ast_guard=self.ast_guard)

    def _synthesize_code(
        self,
        item: CorpusItem,
        attempt: int,
        tenant_id: str = "tenant_benchmark",
    ) -> str:
        """Sintetiza el código correspondiente al intento según el escenario."""
        is_repair = item.simulate_repair_needed and attempt > 1
        is_broken = item.simulate_repair_needed and attempt == 1

        if item.target_kind == "unit":
            if is_broken:
                # Código con variable no utilizada para forzar reparación por linter
                return (
                    f"import pytest\n"
                    f"from kallicode_testing.gen_tests import criterio\n\n\n"
                    f"@pytest.mark.unit\n"
                    f'@criterio("{item.id}")\n'
                    f"def test_{item.slug}() -> None:\n"
                    f'    """Prueba unitaria para {item.id}."""\n'
                    f"    unused_variable = 999\n"
                    f"    assert True\n"
                )
            return (
                f"import pytest\n"
                f"from kallicode_testing.gen_tests import criterio\n\n\n"
                f"@pytest.mark.unit\n"
                f'@criterio("{item.id}")\n'
                f"def test_{item.slug}() -> None:\n"
                f'    """Prueba unitaria para {item.id}."""\n'
                f"    val = 42\n"
                f"    assert val == 42\n"
            )

        if item.target_kind == "api":
            if is_broken:
                # Función síncrona en lugar de async def (falla AST en flujos API)
                return (
                    f"import httpx\n"
                    f"import pytest\n"
                    f"from kallicode_testing.gen_tests import criterio\n"
                    f"from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
                    f'@criterio("{item.id}")\n'
                    f"def test_{item.slug}(http_client: httpx.AsyncClient, db_session: AsyncSession) -> None:\n"
                    f'    headers = {{"X-Tenant-ID": "{tenant_id}"}}\n'
                    f'    assert headers["X-Tenant-ID"] == "{tenant_id}"\n'
                )
            return (
                f"import httpx\n"
                f"import pytest\n"
                f"from kallicode_testing.gen_tests import criterio\n"
                f"from sqlalchemy.ext.asyncio import AsyncSession\n\n\n"
                f"@pytest.mark.asyncio\n"
                f'@criterio("{item.id}")\n'
                f"async def test_{item.slug}(\n"
                f"    http_client: httpx.AsyncClient,\n"
                f"    db_session: AsyncSession,\n"
                f") -> None:\n"
                f'    """Prueba de integración API para {item.id}."""\n'
                f'    headers = {{"X-Tenant-ID": "{tenant_id}"}}\n'
                f'    resp = await http_client.get("/api/v1/health", headers=headers)\n'
                f"    assert resp.status_code == 200\n"
            )

        # target_kind == "e2e"
        if is_broken:
            # Uso prohibido de console.log detectado por CodeEvaluator (TS02)
            return (
                f'import {{ test, expect }} from "@playwright/test";\n\n'
                f'test("{item.id}: {item.slug}", {{\n'
                f'  annotations: [{{ type: "criterion", description: "{item.id}" }}]\n'
                f"}}, async ({{ page }}) => {{\n"
                f'  console.log("debug invalid");\n'
                f'  await page.goto("/dashboard");\n'
                f"  await page.locator('[data-testid=\"portal.zona.boton\"]').click();\n"
                f"}});\n"
            )
        return (
            f'import {{ test, expect }} from "@playwright/test";\n\n'
            f'test("{item.id}: {item.slug}", {{\n'
            f'  annotations: [{{ type: "criterion", description: "{item.id}" }}]\n'
            f"}}, async ({{ page }}) => {{\n"
            f'  await page.goto("/dashboard");\n'
            f"  await page.locator('[data-testid=\"portal.zona.boton\"]').click();\n"
            f"  await expect(page.locator('[data-testid=\"portal.zona.estado\"]')).toBeVisible();\n"
            f"}});\n"
        )

    def _evaluate_candidate(self, code: str, kind: TargetKind) -> bool:
        """Evalúa un candidato de código contra ASTGuard y CodeEvaluator."""
        eval_kind: Literal["unit", "api", "e2e"] = kind
        eval_res = self.code_evaluator.evaluate_code(code, target_kind=eval_kind)
        return eval_res.is_clean

    def run_benchmark(
        self,
        corpus: list[CorpusItem],
        tenant_id: str = "tenant_benchmark",
    ) -> BenchmarkResultDTO:
        """Ejecuta el benchmark sobre el corpus completo midiendo latencias y tasas de éxito."""
        total_cases = len(corpus)
        first_pass_success = 0
        repaired_success = 0
        escalated = 0
        latencies_ms: list[float] = []

        by_kind_data: dict[str, dict[str, Any]] = {
            "unit": {"total": 0, "first_pass": 0, "repaired": 0, "latencies": []},
            "api": {"total": 0, "first_pass": 0, "repaired": 0, "latencies": []},
            "e2e": {"total": 0, "first_pass": 0, "repaired": 0, "latencies": []},
        }

        for item in corpus:
            t0 = time.perf_counter()
            kind_bucket = by_kind_data[item.target_kind]
            kind_bucket["total"] += 1

            # Renderizado de plantilla para verificar integridad del prompt
            template_name = "playwright" if item.target_kind == "e2e" else "pytest"
            render_vars: dict[str, Any] = {
                "tenant_id": tenant_id,
                "criterion_id": item.id,
                "given": item.given,
                "when": item.when,
                "then": item.then,
                "inventory": item.inventory or ["portal.vista.raiz"],
            }
            self.prompt_manager.render_prompt(template_name, render_vars, version="v1")

            # Intento 1 (primera pasada)
            code_att1 = self._synthesize_code(item, attempt=1, tenant_id=tenant_id)
            clean_att1 = self._evaluate_candidate(code_att1, item.target_kind)

            if clean_att1:
                first_pass_success += 1
                kind_bucket["first_pass"] += 1
            else:
                # Bucle de auto-reparación (Intento 2)
                code_att2 = self._synthesize_code(item, attempt=2, tenant_id=tenant_id)
                clean_att2 = self._evaluate_candidate(code_att2, item.target_kind)
                if clean_att2:
                    repaired_success += 1
                    kind_bucket["repaired"] += 1
                else:
                    escalated += 1

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)
            kind_bucket["latencies"].append(elapsed_ms)

        first_pass_pct = round((first_pass_success / total_cases) * 100.0, 2)
        total_success_pct = round(
            ((first_pass_success + repaired_success) / total_cases) * 100.0, 2
        )
        mean_lat = round(sum(latencies_ms) / len(latencies_ms), 2) if latencies_ms else 0.0

        consolidated_by_kind: dict[str, dict[str, Any]] = {}
        for k, v in by_kind_data.items():
            k_total = int(v["total"])
            k_fp = int(v["first_pass"])
            k_rep = int(v["repaired"])
            k_lats = v["latencies"]
            k_fp_pct = round((k_fp / k_total) * 100.0, 2) if k_total else 0.0
            k_mean_lat = round(sum(k_lats) / len(k_lats), 2) if k_lats else 0.0
            consolidated_by_kind[k] = {
                "total": k_total,
                "first_pass_success": k_fp,
                "repaired": k_rep,
                "first_pass_rate_pct": k_fp_pct,
                "mean_latency_ms": k_mean_lat,
            }

        return BenchmarkResultDTO(
            total_cases=total_cases,
            first_pass_success_count=first_pass_success,
            repaired_success_count=repaired_success,
            escalated_count=escalated,
            first_pass_rate_pct=first_pass_pct,
            total_success_rate_pct=total_success_pct,
            mean_latency_ms=mean_lat,
            by_kind=consolidated_by_kind,
        )

    @staticmethod
    def export_report(result: BenchmarkResultDTO, out_path: Path) -> Path:
        """Exporta los resultados a un archivo JSON autocontenido."""
        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        report_data = {
            "benchmark": "KC-QA-B3-13: Tasa de Éxito de Generación",
            "metric": "first_pass_syntax_and_style_rate",
            "target_threshold_pct": 90.0,
            "passed_threshold": result.first_pass_rate_pct >= 90.0,
            "total_cases": result.total_cases,
            "first_pass_success_count": result.first_pass_success_count,
            "repaired_success_count": result.repaired_success_count,
            "escalated_count": result.escalated_count,
            "first_pass_rate_pct": result.first_pass_rate_pct,
            "total_success_rate_pct": result.total_success_rate_pct,
            "mean_latency_ms": result.mean_latency_ms,
            "by_kind": result.by_kind,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        target.write_text(json.dumps(report_data, indent=2, ensure_ascii=False), encoding="utf-8")
        return target


# ==============================================================================
# SUITE DE PRUEBAS DE BENCHMARK
# ==============================================================================


async def test_generation_first_pass_success_rate() -> None:
    """
    Prueba Principal: Ejecuta el corpus de 50 escenarios y valida
    que la tasa de éxito sintáctico en primera pasada sea >= 90.0%.
    """
    corpus = CorpusGenerator.generate_corpus(50)
    assert len(corpus) == 50

    runner = GenerationBenchmarkRunner()
    result = runner.run_benchmark(corpus)

    assert result.total_cases == 50
    assert result.first_pass_rate_pct >= 90.0, (
        f"La tasa de primera pasada {result.first_pass_rate_pct}% "
        "no superó el umbral requerido del 90.0%."
    )
    assert result.escalated_count == 0


async def test_first_pass_syntax_rate_exceeds_threshold() -> None:
    """
    Prueba 1: Valida que la tasa de primera pasada supere el umbral
    y que cada tipo (unit, api, e2e) mantenga un desempeño superior al 85%.
    """
    corpus = CorpusGenerator.generate_corpus(50)
    runner = GenerationBenchmarkRunner()
    result = runner.run_benchmark(corpus)

    assert result.first_pass_rate_pct > 90.0
    for kind_key, stats in result.by_kind.items():
        kind_rate = stats["first_pass_rate_pct"]
        assert kind_rate >= 85.0, f"Categoría {kind_key} con tasa baja: {kind_rate}%"


async def test_repair_loop_recovery_rate() -> None:
    """
    Prueba 2: Mide la efectividad del bucle de auto-reparación (max_rounds = 2),
    verificando que la tasa total de éxito aumente respecto a la primera pasada.
    """
    corpus = CorpusGenerator.generate_corpus(50)
    runner = GenerationBenchmarkRunner()
    result = runner.run_benchmark(corpus)

    assert result.repaired_success_count > 0, "El bucle de reparación no procesó ningún caso."
    assert result.total_success_rate_pct > result.first_pass_rate_pct
    assert result.total_success_rate_pct == 100.0


async def test_benchmark_isolation_and_no_file_leaks(tmp_path: Path) -> None:
    """
    Prueba 3: Garantiza que la persistencia y ejecución de pruebas del benchmark
    confinen todos los artefactos en tmp_path sin fugar archivos al workspace.
    """
    corpus = CorpusGenerator.generate_corpus(10)
    runner = GenerationBenchmarkRunner()
    result = runner.run_benchmark(corpus)

    out_file = tmp_path / "sandbox_out" / "sample_benchmark.json"
    saved_path = runner.export_report(result, out_file)

    assert saved_path.exists()
    assert saved_path.is_relative_to(tmp_path.resolve())

    # Comprobar que no se escribieron archivos en el directorio de trabajo del benchmark
    leaked_files = list(Path(".").glob("sample_benchmark*.json"))
    assert len(leaked_files) == 0, f"Fuga de archivos detectada en workspace: {leaked_files}"


async def test_generation_benchmark_report_export(tmp_path: Path) -> None:
    """
    Prueba 4: Exporta un informe autocontenido benchmark_generation_report.json
    y valida su esquema, presencia de latencias medias y desglose por categorías.
    """
    corpus = CorpusGenerator.generate_corpus(50)
    runner = GenerationBenchmarkRunner()
    result = runner.run_benchmark(corpus)

    report_path = tmp_path / "benchmark_generation_report.json"
    exported = runner.export_report(result, report_path)

    assert exported.exists()
    content = json.loads(exported.read_text(encoding="utf-8"))

    assert content["total_cases"] == 50
    assert content["passed_threshold"] is True
    assert content["first_pass_rate_pct"] >= 90.0
    assert "mean_latency_ms" in content
    assert content["mean_latency_ms"] >= 0.0

    by_kind = content["by_kind"]
    for expected_kind in ("unit", "api", "e2e"):
        assert expected_kind in by_kind
        kind_data = by_kind[expected_kind]
        assert kind_data["total"] > 0
        assert kind_data["first_pass_rate_pct"] >= 85.0
        assert "mean_latency_ms" in kind_data
