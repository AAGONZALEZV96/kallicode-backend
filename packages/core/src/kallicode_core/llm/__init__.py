"""Módulo LLM de Kallicode: tres tiers con escalado automático.

    flash  Razonamiento simple            DeepSeek V4 Flash
    pro    Razonamiento complejo          DeepSeek V4 Pro
    fable  Muy complejo / orquestación    Claude Fable 5

Uso:
    from kallicode_core.llm import ejecutar_paso, Tier
    r = await ejecutar_paso("triage.classify", {"ticket_id": "KC-1045", ...})
    r.salida        # JSON validado contra el esquema del paso
    r.tier, r.modelo, r.tokens_in, r.tokens_out, r.intentos
"""
from .cliente import LLMEscaladoHumano, ResultadoPaso, ejecutar_paso
from .plantillas import PLANTILLAS, plantilla
from .tiers import Tier, config_tier, siguiente_tier

__all__ = ["ejecutar_paso", "ResultadoPaso", "LLMEscaladoHumano",
           "PLANTILLAS", "plantilla", "Tier", "config_tier", "siguiente_tier"]
