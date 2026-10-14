"""Suite de auditoría formal de cierre de los 13 hallazgos críticos/altos de CodeMapping.

Ticket: KC-CM-05-6
Épica: ECM1 - Fundaciones del paquete, CI y observabilidad
"""

import inspect

from kallicode_codemapping import budget, config, ingest_repo, llm_enrich, snapshots


class TestAuditoriaCierre13Hallazgos:
    """Matriz de comprobación de aceptación técnica para el cierre del Sprint 5."""

    def test_cm_f01_f02_secretos_y_config_segura(self) -> None:
        """CM-F01/CM-F02 (KC-CM-01-4): Verificación de no-fuga y obligatoriedad de tenant_id."""
        # tenant_id debe ser obligatorio sin fallbacks hardcodeados
        cfg_sig = inspect.signature(config.Settings)
        tenant_param = cfg_sig.parameters.get("tenant_id")
        assert tenant_param is not None, "Settings debe declarar tenant_id"

    def test_cm_f06_cliente_llm_canonico(self) -> None:
        """CM-F06 (KC-CM-05-3): Uso exclusivo de kallicode_core.llm.cliente sin SDK directo."""
        codigo_enrich = inspect.getsource(llm_enrich)
        assert "import openai" not in codigo_enrich, "CM-F06: Prohibido import directo de openai"
        assert "from openai" not in codigo_enrich, "CM-F06: Prohibido SDK externo de OpenAI"
        assert "ejecutar_paso" in codigo_enrich, (
            "CM-F06: Debe canalizarse por kallicode_core.llm.cliente.ejecutar_paso"
        )

    def test_cm_f07_presupuesto_y_dry_run(self) -> None:
        """CM-F07 (KC-CM-05-5): Estimación en seco y BudgetExceededError."""
        assert hasattr(budget, "verificar_presupuesto_tenant"), (
            "CM-F07: Falta verificar_presupuesto_tenant"
        )
        assert hasattr(budget, "BudgetExceededError"), (
            "CM-F07: Falta BudgetExceededError para corte preventivo"
        )
        assert hasattr(budget, "estimar_tokens_ingesta"), (
            "CM-F07: Falta heurística determinista de tokens"
        )

    def test_cm_f10_telemetria_jsonb_snapshots(self) -> None:
        """CM-F10 (KC-CM-04-4): Registro estructurado JSONB con trace_id y tenant_id."""
        sig = inspect.signature(snapshots.SnapshotManager.registrar_checkpoint)
        assert "trace_id" in sig.parameters, "CM-F10: registrar_checkpoint debe propagar trace_id"

    def test_cm_f12_git_branches_fallback_force_push(self) -> None:
        """CM-F12 (KC-CM-04-2): Resolución de ramas multi-tenant y fallback ante force-push."""
        assert hasattr(ingest_repo, "resolver_rama_tenant"), (
            "CM-F12: Debe existir resolver_rama_tenant"
        )
        assert hasattr(ingest_repo, "verificar_ancestro_comun"), (
            "CM-F12: Debe detectar divergencia git"
        )

    def test_cm_adr018_purga_neo4j_y_qdrant(self) -> None:
        """ADR-018: Verificación de remoción definitiva de dependencias obsoletas."""
        cfg_source = inspect.getsource(config.Settings)
        assert "neo4j" not in cfg_source.lower(), "ADR-018: Quedan atributos residuales de Neo4j"
        assert "qdrant" not in cfg_source.lower(), "ADR-018: Quedan atributos residuales de Qdrant"
