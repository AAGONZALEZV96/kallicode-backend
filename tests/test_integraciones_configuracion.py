"""KC-BE-05-7: la simulación de proveedores solo se permite en local."""

from types import SimpleNamespace

import pytest
from kallicode_api.servicios import integraciones
from kallicode_core.errors import AppError


@pytest.mark.parametrize(
    "proveedor",
    ["github", "gitlab", "jira", "github_actions", "postgresql"],
)
@pytest.mark.parametrize("entorno", ["test", "staging", "production"])
async def test_simulacion_rechazada_fuera_de_local(
    monkeypatch,
    proveedor,
    entorno,
):
    monkeypatch.setattr(
        integraciones,
        "get_settings",
        lambda: SimpleNamespace(env=entorno),
    )

    with pytest.raises(AppError) as error:
        await integraciones.probar(
            proveedor,
            {},
            {"token": "valido"},
        )

    assert error.value.http == 503
    assert error.value.codigo == "DEPENDENCIA_NO_DISPONIBLE"
    assert proveedor in error.value.mensaje


async def test_simulacion_local_sigue_disponible(monkeypatch):
    monkeypatch.setattr(
        integraciones,
        "get_settings",
        lambda: SimpleNamespace(env="local"),
    )

    resultado_valido = await integraciones.probar(
        "github",
        {},
        {"token": "valido"},
    )
    resultado_invalido = await integraciones.probar(
        "github",
        {},
        {"token": "invalido"},
    )

    assert resultado_valido["ok"] is True
    assert resultado_invalido["ok"] is False
