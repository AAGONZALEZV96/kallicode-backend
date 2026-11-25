"""KC-BE-10-2: contrato de modelos, política D10 y ejecución del pipeline."""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema, todos
from kallicode_core.llm import PLANTILLAS, Tier, cliente
from kallicode_core.llm.asignaciones import FuncionPipeline
from kallicode_core.llm.tiers import ConfigTier
from sqlalchemy import text

MODELOS = "/api/v1/models"


@pytest.fixture
def sesion_sin_db(monkeypatch):
    @asynccontextmanager
    async def sesion_falsa(_):
        yield object()

    monkeypatch.setattr("kallicode_api.routers.models_ia.sesion_de", sesion_falsa)


@pytest.fixture
async def cliente_api(monkeypatch):
    identidad = UsuarioActual("u_modelos", "org_modelos", "admin", False)
    monkeypatch.setitem(app.dependency_overrides, usuario_actual, lambda: identidad)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        yield http, identidad


@pytest.mark.parametrize(
    ("tier", "protocolo", "ruta", "cabecera"),
    [
        (Tier.FLASH, "openai", "/v1/chat/completions", "authorization"),
        (Tier.PRO, "openai", "/v1/chat/completions", "authorization"),
        (Tier.FABLE, "anthropic", "/v1/messages", "x-api-key"),
    ],
)
async def test_prueba_tier_llama_una_vez_y_devuelve_modelo_efectivo(
    cliente_api, monkeypatch, tier, protocolo, ruta, cabecera
):
    http, _ = cliente_api
    solicitudes = []

    def proveedor(request):
        solicitudes.append(request)
        datos = json.loads(request.content)
        assert request.url.path == ruta
        assert "clave-local-de-prueba" in request.headers[cabecera]
        assert datos["model"] == "alias-configurado"
        assert datos["max_tokens"] == 16
        return httpx.Response(200, json={"model": "modelo-versionado"})

    cfg = ConfigTier(
        tier,
        "alias-configurado",
        "https://proveedor.test/v1",
        "clave-local-de-prueba",
        protocolo,
        4096,
    )
    monkeypatch.setattr(cliente, "config_tier", lambda _: cfg)
    async with httpx.AsyncClient(transport=httpx.MockTransport(proveedor)) as llm:
        monkeypatch.setattr(cliente, "_obtener_cliente_http", lambda: llm)
        respuesta = await http.post(f"{MODELOS}/providers/{tier}/test")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "tier": tier.value,
        "modelo": "modelo-versionado",
        "latencia_ms": respuesta.json()["latencia_ms"],
    }
    assert respuesta.json()["latencia_ms"] >= 0
    assert len(solicitudes) == 1


async def test_prueba_tier_enmascara_fallo_del_proveedor(cliente_api, monkeypatch):
    http, _ = cliente_api
    cfg = ConfigTier(
        Tier.PRO, "modelo", "https://proveedor.test/v1", "clave-local-de-prueba", "openai", 4096
    )
    monkeypatch.setattr(cliente, "config_tier", lambda _: cfg)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": cfg.api_key}))
    ) as llm:
        monkeypatch.setattr(cliente, "_obtener_cliente_http", lambda: llm)
        respuesta = await http.post(f"{MODELOS}/providers/pro/test")

    assert respuesta.status_code == 503
    assert respuesta.json()["error"]["codigo"] == "DEPENDENCIA_NO_DISPONIBLE"
    assert cfg.api_key not in respuesta.text


@pytest.mark.parametrize("rol", ["architect", "approver", "member", "viewer"])
async def test_operaciones_de_gestion_rechazan_roles_sin_permiso(cliente_api, monkeypatch, rol):
    http, usuario = cliente_api
    usuario.rol = rol
    sonda = AsyncMock()
    monkeypatch.setattr("kallicode_api.routers.models_ia.probar_tier", sonda)

    prueba = await http.post(f"{MODELOS}/providers/pro/test")
    cambio = await http.put(
        f"{MODELOS}/assignments",
        json={
            "asignaciones": [{"funcion": "triage", "tier": "pro"}],
        },
    )
    for respuesta in (prueba, cambio):
        assert respuesta.status_code == 403
        assert respuesta.json()["error"]["codigo"] == "PERMISO_DENEGADO"
    sonda.assert_not_awaited()


