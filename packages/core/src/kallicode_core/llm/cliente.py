"""Cliente LLM con escalado automático y registro completo de cada llamada.

Flujo de `ejecutar_paso`:

    1. Resuelve la plantilla del catálogo (plantillas.py) y su tier inicial
       (el orquestador puede subirlo con `tier_minimo`, p. ej. por la
       complejidad estimada en Triage).
    2. Llama al modelo del tier (DeepSeek OpenAI-compatible o Claude Fable 5
       vía API de Anthropic) con response-format JSON.
    3. Valida la salida contra el JSON Schema de la plantilla.
       - inválida: reintenta en el mismo tier con el error como feedback
         (hasta KC_LLM_MAX_REINTENTOS_ESQUEMA); si persiste, ESCALA de tier.
       - confianza < umbral: escala de tier directamente.
       - timeout/5xx: un reintento y escala.
    4. Registra la transacción en el log central y devuelve ResultadoPaso;
       si el job está en base, el orquestador persiste el paso vía
       POST /internal/v1/jobs/{id}/steps (tabla core.llm_steps + auditoría).
    5. Si FABLE también falla → LLMEscaladoHumano (el llamador abre la
       escalación §17.12).

Cada llamada queda en el log central con: paso, tier, modelo, intento,
duracion_ms, tokens_in/out, validacion y (si aplica) el motivo de escalado.
Los payloads completos NUNCA van al log: van a Blob (refs entrada/salida).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import jsonschema

from ..config import get_settings
from ..logging import Cronometro, log
from .plantillas import plantilla
from .tiers import Tier, config_tier, siguiente_tier


class LLMEscaladoHumano(Exception):
    """Todos los tiers fallaron: el paso requiere intervención humana."""

    def __init__(self, paso_id: str, motivo: str, intentos: list[dict]):
        self.paso_id = paso_id
        self.motivo = motivo
        self.intentos = intentos
        super().__init__(f"{paso_id}: escalado a humano ({motivo})")


@dataclass
class ResultadoPaso:
    """Resultado documentado de un paso LLM.

    Campos:
        paso_id / version: identidad de la plantilla ejecutada.
        tier / modelo: tier y modelo EFECTIVOS (tras escalados).
        salida: JSON validado contra el esquema de la plantilla.
        confianza: la declarada por el modelo (None si el esquema no la pide).
        validacion: esquema_valido | reintento | escalado_tier.
        tokens_in / tokens_out / duracion_ms: consumo de la llamada final.
        intentos: rastro completo (tier, modelo, resultado) para auditoría.
    """
    paso_id: str
    version: str
    tier: Tier
    modelo: str
    salida: dict[str, Any]
    confianza: float | None
    validacion: str
    tokens_in: int
    tokens_out: int
    duracion_ms: int
    intentos: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Transporte por protocolo
# ---------------------------------------------------------------------------
async def _llamar_openai(cfg, system: str, user: str) -> tuple[str, int, int]:
    """DeepSeek V4 Flash/Pro: endpoint OpenAI-compatible (/chat/completions)."""
    async with httpx.AsyncClient(timeout=get_settings().llm_timeout_s) as cli:
        r = await cli.post(f"{cfg.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {cfg.api_key}"},
                           json={"model": cfg.modelo, "temperature": 0.1,
                                 "max_tokens": cfg.max_tokens_salida,
                                 "response_format": {"type": "json_object"},
                                 "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}]})
        r.raise_for_status()
        d = r.json()
        uso = d.get("usage", {})
        return (d["choices"][0]["message"]["content"],
                uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0))


async def _llamar_anthropic(cfg, system: str, user: str) -> tuple[str, int, int]:
    """Claude Fable 5: API de Anthropic (/messages)."""
    async with httpx.AsyncClient(timeout=get_settings().llm_timeout_s) as cli:
        r = await cli.post(f"{cfg.base_url}/messages",
                           headers={"x-api-key": cfg.api_key,
                                    "anthropic-version": "2023-06-01"},
                           json={"model": cfg.modelo, "max_tokens": cfg.max_tokens_salida,
                                 "system": system + " Return ONLY a JSON object.",
                                 "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        d = r.json()
        texto = "".join(b.get("text", "") for b in d.get("content", []))
        uso = d.get("usage", {})
        return texto, uso.get("input_tokens", 0), uso.get("output_tokens", 0)


async def _llamar(cfg, system: str, user: str) -> tuple[str, int, int]:
    if cfg.protocolo == "anthropic":
        return await _llamar_anthropic(cfg, system, user)
    return await _llamar_openai(cfg, system, user)


def _extraer_json(texto: str) -> dict:
    """Tolera fences ```json ... ``` alrededor del objeto."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        if t.startswith("json"):
            t = t[4:]
    return json.loads(t)


