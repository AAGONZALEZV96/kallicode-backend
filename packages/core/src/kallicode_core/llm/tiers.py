"""Los tres tiers de LLM de Kallicode y la política de escalado.

Categorías (decisión julio-2026):

    FLASH  Razonamiento simple           DeepSeek V4 Flash
           Clasificación, extracción, dedup, resúmenes, boilerplate,
           mensajes al usuario, digestión de resultados. Baja latencia,
           alto volumen, salidas cortas y estructuradas.

    PRO    Razonamiento complejo         DeepSeek V4 Pro
           Diseño de alternativas, planes de cambio multi-archivo,
           diagnóstico de fallos, código complejo. Modo razonamiento
           extendido; mayor latencia y coste por token.

    FABLE  Muy complejo y orquestación   Claude Fable 5
           Revisión adversarial de diseños, explotabilidad de seguridad,
           correlación de anomalías de deploy, decisiones de orquestación
           del pipeline. El tier de máximo criterio: se usa poco y donde
           equivocarse es caro.

Escalado automático (política del §3.1 del documento técnico, extendida):

    flash --> pro --> fable

Un paso escala al siguiente tier cuando ocurre cualquiera de:
    (a) la validación de esquema JSON falla KC_LLM_MAX_REINTENTOS_ESQUEMA
        veces consecutivas en el tier actual;
    (b) el modelo declara confianza < KC_LLM_UMBRAL_CONFIANZA;
    (c) el proveedor no responde (timeout / 5xx) tras un reintento.
Si FABLE también falla, el paso se marca escalado_humano y el orquestador
abre una escalación (§17.12) — la máquina se detiene documentadamente.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import get_settings


class Tier(str, Enum):
    FLASH = "flash"
    PRO = "pro"
    FABLE = "fable"


ORDEN_ESCALADO: list[Tier] = [Tier.FLASH, Tier.PRO, Tier.FABLE]


@dataclass(frozen=True)
class ConfigTier:
    tier: Tier
    modelo: str
    base_url: str
    api_key: str
    protocolo: str            # "openai" (DeepSeek self-hosted) | "anthropic" (Fable)
    max_tokens_salida: int


def config_tier(tier: Tier) -> ConfigTier:
    s = get_settings()
    if tier is Tier.FLASH:
        return ConfigTier(tier, s.llm_flash_model, s.llm_flash_base_url,
                          s.llm_flash_api_key, "openai", 4096)
    if tier is Tier.PRO:
        return ConfigTier(tier, s.llm_pro_model, s.llm_pro_base_url,
                          s.llm_pro_api_key, "openai", 8192)
    return ConfigTier(tier, s.llm_fable_model, s.llm_fable_base_url,
                      s.llm_fable_api_key, "anthropic", 16384)


def siguiente_tier(actual: Tier) -> Tier | None:
    """Tier al que se escala, o None si ya es FABLE (escala a humano)."""
    i = ORDEN_ESCALADO.index(actual)
    return ORDEN_ESCALADO[i + 1] if i + 1 < len(ORDEN_ESCALADO) else None
