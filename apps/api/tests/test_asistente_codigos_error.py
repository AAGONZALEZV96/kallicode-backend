"""Errores del asistente de alta registrados en CATALOGO_ERRORES (KC-PT-07-5).

ONBOARDING_INCOMPLETO, JOBS_ACTIVOS y CONEXION_EN_USO se usaban sin registrar: AppError
lanzaba ValueError y el cliente recibía 500 ERROR_INTERNO. Con la sesión y las consultas
simuladas, cada endpoint responde ahora su 409 con el código. No requieren base de datos.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app

MODULO = "kallicode_api.routers.connections"


def _como(rol: str) -> None:
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=f"u_{rol}", tenant_id="org_prueba", rol=rol, restringido=False
    )


def _consultas(*respuestas: object):
    """Simula sesion_de y uno: cada SELECT devuelve la siguiente respuesta."""

    @asynccontextmanager
    async def sesion(_usuario: UsuarioActual) -> AsyncIterator[object]:
        db = AsyncMock()
        yield db

    return (
        patch(f"{MODULO}.sesion_de", sesion),
        patch(f"{MODULO}.uno", new_callable=AsyncMock, side_effect=list(respuestas)),
    )


def _grafo_listo():
    return patch(
        f"{MODULO}.leer_estado_ingesta",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(
            snapshot_id="snap_prueba",
            fase=SimpleNamespace(checkpoint_numero=6),
            cobertura=SimpleNamespace(nodos_totales=20, porcentaje_embeddings=75.0),
        ),
    )


@pytest.mark.asyncio
async def test_encender_sin_repositorio_ni_ramas_responde_409_con_los_pendientes(client) -> None:
    _como("owner")
    sesion, uno = _consultas({"fabrica_activa": False}, None, None)
    with sesion, uno, _grafo_listo():
        resp = await client.post("/api/v1/onboarding/complete")

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["codigo"] == "ONBOARDING_INCOMPLETO"
    assert error["detalle"] == {"pendientes": ["repositorio", "mapa_ramas"]}


@pytest.mark.asyncio
async def test_encender_sin_mapa_de_ramas_solo_lo_pide_a_el(client) -> None:
    _como("owner")
    sesion, uno = _consultas({"fabrica_activa": False}, {"id": "cn_1"}, None)
    with sesion, uno, _grafo_listo():
        resp = await client.post("/api/v1/onboarding/complete")

    assert resp.status_code == 409
    assert resp.json()["error"]["detalle"] == {"pendientes": ["mapa_ramas"]}


@pytest.mark.asyncio
async def test_mapa_de_ramas_con_trabajos_en_build_o_deploy_responde_409(client) -> None:
    _como("admin")
    sesion, uno = _consultas({"n": 2})
    with sesion, uno:
        resp = await client.put(
            "/api/v1/connections/branch-map",
            json={"feature": "feature/*", "staging": "develop", "produccion": "main"},
        )

    assert resp.status_code == 409
    assert resp.json()["error"]["codigo"] == "JOBS_ACTIVOS"


@pytest.mark.asyncio
async def test_eliminar_un_repositorio_con_trabajos_activos_responde_409(client) -> None:
    _como("owner")
    sesion, uno = _consultas(
        {"id": "cn_1", "categoria": "repositorio", "proveedor": "github"}, {"n": 1}
    )
    with sesion, uno:
        resp = await client.delete("/api/v1/connections/cn_1")

    assert resp.status_code == 409
    assert resp.json()["error"]["codigo"] == "CONEXION_EN_USO"
