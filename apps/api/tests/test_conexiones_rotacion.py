"""PATCH /connections/{id}: rotación de credenciales y renombre (KC-PT-07-4).

Antes, rotar credenciales escribía una copia en texto plano en la columna
core.connections.configuracion, que no existe: toda rotación respondía 500. Y renombrar
marcaba la conexión como conectada sin probarla. Con la sesión, la bóveda y la prueba de
conexión simuladas, no requieren base de datos.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app

MODULO = "kallicode_api.routers.connections"
CONEXION = {
    "id": "cn_1",
    "proveedor": "github",
    "categoria": "repositorio",
    "config": {"repo_url": "https://github.com/a/b"},
    "estado": "error",
}
OK = {"ok": True, "mensaje": "Conexión verificada.", "permisos": ["repo:read"]}


@dataclass
class Simulacion:
    sql: list[str] = field(default_factory=list)
    guardar: AsyncMock = field(default_factory=AsyncMock)
    leer: AsyncMock = field(default_factory=lambda: AsyncMock(return_value={"token": "viejo"}))
    probar: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=OK))


@pytest.fixture
def sim():
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id="u_admin", tenant_id="org_prueba", rol="admin", restringido=False
    )
    s = Simulacion()

    @asynccontextmanager
    async def sesion(_usuario: UsuarioActual) -> AsyncIterator[object]:
        db = AsyncMock()

        async def ejecutar(sentencia, _params=None):
            s.sql.append(" ".join(str(sentencia).split()))

        db.execute.side_effect = ejecutar
        yield db

    final = {"id": "cn_1", "estado": "conectada", "ultimo_test": None}
    with (
        patch(f"{MODULO}.sesion_de", sesion),
        patch(f"{MODULO}.uno", new_callable=AsyncMock, side_effect=[CONEXION, final]),
        patch(f"{MODULO}.registrar_evento", new_callable=AsyncMock),
        patch(f"{MODULO}.boveda.guardar", s.guardar),
        patch(f"{MODULO}.boveda.leer", s.leer),
        patch(f"{MODULO}.integraciones.probar", s.probar),
    ):
        yield s


@pytest.mark.asyncio
async def test_rotar_credenciales_las_prueba_y_las_guarda_solo_en_la_boveda(client, sim) -> None:
    nuevas = {"token": "ghp_nuevo", "repo_url": "https://github.com/a/b"}

    resp = await client.patch(
        "/api/v1/connections/cn_1",
        json={"config": {"repo_url": "https://github.com/a/b"}, "credenciales": nuevas},
    )

    assert resp.status_code == 200
    sim.probar.assert_awaited_once_with("github", {"repo_url": "https://github.com/a/b"}, nuevas)
    sim.guardar.assert_awaited_once_with("org_prueba", "cn--cn_1", nuevas)
    assert not any("configuracion" in s for s in sim.sql)
    assert not any("ghp_nuevo" in s for s in sim.sql)
    assert any("estado='conectada'" in s and "ultimo_test=now()" in s for s in sim.sql)


@pytest.mark.parametrize(
    ("resultado", "codigo"),
    [
        ({"ok": False, "mensaje": "Credenciales rechazadas."}, "CREDENCIALES_INVALIDAS"),
        (
            {"ok": False, "mensaje": "Acceso a datos.", "permisos_excesivos": True},
            "PERMISOS_EXCESIVOS",
        ),
    ],
)
@pytest.mark.asyncio
async def test_si_la_prueba_falla_no_se_toca_nada(client, sim, resultado, codigo) -> None:
    sim.probar.return_value = resultado

    resp = await client.patch("/api/v1/connections/cn_1", json={"credenciales": {"token": "x"}})

    assert resp.status_code == 400
    assert resp.json()["error"]["codigo"] == codigo
    assert resp.json()["error"]["mensaje"].endswith("Se mantienen las anteriores.")
    sim.guardar.assert_not_awaited()
    assert not any(s.startswith("UPDATE") for s in sim.sql)


@pytest.mark.asyncio
async def test_renombrar_no_prueba_ni_cambia_el_estado(client, sim) -> None:
    resp = await client.patch("/api/v1/connections/cn_1", json={"nombre": "app"})

    assert resp.status_code == 200
    sim.probar.assert_not_awaited()
    sim.guardar.assert_not_awaited()
    assert sim.sql == ["UPDATE core.connections SET nombre=:v WHERE id=:i"]


@pytest.mark.asyncio
async def test_cambiar_solo_la_config_la_prueba_con_las_credenciales_guardadas(client, sim) -> None:
    config = {"repo_url": "https://github.com/a/c"}

    resp = await client.patch("/api/v1/connections/cn_1", json={"config": config})

    assert resp.status_code == 200
    sim.leer.assert_awaited_once_with("org_prueba", "cn--cn_1")
    sim.probar.assert_awaited_once_with("github", config, {"token": "viejo"})
    sim.guardar.assert_not_awaited()
