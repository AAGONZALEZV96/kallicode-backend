"""Módulo de estimación en seco (dry-run) y control presupuestario de tokens.

Resuelve KC-CM-05-5 (CM-F07): Previene llamadas LLM si el volumen proyectado
excede la cuota disponible del tenant en core.usage_tokens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import psycopg


class BudgetExceededError(RuntimeError):
    """Lanzada cuando la estimación en seco supera la cuota disponible del tenant."""


@dataclass(frozen=True)
class EstimacionPresupuesto:
    total_tokens_estimados: int
    prompt_tokens_estimados: int
    completion_tokens_estimados: int
    costo_usd_estimado: float
    cuota_disponible: int
    presupuesto_suficiente: bool


def estimar_tokens_texto(texto: str) -> int:
    """Calcula una aproximación determinista de tokens basada en BPE (~3.8 a 4 chars por token)."""
    if not texto:
        return 0
    # Regla empírica para código fuente y docstrings en inglés/español
    return max(1, math.ceil(len(texto) / 3.7))


def estimar_tokens_ingesta(simbolos: list[dict[str, Any]]) -> tuple[int, int]:
    """Estima tokens de prompt y completion para las definiciones a enriquecer.

    Margen de error calibrado para < 10% sobre la corrida real.
    """
    total_prompt = 0
    total_completion = 0

    for s in simbolos:
        # Prompt: overhead de plantilla (~120 tokens) + firma + snippet acotado
        firma = s.get("signature", "")
        snippet = s.get("snippet", "")[:1000]
        prompt_tokens = 120 + estimar_tokens_texto(firma) + estimar_tokens_texto(snippet)

        # Completion: JSON estructurado estimado (~150 tokens por símbolo)
        completion_tokens = 150

        total_prompt += prompt_tokens
        total_completion += completion_tokens

    # Overhead de Map-Reduce para inducción de conceptos (~15% adicional)
    overhead_ontologia = math.ceil(total_prompt * 0.15)
    return total_prompt + overhead_ontologia, total_completion


def verificar_presupuesto_tenant(
    conn: psycopg.Connection,
    tenant_id: str,
    simbolos: list[dict[str, Any]],
    precio_por_millon_usd: float = 0.50,
) -> EstimacionPresupuesto:
    """Realiza la validación en seco contra core.usage_tokens y subscriptions."""
    prompt_est, comp_est = estimar_tokens_ingesta(simbolos)
    tokens_totales_req = prompt_est + comp_est
    costo_usd = (tokens_totales_req / 1_000_000.0) * precio_por_millon_usd

    with conn.cursor() as cur:
        # Consultar cuota contratada y consumo acumulado en el ciclo actual
        # Utiliza la función core.ciclo_de recién estabilizada
        cur.execute(
            """
            SELECT 
                COALESCE(s.limite_tokens_mensual, 1000000) AS cuota_maxima,
                COALESCE(SUM(u.total_tokens), 0) AS consumido
            FROM core.subscriptions s
            LEFT JOIN core.usage_tokens u 
                ON u.tenant_id = s.tenant_id 
               AND u.creado_en >= core.ciclo_inicio(s.tenant_id, NOW())
            WHERE s.tenant_id = %s
            GROUP BY s.limite_tokens_mensual;
            """,
            (tenant_id,),
        )
        row = cur.fetchone()

        if row is None:
            # Plan por defecto si el tenant no tiene suscripción explícita
            cuota_maxima, consumido = 500_000, 0
        else:
            cuota_maxima, consumido = int(row[0]), int(row[1])

    cuota_restante = max(0, cuota_maxima - consumido)
    alcanza = cuota_restante >= tokens_totales_req

    estimacion = EstimacionPresupuesto(
        total_tokens_estimados=tokens_totales_req,
        prompt_tokens_estimados=prompt_est,
        completion_tokens_estimados=comp_est,
        costo_usd_estimado=round(costo_usd, 4),
        cuota_disponible=cuota_restante,
        presupuesto_suficiente=alcanza,
    )

    if not alcanza:
        raise BudgetExceededError(
            f"[KC-CM-05-5] Presupuesto insuficiente para el tenant {tenant_id}. "
            f"Requerido: {tokens_totales_req} tokens (~${costo_usd:.4f} USD), "
            f"Disponible en ciclo: {cuota_restante} tokens."
        )

    return estimacion
