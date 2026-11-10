"""KC-BE-05-7: comprobación de la firma de Blob al arrancar."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from kallicode_api import main


@pytest.fixture
def recursos_simulados(monkeypatch):
    monkeypatch.setattr(
        main.db_module,
        "_init_engines",
        AsyncMock(),
    )
    monkeypatch.setattr(main.db_module, "_engine_app", None)
    monkeypatch.setattr(main.db_module, "_engine_admin", None)
    monkeypatch.setattr(main.comercial, "redis_cliente", Mock())
    monkeypatch.setattr(main.comercial, "_redis", None)


@pytest.mark.parametrize("entorno", ["production", "staging"])
async def test_arranque_comprueba_delegacion(
    monkeypatch,
    recursos_simulados,
    entorno,
):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(env=entorno),
    )
    verificar = Mock()
    monkeypatch.setattr(main.blob, "verificar_delegacion", verificar)

    async with main.lifespan(main.app):
        verificar.assert_called_once()


async def test_arranque_falla_si_no_hay_clave(
    monkeypatch,
    recursos_simulados,
):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(env="production"),
    )
    verificar = Mock(side_effect=RuntimeError("Sin clave de delegación"))
    monkeypatch.setattr(main.blob, "verificar_delegacion", verificar)

    with pytest.raises(RuntimeError, match="Sin clave de delegación"):
        async with main.lifespan(main.app):
            pytest.fail("La aplicación no debería arrancar")

    main.db_module._init_engines.assert_not_awaited()


async def test_arranque_local_no_solicita_delegacion(
    monkeypatch,
    recursos_simulados,
):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(env="local"),
    )
    verificar = Mock()
    monkeypatch.setattr(main.blob, "verificar_delegacion", verificar)

    async with main.lifespan(main.app):
        verificar.assert_not_called()
