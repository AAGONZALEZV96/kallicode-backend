"""Contrato cm-escalacion-v1: escalación humana de una ingesta bloqueada (KC-CM-10-6).

Cuando el worker de ingesta de CodeMapping (KC-CM-10-1) se encuentra con un bloqueo que reintentar
no arregla —credenciales del repositorio vencidas, repositorio corrupto, la cadena semántica agotó
los tiers del LLM o se agotaron los reintentos—, pausa el snapshot del commit en
`pendiente_intervencion` y pide a Backend que abra la escalación y notifique al responsable:

    POST {KC_INTERNAL_API_BASE_URL}/codemapping/escalations
    Authorization: Bearer <JWT de servicio, svc = "ingesta">
    Idempotency-Key: cm-esc:<tenant>:<repo_id>:<commit_sha>:<motivo>
    X-Trace-Id: <trace_id de la ingesta>

    201 -> escalación abierta · 200 -> ya estaba abierta (misma clave de idempotencia)

La solicitud lleva el diagnóstico (tipo de error, mensaje enmascarado y acotado, frames sin código),
el paso, el tier y el lote del fallo, la referencia de entrada (`repo_id@commit_sha`) y el
`trace_id`. Nunca lleva código del cliente ni credenciales.

Este módulo es la única definición del contrato: los modelos, las cotas y el JSON Schema congelado
en contratos/cm-escalacion-v1.schema.json. El cliente HTTP está en
`kallicode_codemapping.escalacion`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION_CONTRATO = "cm-escalacion-v1"
RUTA_CONTRATO = Path(__file__).resolve().parents[4] / "contratos" / "cm-escalacion-v1.schema.json"

# Ruta relativa a KC_INTERNAL_API_BASE_URL (que ya termina en /internal/v1).
RUTA_ENDPOINT = "/codemapping/escalations"
# Servicio del JWT: ya está en SERVICIOS_PERMITIDOS de apps/api/src/kallicode_api/deps.py.
SERVICIO = "ingesta"

# Cotas del diagnóstico: suficiente para triar sin volcar nada del repositorio del cliente.
MENSAJE_MAX_CHARS = 500
MAX_FRAMES = 20
MAX_TIERS = 10
IDENT_MAX_CHARS = 200

MotivoEscalacion = Literal[
    "credenciales_expiradas",
    "repositorio_corrupto",
    "tiers_agotados",
    "reintentos_agotados",
    "otro",
]
Severidad = Literal["baja", "media", "alta", "critica"]
EstadoEscalacion = Literal["abierta", "en_curso", "resuelta"]

# Sin credenciales o con el repositorio roto no hay grafo: bloquea el onboarding entero.
SEVERIDAD_POR_MOTIVO: dict[str, Severidad] = {
    "credenciales_expiradas": "alta",
    "repositorio_corrupto": "alta",
    "tiers_agotados": "media",
    "reintentos_agotados": "media",
    "otro": "media",
}


class FrameDiagnostico(BaseModel):
    """Un frame de la traza: solo nombre de archivo de Kallicode, línea y función."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    archivo: str = Field(max_length=IDENT_MAX_CHARS)
    linea: int = Field(ge=0)
    funcion: str = Field(max_length=IDENT_MAX_CHARS)


class Diagnostico(BaseModel):
    """Contexto para el responsable: qué falló, cuántas veces y por dónde pasó."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tipo_error: str = Field(min_length=1, max_length=IDENT_MAX_CHARS)
    mensaje: str = Field(
        max_length=MENSAJE_MAX_CHARS,
        description="Primera línea del error, con las credenciales de las URLs enmascaradas",
    )
    intentos: int = Field(ge=0, description="Intentos de la ingesta antes de escalar")
    frames: list[FrameDiagnostico] = Field(default_factory=list, max_length=MAX_FRAMES)
    tiers_probados: list[str] = Field(
        default_factory=list,
        max_length=MAX_TIERS,
        description="Tiers del LLM que se probaron (solo motivo tiers_agotados)",
    )


class EscalacionIngestaIn(BaseModel):
    """Cuerpo de POST /internal/v1/codemapping/escalations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=IDENT_MAX_CHARS)
    repo_id: str = Field(min_length=1, max_length=IDENT_MAX_CHARS)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    snapshot_id: str | None = Field(
        default=None,
        max_length=IDENT_MAX_CHARS,
        description="Snapshot pausado en pendiente_intervencion",
    )
    trace_id: str = Field(min_length=1, max_length=IDENT_MAX_CHARS)
    motivo: MotivoEscalacion
    severidad: Severidad
    paso: str = Field(
        min_length=1,
        max_length=IDENT_MAX_CHARS,
        description="'ingesta' o el paso LLM del catálogo (p. ej. codemapping.describe_symbols)",
    )
    tier: str | None = Field(default=None, max_length=IDENT_MAX_CHARS)
    lote: int | None = Field(default=None, ge=0, description="Índice del lote LLM que falló")
    entrada_ref: str = Field(
        min_length=1,
        max_length=2 * IDENT_MAX_CHARS + 65,
        description="Referencia de la entrada: <repo_id>@<commit_sha>; nunca el contenido",
    )
    diagnostico: Diagnostico


class EscalacionIngestaOut(BaseModel):
    """Respuesta 200/201. Campos nuevos de Backend se ignoran (cambio compatible)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    escalation_id: str = Field(min_length=1, max_length=IDENT_MAX_CHARS)
    estado: EstadoEscalacion


def clave_idempotencia(solicitud: EscalacionIngestaIn) -> str:
    """Una escalación abierta por (tenant, repo, commit, motivo): reenviarla no duplica."""
    return (
        f"cm-esc:{solicitud.tenant_id}:{solicitud.repo_id}:{solicitud.commit_sha}:"
        f"{solicitud.motivo}"
    )


def generar_contrato() -> dict[str, Any]:
    """JSON Schema del contrato cm-escalacion-v1 a partir de los modelos (fuente única)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:kallicode:contrato:cm-escalacion:v1",
        "title": "Escalación humana de una ingesta de CodeMapping bloqueada",
        "description": (
            "Contrato v1.0.0 entre CodeMapping y Backend (KC-CM-10-6). El worker de ingesta pausa "
            "el snapshot en pendiente_intervencion y pide abrir la escalación con contexto "
            "diagnóstico y trace_id, sin código del cliente ni credenciales. Idempotente por "
            "(tenant, repo, commit, motivo). Cambios compatibles suben la versión menor; quitar o "
            "renombrar un campo exige v2."
        ),
        "x-version": "1.0.0",
        "x-endpoint": {
            "metodo": "POST",
            "ruta": "/internal/v1" + RUTA_ENDPOINT,
            "servicio_jwt": SERVICIO,
            "cabeceras": ["Authorization", "Idempotency-Key", "X-Trace-Id"],
            "respuestas": {"201": "abierta", "200": "ya abierta (idempotente)"},
        },
        "x-severidad_por_motivo": dict(SEVERIDAD_POR_MOTIVO),
        "entrada": EscalacionIngestaIn.model_json_schema(),
        "salida": EscalacionIngestaOut.model_json_schema(),
    }


def serializar_contrato() -> str:
    return json.dumps(generar_contrato(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":  # pragma: no cover - regeneración manual del contrato
    RUTA_CONTRATO.write_text(serializar_contrato(), encoding="utf-8", newline="\n")
    sys.stdout.write(f"contrato escrito en {RUTA_CONTRATO}\n")
