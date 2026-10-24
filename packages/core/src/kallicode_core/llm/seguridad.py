"""
Módulo de seguridad para la interacción con LLMs.
Implementa el patrón de Defensa en Profundidad requerido por BE-F16:
- Validación dura de outputs contra la base de datos (evita escalado de privilegios).
- Control estricto de presupuesto (cuotas) vía Redis.
"""

from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .tiers import Tier


class SeguridadLLMError(Exception):
    """Excepción base para violaciones de seguridad o cuota de LLM."""


class CuotaExcedidaError(SeguridadLLMError):
    pass


class InyeccionDetectadaError(SeguridadLLMError):
    pass


async def verificar_y_consumir_cuota(
    redis_client: Redis,
    tenant_id: str,
    ticket_id: str,
    tokens_consumidos: int,
    limite_tenant: int = 2_000_000,
    limite_ticket: int = 50_000,
) -> None:
    """
    Descuenta los tokens en tiempo real usando un Pipeline de Redis para atomicidad.
    Lanza CuotaExcedidaError si el tenant o el ticket superan su límite presupuestario.
    """
    key_tenant = f"llm_quota:tenant:{tenant_id}"
    key_ticket = f"llm_quota:ticket:{ticket_id}"

    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.incrby(key_tenant, tokens_consumidos)
        pipe.incrby(key_ticket, tokens_consumidos)
        # TTL de 30 días para no saturar la RAM de Redis con tickets viejos
        pipe.expire(key_ticket, 60 * 60 * 24 * 30)
        resultados = await pipe.execute()

    consumo_tenant = resultados[0]
    consumo_ticket = resultados[1]

    if consumo_tenant > limite_tenant:
        raise CuotaExcedidaError(
            f"El Tenant {tenant_id} superó la cuota mensual LLM ({consumo_tenant}/{limite_tenant})"
        )

    if consumo_ticket > limite_ticket:
        raise CuotaExcedidaError(
            f"El Ticket {ticket_id} consumió demasiados recursos LLM y fue abortado "
            f"({consumo_ticket}/{limite_ticket})"
        )


async def validar_decisiones_criticas(
    db: AsyncSession, tenant_id: str, salida_llm: dict[str, Any]
) -> None:
    """
    Verifica que las variables críticas de la salida no hayan sido manipuladas.
    Cualquier discrepancia lanza InyeccionDetectadaError.
    """
    # 1. Validación de Tier: El LLM no puede forzar un string inventado.
    tier_recomendado = salida_llm.get("recommended_tier_downstream") or salida_llm.get("tier")
    if tier_recomendado is not None:
        valores_validos = {t.value for t in Tier}
        if tier_recomendado not in valores_validos:
            raise InyeccionDetectadaError(
                f"Alucinación de billing detectada: Tier '{tier_recomendado}' es inválido."
            )

    # 2. Validación Cross-Tenant de Duplicados (duplicate_of)
    duplicate_of = salida_llm.get("duplicate_of")
    if duplicate_of is not None:
        query = text("""
            SELECT 1 FROM core.tickets 
            WHERE id = :ticket_id AND tenant_id = :tenant_id
        """)
        resultado = await db.execute(query, {"ticket_id": duplicate_of, "tenant_id": tenant_id})
        if not resultado.scalar():
            raise InyeccionDetectadaError(
                f"Intento de manipulación cruzada: El ticket duplicado {duplicate_of} "
                f"no pertenece al tenant {tenant_id}."
            )

    # 3. Validación de Alcance (scope_paths)
    scope_paths = salida_llm.get("scope_paths")
    if scope_paths and isinstance(scope_paths, list):
        query = text("""
            SELECT allowed_path FROM core.tenant_allowlists 
            WHERE tenant_id = :tenant_id
        """)
        resultado = await db.execute(query, {"tenant_id": tenant_id})
        rutas_permitidas = {row[0] for row in resultado.fetchall()}

        for path in scope_paths:
            # Validamos si el path que quiere tocar empieza con alguna ruta autorizada
            autorizado = any(path.startswith(rp) for rp in rutas_permitidas)
            if not autorizado:
                raise InyeccionDetectadaError(
                    f"Escalamiento de privilegios bloqueado: LLM intentó modificar '{path}', "
                    f"el cual no está en la allowlist del tenant."
                )
