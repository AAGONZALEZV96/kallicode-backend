"""Asignación por función del pipeline (§14.4) en modalidad gestionada (D10)."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auditoria import registrar_evento
from ..db import sesion_tenant, todos, uno
from ..errors import funcion_no_disponible, no_encontrado
from .tiers import ORDEN_ESCALADO, Tier, config_tier, siguiente_tier


class FuncionPipeline(StrEnum):
    TRIAGE = "triage"
    DESIGN = "design"
    BUILD = "build"
    QA = "qa"
    SECURITY = "security"
    DEPLOY = "deploy"


# Piso por función según el catálogo actual. Los pasos especializados conservan
# su tier mínimo individual. Orquestación tiene un tier fijo fuera de las seis funciones.
TIER_PREDETERMINADO = {
    FuncionPipeline.TRIAGE: Tier.FLASH,
    FuncionPipeline.DESIGN: Tier.FLASH,
    FuncionPipeline.BUILD: Tier.PRO,
    FuncionPipeline.QA: Tier.FLASH,
    FuncionPipeline.SECURITY: Tier.FLASH,
    FuncionPipeline.DEPLOY: Tier.FLASH,
}


def resolver_tier_paso(tier: Tier, escalable: bool, asignado: Tier | None) -> Tier:
    """Una asignación eleva el piso de las plantillas escalables de la función."""
    if (
        escalable
        and asignado is not None
        and ORDEN_ESCALADO.index(asignado) > ORDEN_ESCALADO.index(tier)
    ):
        return asignado
    return tier


def _permitido(funcion: FuncionPipeline, tier: Tier) -> bool:
    # Fable queda como último respaldo. Toda función configurable debe tener fallback.
    return siguiente_tier(tier) is not None and ORDEN_ESCALADO.index(tier) >= ORDEN_ESCALADO.index(
        TIER_PREDETERMINADO[funcion]
    )


def _proveedor(tier: Tier) -> str:
    return "anthropic" if tier is Tier.FABLE else "deepseek_selfhosted"


async def leer_asignaciones(db: AsyncSession, tenant_id: str) -> dict[FuncionPipeline, Tier]:
    filas = await todos(
        db,
        """SELECT funcion, principal_modelo FROM core.model_assignments
           WHERE tenant_id = :tenant AND principal_modelo LIKE 'tier:%'""",
        {"tenant": tenant_id},
    )
    asignaciones = {}
    for fila in filas:
        funcion = FuncionPipeline(fila["funcion"])
        tier_id = fila["principal_modelo"].removeprefix("tier:")
        if tier_id in Tier and _permitido(funcion, Tier(tier_id)):
            asignaciones[funcion] = Tier(tier_id)
    return asignaciones


async def tier_configurado_para_paso(tenant_id: str, paso_id: str) -> Tier | None:
    etapa = paso_id.partition(".")[0]
    if etapa not in FuncionPipeline:
        return None

    async with sesion_tenant(tenant_id) as db:
        asignado = (await leer_asignaciones(db, tenant_id)).get(FuncionPipeline(etapa))
    # Restablecer el valor por defecto conserva las decisiones dinámicas del orquestador.
    return None if asignado == TIER_PREDETERMINADO[FuncionPipeline(etapa)] else asignado


async def guardar_asignaciones(
    db: AsyncSession,
    tenant_id: str,
    actor_id: str,
    cambios: dict[FuncionPipeline, Tier],
) -> dict[FuncionPipeline, Tier]:
    for funcion, tier in cambios.items():
        if not _permitido(funcion, tier):
            raise funcion_no_disponible(
                f"El tier '{tier}' no está permitido para '{funcion}' en modalidad gestionada."
            )

    # Serializa las escrituras del mismo tenant para auditar el valor anterior real.
    org = await uno(
        db,
        "SELECT id FROM core.organizations WHERE id = :tenant FOR NO KEY UPDATE",
        {"tenant": tenant_id},
    )
    if org is None:
        raise no_encontrado("La organización")

    asignaciones = await leer_asignaciones(db, tenant_id)
    auditoria = []
    for funcion, tier in cambios.items():
        anterior = asignaciones.get(funcion, TIER_PREDETERMINADO[funcion])
        if tier == anterior:
            continue
        fallback = siguiente_tier(tier)
        assert fallback is not None

        # Las FK requieren un registro descriptivo por proveedor. D10 conserva
        # las credenciales en la configuración central y respeta las filas preexistentes.
        for candidato in (tier, fallback):
            await db.execute(
                text("""
                    INSERT INTO core.model_providers (tenant_id, proveedor, base_url)
                    VALUES (:tenant, :proveedor, :base_url)
                    ON CONFLICT (tenant_id, proveedor) DO NOTHING
                """),
                {
                    "tenant": tenant_id,
                    "proveedor": _proveedor(candidato),
                    "base_url": config_tier(candidato).base_url,
                },
            )
        # Los alias de modelo son estables cuando Kallicode rota el modelo de un tier.
        await db.execute(
            text("""
                INSERT INTO core.model_assignments (
                    tenant_id, funcion, principal_proveedor, principal_modelo,
                    fallback_proveedor, fallback_modelo
                )
                VALUES (:tenant, :funcion, :proveedor, :principal, :proveedor_fb, :fallback)
                ON CONFLICT (tenant_id, funcion) DO UPDATE SET
                    principal_proveedor = EXCLUDED.principal_proveedor,
                    principal_modelo = EXCLUDED.principal_modelo,
                    fallback_proveedor = EXCLUDED.fallback_proveedor,
                    fallback_modelo = EXCLUDED.fallback_modelo,
                    actualizado_en = now()
            """),
            {
                "tenant": tenant_id,
                "funcion": funcion.value,
                "proveedor": _proveedor(tier),
                "principal": f"tier:{tier}",
                "proveedor_fb": _proveedor(fallback),
                "fallback": f"tier:{fallback}",
            },
        )
        asignaciones[funcion] = tier
        auditoria.append(
            {"funcion": funcion.value, "anterior": anterior.value, "nuevo": tier.value}
        )

    if auditoria:
        # La sesión del tenant confirma la asignación y su evento en la misma transacción.
        await registrar_evento(
            db,
            tenant_id,
            evento="modelos_configurados",
            resumen="Asignaciones de modelos actualizadas",
            actor_tipo="humano",
            actor_id=actor_id,
            datos={"asignaciones": auditoria},
        )
    return asignaciones
