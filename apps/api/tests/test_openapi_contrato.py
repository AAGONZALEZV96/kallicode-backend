import re
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from kallicode_api.contrato_openapi import METODOS_HTTP, enriquecer_contrato
from kallicode_api.main import app as app_publica
from kallicode_api.main_interna import app as app_interna
from kallicode_api.routers import health, webhooks

ROOT = Path(__file__).resolve().parents[3]

CONTRATOS = {
    "/api/v1": (
        app_publica,
        ROOT / "contratos/api-v1.yaml",
    ),
    "/internal/v1": (
        app_interna,
        ROOT / "contratos/internal-v1.yaml",
    ),
}


def test_contratos_congelados_coinciden_con_runtime() -> None:
    for prefijo, (aplicacion, ruta) in CONTRATOS.items():
        assert ruta.exists(), f"No existe el contrato: {ruta}"

        with ruta.open(encoding="utf-8") as archivo:
            contrato_publicado = yaml.safe_load(archivo)

        contrato_actual = enriquecer_contrato(
            aplicacion.openapi(),
            prefijo,
        )

        assert contrato_publicado == contrato_actual, (
            f"{ruta} no coincide con el esquema actual. "
            "Regenera los contratos ejecutando "
            "docs/congelar_contrato.py."
        )


def _operaciones_publicadas():
    contrato = yaml.safe_load(CONTRATOS["/api/v1"][1].read_text(encoding="utf-8"))
    return {
        (metodo, ruta): operacion
        for ruta, operaciones in contrato["paths"].items()
        for metodo, operacion in operaciones.items()
        if metodo in METODOS_HTTP
    }


def test_cobertura_de_80_operaciones_publicas():
    rutas_reales = {
        (metodo, ruta)
        for ruta, operaciones in app_publica.openapi()["paths"].items()
        for metodo in operaciones
        if metodo in METODOS_HTTP
    }
    assert rutas_reales == _operaciones_publicadas().keys()
    assert len(rutas_reales) == 80


@pytest.fixture
async def cliente_contrato(monkeypatch):
    @asynccontextmanager
    async def sesion_falsa():
        yield object()

    # Readiness recibe dependencias sanas. Los webhooks consultan un token inexistente.
    monkeypatch.setattr(health, "sesion_sistema", sesion_falsa)
    monkeypatch.setattr(health, "valor", AsyncMock(return_value=1))
    monkeypatch.setattr(health.comercial, "redis_cliente", lambda: AsyncMock())
    monkeypatch.setattr("kallicode_api.main.registrar_http", AsyncMock())
    monkeypatch.setattr(webhooks, "sesion_sistema", sesion_falsa)
    monkeypatch.setattr(webhooks, "uno", AsyncMock(return_value=None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        yield cliente


@pytest.mark.parametrize(
    ("metodo", "ruta", "operacion"),
    [
        pytest.param(metodo, ruta, op, id=f"{metodo.upper()} {ruta}")
        for (metodo, ruta), op in _operaciones_publicadas().items()
    ],
)
async def test_operacion_publica_responde_con_codigo_declarado(
    cliente_contrato, metodo, ruta, operacion
):
    # Todas las operaciones tienen un caso HTTP concreto. En rutas privadas se
    # comprueba el rechazo sin credenciales y en auth pública el cuerpo inválido.
    if ruta.startswith("/health"):
        estado, codigo = 200, None
    elif ruta.startswith("/api/v1/webhooks/"):
        estado, codigo = 401, None  # El rechazo de firma responde sin cuerpo.
    elif ruta.startswith("/api/v1/auth/") or ruta == "/api/v1/telemetry/client":
        # Auth pública y la telemetría del portal (KC-PT-11-5, acepta envíos sin sesión).
        estado, codigo = 422, "VALIDACION_ENTRADA"
    else:
        estado, codigo = 401, "NO_AUTENTICADO"

    url = re.sub(r"\{[^}]+\}", "flash" if "/models/providers/" in ruta else "1", ruta)
    respuesta = await cliente_contrato.request(metodo, url)
    assert str(estado) in operacion["responses"]
    assert respuesta.status_code == estado, respuesta.text
    if codigo:
        assert respuesta.json()["error"]["codigo"] == codigo
