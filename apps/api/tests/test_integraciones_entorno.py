"""KC-BE-05-7: ninguna conexión simulada sale de local."""

from types import SimpleNamespace

import pytest
from kallicode_api.servicios import integraciones
from kallicode_core.errors import AppError


@pytest.mark.parametrize(
    "proveedor",
    [
        "github",
        "gitlab",
        "jira",
        "github_actions",
        "postgresql",
    ],
)
@pytest.mark.parametrize(
    "entorno",
    ["dev", "staging", "production"],
)
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
            {"token": "credencial-de-prueba"},
        )

    assert error.value.codigo == "CONFIG_PROVEEDOR"
    assert error.value.http == 422
    assert error.value.detalle == {
        "proveedor": proveedor,
    }


async def test_simulacion_disponible_en_local(monkeypatch):
    monkeypatch.setattr(
        integraciones,
        "get_settings",
        lambda: SimpleNamespace(env="local"),
    )

    resultado = await integraciones.probar(
        "github",
        {},
        {"token": "credencial-de-prueba"},
    )

    assert resultado["ok"] is True
    assert "simulada" in resultado["mensaje"]


async def test_credencial_invalida_falla_en_local(monkeypatch):
    monkeypatch.setattr(
        integraciones,
        "get_settings",
        lambda: SimpleNamespace(env="local"),
    )

    resultado = await integraciones.probar(
        "github",
        {},
        {"token": "invalido"},
    )

    assert resultado["ok"] is False
