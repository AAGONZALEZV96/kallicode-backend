"""Proveedor HTTP local de respuestas grabadas para la demo hasta Gate 1."""

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from kallicode_core.llm.plantillas import PLANTILLAS
from pydantic import BaseModel

app = FastAPI(title="Kallicode LLM de demostración")
_GRABACIONES = Path(__file__).with_name("grabaciones")
_RESPUESTAS = {
    (paso, tier): (_GRABACIONES / f"{paso.replace('.', '_')}_{tier}.json").read_text(
        encoding="utf-8"
    )
    for paso, tier in (
        ("triage.classify", "flash"),
        ("triage.dedup", "flash"),
        ("triage.impact", "flash"),
        ("design.digest", "flash"),
        ("design.alternatives", "pro"),
        ("design.adversarial_review", "fable"),
        ("design.compile", "flash"),
    )
}


class Mensaje(BaseModel):
    role: str
    content: str


class SolicitudChat(BaseModel):
    model: Literal["demo-flash", "demo-pro"]
    messages: list[Mensaje]


class SolicitudFable(BaseModel):
    model: Literal["demo-fable"]
    system: str


def _respuesta(system: str, tier: str) -> str:
    for (paso, tier_grabado), respuesta in _RESPUESTAS.items():
        if tier == tier_grabado and system.startswith(PLANTILLAS[paso]["system"]):
            return respuesta
    raise HTTPException(422, "La demo solo cubre Triage y Diseño hasta Gate 1.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"estado": "demo"}


@app.post("/v1/chat/completions")
def chat(datos: SolicitudChat) -> dict:
    system = next((mensaje.content for mensaje in datos.messages if mensaje.role == "system"), "")
    respuesta = _respuesta(system, datos.model.removeprefix("demo-"))
    return {
        "choices": [{"message": {"role": "assistant", "content": respuesta}}],
        "usage": {"prompt_tokens": 64, "completion_tokens": 32},
    }


@app.post("/v1/messages")
def messages(datos: SolicitudFable) -> dict:
    return {
        "content": [{"type": "text", "text": _respuesta(datos.system, "fable")}],
        "usage": {"input_tokens": 64, "output_tokens": 32},
    }