async def test_lectura_con_seis_funciones_y_pasos_compatibles(
    cliente_api, monkeypatch, sesion_sin_db
):
    http, usuario = cliente_api
    usuario.rol = "architect"
    monkeypatch.setattr(
        "kallicode_api.routers.models_ia.leer_asignaciones",
        AsyncMock(return_value={FuncionPipeline.DESIGN: Tier.PRO}),
    )
    respuesta = await http.get(f"{MODELOS}/assignments")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    funciones = {f["funcion"]: f for f in datos["funciones"]}
    assert len(datos["funciones"]) == 6
    assert funciones.keys() == {f.value for f in FuncionPipeline}
    assert funciones["design"]["principal"]["tier"] == "pro"
    assert funciones["design"]["fallback"]["tier"] == "fable"
    assert {p["paso"]: p["tier"] for p in funciones["design"]["pasos"]} == {
        "design.digest": "pro",
        "design.alternatives": "pro",
        "design.adversarial_review": "fable",
        "design.compile": "pro",
    }
    pasos_orquestacion = {p["paso"] for p in datos["pasos_orquestacion"]}
    assert pasos_orquestacion == {"orchestrator.route"}
    assert {
        p["paso"] for funcion in funciones.values() for p in funcion["pasos"]
    } | pasos_orquestacion == PLANTILLAS.keys()
    assert {p["paso"] for p in datos["asignaciones"]} == PLANTILLAS.keys()


@pytest.mark.parametrize(("funcion", "tier"), [("build", "flash"), ("triage", "fable")])
async def test_tier_fuera_de_politica_devuelve_codigo_de_catalogo(
    cliente_api, sesion_sin_db, funcion, tier
):
    http, _ = cliente_api
    respuesta = await http.put(
        f"{MODELOS}/assignments",
        json={"asignaciones": [{"funcion": funcion, "tier": tier}]},
    )
    assert respuesta.status_code == 403
    assert respuesta.json()["error"]["codigo"] == "FUNCION_NO_DISPONIBLE"


async def test_registro_de_llave_propia_sigue_bloqueado(cliente_api):
    http, _ = cliente_api
    respuesta = await http.put(f"{MODELOS}/providers/pro")
    assert respuesta.status_code == 403
    assert respuesta.json()["error"]["codigo"] == "FUNCION_NO_DISPONIBLE"


@pytest.mark.integracion
async def test_persiste_aplica_y_audita_solo_para_el_tenant(
    cliente_api, seed_dos_tenants, monkeypatch
):
    http, usuario = cliente_api
    tenant_a, tenant_b = seed_dos_tenants
    usuario.tenant_id = tenant_a
    usuario.rol = "owner"
    datos = {"asignaciones": [{"funcion": "triage", "tier": "pro"}]}

    try:
        cambio = await http.put(f"{MODELOS}/assignments", json=datos)
        assert cambio.status_code == 200, cambio.text
        assert (await http.put(f"{MODELOS}/assignments", json=datos)).status_code == 200
        assert (await http.get(f"{MODELOS}/assignments")).json() == cambio.json()
        # Aísla la generación externa y comprueba el tier que selecciona el cliente real.
        salida = {"type": "bug", "summary_en": "Example", "symptoms": [], "confidence": 1}
        llamada = AsyncMock(return_value=(json.dumps(salida), 10, 5))
        monkeypatch.setattr(cliente, "_llamar", llamada)
        monkeypatch.setattr(cliente, "_obtener_cliente_http", object)
        monkeypatch.setattr(cliente, "_circuitos", {})
        paso_a = await cliente.ejecutar_paso("triage.classify", {}, tenant_id=tenant_a)
        paso_b = await cliente.ejecutar_paso("triage.classify", {}, tenant_id=tenant_b)
        assert paso_a.tier is Tier.PRO
        assert paso_b.tier is Tier.FLASH

        usuario.tenant_id = tenant_b
        lectura = await http.get(f"{MODELOS}/assignments")
        assert lectura.status_code == 200
        assert lectura.json()["funciones"][0]["principal"]["tier"] == "flash"

        async with sesion_sistema() as db:
            eventos = await todos(
                db,
                """
                SELECT actor_id, datos FROM audit.audit_events
                WHERE tenant_id = :tenant AND evento = 'modelos_configurados'
            """,
                {"tenant": tenant_a},
            )
        assert eventos == [
            {
                "actor_id": usuario.user_id,
                "datos": {
                    "asignaciones": [{"funcion": "triage", "anterior": "flash", "nuevo": "pro"}]
                },
            }
        ]
    finally:
        async with sesion_sistema() as db:
            for tabla in ("core.model_assignments", "core.model_providers"):
                await db.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id = :tenant"), {"tenant": tenant_a}
                )
            await db.commit()
