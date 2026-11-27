"""POST /api/v1/telemetry/client (KC-PT-11-5): errores de cliente agrupados y Web Vitals del portal."""

from __future__ import annotations

import logging

import pytest
from kallicode_api.metricas import _series_portal
from kallicode_api.routers import telemetria
from kallicode_core.config import get_settings
from kallicode_core.seguridad import emitir_access_token
from kallicode_core.telemetria import CLAVE_PORTAL, registrar_portal

pytestmark = pytest.mark.asyncio

RUTA = "/api/v1/telemetry/client"


def _error(**cambios):
    base = {
        "firma": "a1b2c3d4",
        "tipo": "error",
        "mensaje": "Cannot read properties of undefined (reading 'map')",
        "ruta": "/tickets/:numero",
        "veces": 3,
    }
    return {**base, **cambios}


@pytest.fixture
def registrados(monkeypatch):
    """Lo que el router manda a los contadores de Redis, sin depender de un Redis real."""
    llamadas: list[tuple[dict[str, int], list[tuple[str, str]]]] = []

    async def espia(errores_por_tipo, vitals, *, redis=None):
        llamadas.append((dict(errores_por_tipo), list(vitals)))

    monkeypatch.setattr(telemetria, "registrar_portal", espia)
    return llamadas


def _eventos(caplog):
    return [r for r in caplog.records if r.getMessage() == "portal.error_cliente"]


async def test_sin_sesion_se_acepta_y_queda_en_el_registro(
    client, redis_doble, registrados, caplog
):
    caplog.set_level(logging.WARNING, logger="kallicode")
    r = await client.post(RUTA, json={"errores": [_error()], "vitals": []})

    assert r.status_code == 202
    assert r.json() == {"errores": 1, "vitals": 0}
    (evento,) = _eventos(caplog)
    campos = evento.kc_campos
    assert campos["firma"] == "a1b2c3d4"
    assert campos["veces"] == 3
    assert campos["tenant_id"] is None and campos["user_id"] is None
    assert registrados == [({"error": 3}, [])]


async def test_con_token_valido_el_registro_lleva_organizacion_y_persona(
    client, redis_doble, registrados, caplog
):
    caplog.set_level(logging.WARNING, logger="kallicode")
    token = emitir_access_token("u_ana", "org_demo", "member")
    r = await client.post(
        RUTA,
        json={"errores": [_error(tipo="limite", veces=1)]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 202
    campos = _eventos(caplog)[0].kc_campos
    assert (campos["tenant_id"], campos["user_id"]) == ("org_demo", "u_ana")


async def test_un_token_invalido_no_rechaza_el_envio(client, redis_doble, registrados):
    # La telemetría no debe provocar renovaciones ni 401: con un token roto se registra anónima.
    r = await client.post(
        RUTA, json={"errores": [_error()]}, headers={"Authorization": "Bearer no-es-un-jwt"}
    )
    assert r.status_code == 202


async def test_web_vitals_van_a_los_contadores(client, redis_doble, registrados):
    r = await client.post(
        RUTA,
        json={
            "vitals": [
                {"nombre": "LCP", "valor": 1840, "calificacion": "good", "ruta": "/login"},
                {"nombre": "INP", "valor": 260, "calificacion": "needs-improvement", "ruta": "/"},
            ]
        },
    )
    assert r.status_code == 202
    assert r.json() == {"errores": 0, "vitals": 2}
    assert registrados == [({}, [("LCP", "good"), ("INP", "needs-improvement")])]


async def test_correos_tokens_e_identificadores_no_llegan_al_registro(
    client, redis_doble, registrados, caplog
):
    caplog.set_level(logging.WARNING, logger="kallicode")
    mensaje = (
        "Fallo para ana@demo.kallicode.dev con eyJhbGciOi.eyJzdWIiOiJ1In0.firma "
        "y el código kcpr_abc123def456"
    )
    await client.post(
        RUTA, json={"errores": [_error(mensaje=mensaje, ruta="/tickets/KC-1003?x=1")]}
    )

    campos = _eventos(caplog)[0].kc_campos
    assert "ana@demo" not in campos["mensaje"] and "[correo]" in campos["mensaje"]
    assert "eyJ" not in campos["mensaje"] and "kcpr_" not in campos["mensaje"]
    assert campos["ruta"] == "/tickets/:numero"


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"errores": [_error()] * 21},  # más de 20 grupos
        {"vitals": [{"nombre": "LCP", "valor": 1, "calificacion": "good", "ruta": "/"}] * 11},
        {"errores": [_error(mensaje="x" * 301)]},
        {"errores": [_error(veces=0)]},
        {"errores": [_error(tipo="otro")]},
        {"errores": [_error(firma="no válida!")]},
        {"vitals": [{"nombre": "FID", "valor": 1, "calificacion": "good", "ruta": "/"}]},
        {"errores": [{**_error(), "pila": "datos de más"}]},  # campos desconocidos
    ],
)
async def test_lotes_fuera_de_los_topes_se_rechazan_con_422(
    client, redis_doble, registrados, cuerpo
):
    r = await client.post(RUTA, json=cuerpo)
    assert r.status_code == 422
    assert r.json()["error"]["codigo"] == "VALIDACION_ENTRADA"
    assert registrados == []


