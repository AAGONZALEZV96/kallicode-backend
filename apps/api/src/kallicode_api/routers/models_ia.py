"""Modelos IA (§14 del diseño) — modalidad gestionada (D10).

Las llaves LLM las pone y administra Kallicode centralmente (los tres tiers
de config.py). Para los tenants, esta pantalla es de LECTURA: registrar
llaves propias devuelve FUNCION_NO_DISPONIBLE (403). La asignación por
función refleja el enrutamiento fijo de tiers del catálogo de plantillas.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from kallicode_core.config import get_settings
from kallicode_core.errors import funcion_no_disponible
from kallicode_core.llm import PLANTILLAS, Tier

from ..deps import UsuarioActual, requiere_rol, usuario_actual

router = APIRouter(prefix="/models", tags=["Modelos IA"])

_TIER_INFO = {
    Tier.FLASH: ("DeepSeek V4 Flash", "razonamiento simple · alto volumen"),
    Tier.PRO: ("DeepSeek V4 Pro", "razonamiento complejo · modo extendido"),
    Tier.FABLE: ("Claude Fable 5", "muy complejo · orquestación"),
}


@router.get("/providers", summary="Proveedores y estado (gestionado)")
async def providers(usuario: UsuarioActual = Depends(
        requiere_rol("owner", "admin", "architect"))) -> dict:
    """Salida: los tres tiers con su modelo (llaves gestionadas por Kallicode,
    nunca visibles). Sin credenciales del tenant en esta modalidad."""
    s = get_settings()
    return {"modalidad": "gestionada",
            "tiers": [{"tier": t.value, "modelo": m, "nombre": _TIER_INFO[t][0],
                       "perfil": _TIER_INFO[t][1]}
                      for t, m in ((Tier.FLASH, s.llm_flash_model),
                                   (Tier.PRO, s.llm_pro_model),
                                   (Tier.FABLE, s.llm_fable_model))]}


@router.put("/providers/{proveedor}", summary="Registrar llave propia (no disponible)")
async def registrar_llave(proveedor: str,
                          usuario: UsuarioActual = Depends(
                              requiere_rol("owner", "admin"))) -> dict:
    """En modalidad gestionada (D10) el tenant no aporta llaves: 403."""
    raise funcion_no_disponible(
        "En tu modalidad las llaves de modelos las gestiona Kallicode.")


@router.get("/assignments", summary="Asignación de tiers por paso del pipeline")
async def assignments(usuario: UsuarioActual = Depends(
        requiere_rol("owner", "admin", "architect"))) -> dict:
    """Salida: cada paso del catálogo con su tier por defecto, si escala
    automáticamente y su descripción — la vista viva del enrutamiento."""
    s = get_settings()
    modelo = {Tier.FLASH: s.llm_flash_model, Tier.PRO: s.llm_pro_model,
              Tier.FABLE: s.llm_fable_model}
    pasos = [{"paso": pid, "etapa": pid.split(".")[0], "tier": pl["tier"].value,
              "modelo": modelo[pl["tier"]], "escalable": pl["escalable"],
              "descripcion": pl["descripcion"], "version": pl["version"]}
             for pid, pl in PLANTILLAS.items()]
    return {"politica_escalado": "flash → pro → fable (fallo de esquema x2, "
                                 "baja confianza o proveedor caído); si fable "
                                 "falla, escalado a humano",
            "asignaciones": pasos}
