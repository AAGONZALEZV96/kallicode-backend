"""Modelos IA (§14 del diseño). Kallicode administra las credenciales en D10."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from kallicode_core.errors import funcion_no_disponible
from kallicode_core.llm import PLANTILLAS, Tier
from kallicode_core.llm.asignaciones import (
    TIER_PREDETERMINADO,
    FuncionPipeline,
    guardar_asignaciones,
    leer_asignaciones,
    resolver_tier_paso,
)
from kallicode_core.llm.cliente import probar_tier
from kallicode_core.llm.tiers import config_tier, siguiente_tier
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..deps import UsuarioActual, requiere_rol, sesion_de

router = APIRouter(prefix="/models", tags=["Modelos IA"])

_TIER_INFO = {
    Tier.FLASH: ("DeepSeek V4 Flash", "razonamiento simple · alto volumen"),
    Tier.PRO: ("DeepSeek V4 Pro", "razonamiento complejo · modo extendido"),
    Tier.FABLE: ("Claude Fable 5", "muy complejo · orquestación"),
}


class ModeloTier(BaseModel):
    tier: Tier
    modelo: str


class ResultadoPruebaModelo(ModeloTier):
    latencia_ms: int = Field(ge=0)


class AsignacionPaso(ModeloTier):
    paso: str
    etapa: str
    escalable: bool
    descripcion: str
    version: str


class AsignacionFuncion(BaseModel):
    funcion: FuncionPipeline
    principal: ModeloTier = Field(description="Tier base gestionado para la función.")
    fallback: ModeloTier = Field(description="Primer tier de respaldo del principal.")
    pasos: list[AsignacionPaso]


class AsignacionesOut(BaseModel):
    politica_escalado: str
    funciones: list[AsignacionFuncion]
    pasos_orquestacion: list[AsignacionPaso] = Field(
        description="Pasos compartidos de orquestación, fuera de las seis funciones."
    )
    asignaciones: list[AsignacionPaso] = Field(
        description="Vista plana de compatibilidad con el portal. Incluye el paso de orquestación."
    )


class CambioAsignacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    funcion: FuncionPipeline
    tier: Tier


class AsignacionesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asignaciones: list[CambioAsignacion] = Field(
        min_length=1,
        max_length=len(FuncionPipeline),
        description="Funciones que se actualizarán. Las omitidas conservan su asignación.",
    )

    @field_validator("asignaciones")
    @classmethod
    def funciones_unicas(cls, cambios: list[CambioAsignacion]) -> list[CambioAsignacion]:
        if len({cambio.funcion for cambio in cambios}) != len(cambios):
            raise ValueError("Cada función se puede asignar una sola vez.")
        return cambios


def _respuesta_asignaciones(asignadas: dict[FuncionPipeline, Tier]) -> AsignacionesOut:
    modelos = {tier: ModeloTier(tier=tier, modelo=config_tier(tier).modelo) for tier in Tier}
    pasos = []
    for pid, plantilla in PLANTILLAS.items():
        etapa = pid.partition(".")[0]
        asignado = asignadas.get(FuncionPipeline(etapa)) if etapa in FuncionPipeline else None
        tier = resolver_tier_paso(plantilla["tier"], plantilla["escalable"], asignado)
        pasos.append(
            AsignacionPaso(
                paso=pid,
                etapa=etapa,
                tier=tier,
                modelo=modelos[tier].modelo,
                escalable=plantilla["escalable"],
                descripcion=plantilla["descripcion"],
                version=plantilla["version"],
            )
        )

    funciones = []
    for funcion, predeterminado in TIER_PREDETERMINADO.items():
        tier = asignadas.get(funcion, predeterminado)
        fallback = siguiente_tier(tier)
        assert fallback is not None  # La política gestionada exige un tier de respaldo.
        funciones.append(
            AsignacionFuncion(
                funcion=funcion,
                principal=modelos[tier],
                fallback=modelos[fallback],
                pasos=[paso for paso in pasos if paso.etapa == funcion],
            )
        )

    return AsignacionesOut(
        politica_escalado=(
            "flash → pro → fable (fallo de esquema, baja confianza o proveedor caído); "
            "si fable falla, escalado a humano"
        ),
        funciones=funciones,
        pasos_orquestacion=[paso for paso in pasos if paso.etapa not in FuncionPipeline],
        asignaciones=pasos,
    )


@router.get("/providers", summary="Proveedores y estado (gestionado)")
async def providers(
    usuario: UsuarioActual = Depends(requiere_rol("owner", "admin", "architect")),
) -> dict:
    """Lista los tiers administrados por Kallicode."""
    return {
        "modalidad": "gestionada",
        "tiers": [
            {
                "tier": tier.value,
                "modelo": config_tier(tier).modelo,
                "nombre": nombre,
                "perfil": perfil,
            }
            for tier, (nombre, perfil) in _TIER_INFO.items()
        ],
    }


@router.put(
    "/providers/{proveedor}",
    summary="Registrar llave propia (no disponible)",
    responses={
        403: {"description": "En modalidad gestionada las llaves las administra Kallicode."}
    },
)
async def registrar_llave(
    proveedor: str, usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))
) -> dict:
    """En modalidad gestionada (D10), las llaves las administra Kallicode."""
    raise funcion_no_disponible("En tu modalidad las llaves de modelos las gestiona Kallicode.")


@router.post(
    "/providers/{proveedor}/test",
    summary="Probar disponibilidad de un tier gestionado",
    responses={
        403: {"description": "La prueba requiere el rol owner o admin."},
        503: {"description": "El proveedor del tier gestionado no está disponible."},
    },
)
async def probar_proveedor(
    proveedor: Tier, usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))
) -> ResultadoPruebaModelo:
    """El parámetro proveedor identifica uno de los tiers publicados por GET /models/providers."""
    modelo, latencia_ms = await probar_tier(proveedor)
    return ResultadoPruebaModelo(tier=proveedor, modelo=modelo, latencia_ms=latencia_ms)


@router.get("/assignments", summary="Consultar modelos principales y fallbacks por función")
async def assignments(
    usuario: UsuarioActual = Depends(requiere_rol("owner", "admin", "architect")),
) -> AsignacionesOut:
    async with sesion_de(usuario) as db:
        asignadas = await leer_asignaciones(db, usuario.tenant_id)
    return _respuesta_asignaciones(asignadas)


@router.put(
    "/assignments",
    summary="Actualizar los tiers por función",
    responses={403: {"description": "Rol insuficiente o tier fuera de la política gestionada."}},
)
async def actualizar_asignaciones(
    datos: AsignacionesIn, usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))
) -> AsignacionesOut:
    """Configura el principal de las funciones enviadas.

    Flash y Pro están disponibles como principales, con Pro como mínimo para Build.
    El fallback sigue la escala gestionada y los pasos fijos conservan su propio tier.
    """
    cambios = {cambio.funcion: cambio.tier for cambio in datos.asignaciones}
    async with sesion_de(usuario) as db:
        asignadas = await guardar_asignaciones(db, usuario.tenant_id, usuario.user_id, cambios)
        return _respuesta_asignaciones(asignadas)