async def test_limite_de_frecuencia_por_ip_sin_sesion(
    client, redis_doble, registrados, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_limit_telemetria", 2)
    cabeceras = {"X-Forwarded-For": "200.1.2.3"}
    for _ in range(2):
        assert (await client.post(RUTA, json={}, headers=cabeceras)).status_code == 202
    r = await client.post(RUTA, json={}, headers=cabeceras)
    assert r.status_code == 429
    assert r.json()["error"]["codigo"] == "RATE_LIMIT_EXCEDIDO"
    # Otra IP tiene su propio cupo.
    otra = await client.post(RUTA, json={}, headers={"X-Forwarded-For": "200.9.9.9"})
    assert otra.status_code == 202


async def test_limite_de_frecuencia_por_persona_con_sesion(
    client, redis_doble, registrados, monkeypatch
):
    monkeypatch.setattr(get_settings(), "rate_limit_telemetria", 1)
    ana = {"Authorization": f"Bearer {emitir_access_token('u_ana', 'org_demo', 'member')}"}
    beto = {"Authorization": f"Bearer {emitir_access_token('u_beto', 'org_demo', 'member')}"}
    assert (await client.post(RUTA, json={}, headers=ana)).status_code == 202
    assert (await client.post(RUTA, json={}, headers=ana)).status_code == 429
    assert (await client.post(RUTA, json={}, headers=beto)).status_code == 202


class _PipeDoble:
    def __init__(self, destino: dict[str, int]):
        self.destino = destino
        self.pendientes: list[tuple[str, str, int]] = []

    def hincrby(self, clave, campo, n):
        self.pendientes.append((clave, campo, n))

    async def execute(self):
        for clave, campo, n in self.pendientes:
            assert clave == CLAVE_PORTAL
            self.destino[campo] = self.destino.get(campo, 0) + n

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _RedisDoble:
    def __init__(self):
        self.hash: dict[str, int] = {}

    def pipeline(self, **_):
        return _PipeDoble(self.hash)


async def test_contadores_y_serie_de_metrics():
    redis = _RedisDoble()
    await registrar_portal({"error": 3, "limite": 1}, [("LCP", "good")], redis=redis)
    await registrar_portal({"error": 2}, [("LCP", "good"), ("CLS", "poor")], redis=redis)
    assert redis.hash == {
        "error|error": 5,
        "error|limite": 1,
        "vital|LCP|good": 2,
        "vital|CLS|poor": 1,
    }

    errores, vitals = _series_portal({k: str(v) for k, v in redis.hash.items()})
    assert errores.name == "kc_portal_errores"
    assert {(s.labels["tipo"], s.value) for s in errores.samples} == {("error", 5), ("limite", 1)}
    assert {(s.labels["metrica"], s.labels["calificacion"], s.value) for s in vitals.samples} == {
        ("LCP", "good", 2),
        ("CLS", "poor", 1),
    }