# ---------------------------------------------------------------------------
# Ejecución con validación y escalado
# ---------------------------------------------------------------------------
async def ejecutar_paso(paso_id: str, entrada: dict[str, Any],
                        tier_minimo: Tier | None = None) -> ResultadoPaso:
    """Ejecuta un paso del catálogo con validación de esquema y escalado.

    Parámetros:
        paso_id: id del catálogo (p. ej. "triage.classify").
        entrada: payload que rellena la plantilla (se serializa como user msg).
        tier_minimo: tier de arranque si el orquestador lo sube (p. ej.
            recommended_tier_downstream de Triage). Nunca baja del tier
            declarado por la plantilla.

    Salida: ResultadoPaso (ver dataclass).
    Errores:
        LLMEscaladoHumano: agotados los tiers (incluido FABLE).
    """
    s = get_settings()
    pl = plantilla(paso_id)
    tier: Tier = pl["tier"]
    if tier_minimo is not None:
        orden = [Tier.FLASH, Tier.PRO, Tier.FABLE]
        if orden.index(tier_minimo) > orden.index(tier):
            tier = tier_minimo

    esquema = pl["salida"]
    user_msg = json.dumps(entrada, ensure_ascii=False, default=str)
    intentos: list[dict] = []

    while True:
        cfg = config_tier(tier)
        fallos_esquema = 0
        feedback: str | None = None

        while True:  # reintentos dentro del tier
            msg = user_msg if not feedback else (
                user_msg + "\n\nPREVIOUS ATTEMPT WAS INVALID: " + feedback +
                "\nReturn ONLY valid JSON conforming to the schema.")
            try:
                with Cronometro() as c:
                    texto, tin, tout = await _llamar(cfg, pl["system"], msg)
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                log.warning("llm.proveedor_fallo", paso=paso_id, tier=tier.value,
                            modelo=cfg.modelo, error=type(e).__name__)
                intentos.append({"tier": tier.value, "modelo": cfg.modelo,
                                 "resultado": "proveedor_fallo"})
                break  # sale del tier -> escala

            try:
                salida = _extraer_json(texto)
                jsonschema.validate(salida, esquema)
            except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                fallos_esquema += 1
                feedback = str(e)[:800]
                log.warning("llm.esquema_invalido", paso=paso_id, tier=tier.value,
                            intento=fallos_esquema)
                intentos.append({"tier": tier.value, "modelo": cfg.modelo,
                                 "resultado": "esquema_invalido"})
                if fallos_esquema >= s.llm_max_reintentos_esquema:
                    break  # escala de tier
                continue   # reintenta mismo tier con feedback

            confianza = salida.get("confidence")
            if (confianza is not None and confianza < s.llm_umbral_confianza
                    and pl["escalable"] and siguiente_tier(tier) is not None):
                log.info("llm.baja_confianza", paso=paso_id, tier=tier.value,
                         confianza=confianza)
                intentos.append({"tier": tier.value, "modelo": cfg.modelo,
                                 "resultado": "baja_confianza", "confianza": confianza})
                break  # escala de tier

            # --- éxito ---
            validacion = "esquema_valido" if not intentos else (
                "escalado_tier" if intentos[-1]["tier"] != tier.value else "reintento")
            log.info("llm.paso_ok", paso=paso_id, version=pl["version"],
                     tier=tier.value, modelo=cfg.modelo, duracion_ms=c.ms,
                     tokens_in=tin, tokens_out=tout, confianza=confianza,
                     validacion=validacion, escalados=len(intentos))
            return ResultadoPaso(paso_id=paso_id, version=pl["version"], tier=tier,
                                 modelo=cfg.modelo, salida=salida, confianza=confianza,
                                 validacion=validacion, tokens_in=tin, tokens_out=tout,
                                 duracion_ms=c.ms, intentos=intentos)

        # --- escalado de tier ---
        prox = siguiente_tier(tier) if pl["escalable"] or tier is not Tier.FABLE else None
        if prox is None:
            log.error("llm.escalado_humano", paso=paso_id, ultimo_tier=tier.value,
                      intentos=len(intentos))
            raise LLMEscaladoHumano(paso_id, "tiers_agotados", intentos)
        log.info("llm.escalado", paso=paso_id, de=tier.value, a=prox.value)
        tier = prox
