"""Pruebas de sondas de salud /health y /health/ready (KC-BE-10-4)."""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from time import monotonic
from unittest.mock import AsyncMock

import httpx
import pytest
from kallicode_api.main import app as app_publica
from kallicode_api.main_interna import app as app_interna
from kallicode_api.routers import health
from kallicode_core.config import get_settings
from kallicode_workers import salud as salud_workers


def _puerto_cerrado() -> int:
    """Reserva un puerto efímero local y lo cierra inmediatamente."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(autouse=True)
def _limpiar_config_y_estado():
    get_settings.cache_clear()
    salud_workers.registrar_orquestador(None)
    yield
    salud_workers.registrar_orquestador(None)
    get_settings.cache_clear()


def _mock_dependencias_sanas(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    @asynccontextmanager
    async def sesion_falsa():
        yield object()

    redis_falso = AsyncMock()
    redis_falso.ping = AsyncMock(return_value=True)
    redis_falso.zcount = AsyncMock(return_value=1)

    for modulo in (health, salud_workers):
        monkeypatch.setattr(modulo, "sesion_sistema", sesion_falsa)
        monkeypatch.setattr(modulo, "valor", AsyncMock(return_value=1))
        monkeypatch.setattr(modulo.comercial, "redis_cliente", lambda: redis_falso)

    return redis_falso


@pytest.mark.asyncio
async def test_health_liveness_sin_dependencias(monkeypatch: pytest.MonkeyPatch) -> None:
    puerto = _puerto_cerrado()
    monkeypatch.setenv(
        "KC_DATABASE_URL_ADMIN",
        f"postgresql+asyncpg://kallicode_admin:dev@127.0.0.1:{puerto}/kallicode",
    )
    monkeypatch.setenv("KC_REDIS_URL", f"redis://127.0.0.1:{puerto}/0")
    get_settings.cache_clear()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        resp = await cliente.get("/health")

    assert resp.status_code == 200
    assert resp.json()["estado"] == "ok"


@pytest.mark.asyncio
async def test_ready_200_cuando_todas_dependencias_estan_sanas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dependencias_sanas(monkeypatch)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        resp = await cliente.get("/health/ready")

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "ok"
    assert cuerpo["postgres"] is True
    assert cuerpo["redis"] is True
    assert cuerpo["boveda"] is True
    assert "orquestador" not in cuerpo
    assert cuerpo["detalle"]["postgres"]["ok"] is True
    assert cuerpo["detalle"]["redis"]["ok"] is True
    assert cuerpo["detalle"]["boveda"]["ok"] is True


@pytest.mark.asyncio
async def test_ready_503_con_postgres_en_puerto_cerrado(monkeypatch: pytest.MonkeyPatch) -> None:
    puerto = _puerto_cerrado()
    monkeypatch.setenv(
        "KC_DATABASE_URL_ADMIN",
        f"postgresql+asyncpg://kallicode_admin:dev@127.0.0.1:{puerto}/kallicode",
    )
    monkeypatch.setenv("KC_HEALTH_TIMEOUT_S", "0.5")
    get_settings.cache_clear()

    redis_falso = AsyncMock()
    redis_falso.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(health.comercial, "redis_cliente", lambda: redis_falso)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        resp = await cliente.get("/health/ready")

    assert resp.status_code == 503
    cuerpo = resp.json()
    assert cuerpo["estado"] == "degradado"
    assert cuerpo["postgres"] is False
    assert cuerpo["redis"] is True
    assert cuerpo["boveda"] is True
    assert cuerpo["detalle"]["postgres"]["ok"] is False
    assert cuerpo["detalle"]["postgres"]["detalle"] != "ok"


@pytest.mark.asyncio
async def test_ready_503_con_redis_en_puerto_cerrado(monkeypatch: pytest.MonkeyPatch) -> None:
    puerto = _puerto_cerrado()
    monkeypatch.setenv("KC_REDIS_URL", f"redis://127.0.0.1:{puerto}/0")
    monkeypatch.setenv("KC_HEALTH_TIMEOUT_S", "0.5")
    get_settings.cache_clear()

    @asynccontextmanager
    async def sesion_falsa():
        yield object()

    monkeypatch.setattr(health, "sesion_sistema", sesion_falsa)
    monkeypatch.setattr(health, "valor", AsyncMock(return_value=1))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        resp = await cliente.get("/health/ready")

    assert resp.status_code == 503
    cuerpo = resp.json()
    assert cuerpo["estado"] == "degradado"
    assert cuerpo["postgres"] is True
    assert cuerpo["redis"] is False
    assert cuerpo["boveda"] is False
    assert cuerpo["detalle"]["redis"]["ok"] is False
    assert cuerpo["detalle"]["boveda"]["ok"] is False


@pytest.mark.asyncio
async def test_ready_503_con_boveda_keyvault_en_puerto_cerrado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    puerto = _puerto_cerrado()
    _mock_dependencias_sanas(monkeypatch)
    monkeypatch.setenv("KC_ENV", "prod")
    monkeypatch.setenv(
        "KC_DATABASE_URL_ADMIN",
        "postgresql+asyncpg://kallicode_admin:dev@127.0.0.1:5432/kallicode",
    )
    monkeypatch.setenv("KC_BLOB_ACCOUNT_URL", "https://sttest.blob.core.windows.net")
    monkeypatch.setenv("KC_KEYVAULT_URL", f"http://127.0.0.1:{puerto}")
    monkeypatch.setenv("KC_HEALTH_TIMEOUT_S", "0.5")
    # En prod valida pares JWT RSA; inyectamos un bypass de claves solo en Settings.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pares = []
    for _ in range(2):
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pares.append(
            (
                priv.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode(),
                priv.public_key()
                .public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                .decode(),
            )
        )
    monkeypatch.setenv("KC_JWT_PRIVATE_KEY", pares[0][0])
    monkeypatch.setenv("KC_JWT_PUBLIC_KEY", pares[0][1])
    monkeypatch.setenv("KC_JWT_PRIVATE_KEY_SVC", pares[1][0])
    monkeypatch.setenv("KC_JWT_PUBLIC_KEY_SVC", pares[1][1])
    get_settings.cache_clear()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        resp = await cliente.get("/health/ready")

    assert resp.status_code == 503
    cuerpo = resp.json()
    assert cuerpo["postgres"] is True
    assert cuerpo["redis"] is True
    assert cuerpo["boveda"] is False
    assert cuerpo["detalle"]["boveda"]["ok"] is False


@pytest.mark.asyncio
async def test_ready_acota_tiempo_de_espera_ante_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KC_HEALTH_TIMEOUT_S", "0.15")
    get_settings.cache_clear()

    @asynccontextmanager
    async def sesion_lenta():
        await asyncio.sleep(5.0)
        yield object()

    redis_falso = AsyncMock()
    redis_falso.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(health, "sesion_sistema", sesion_lenta)
    monkeypatch.setattr(health.comercial, "redis_cliente", lambda: redis_falso)

    inicio = monotonic()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_publica), base_url="http://testserver"
    ) as cliente:
        resp = await cliente.get("/health/ready")
    duracion = monotonic() - inicio

    assert resp.status_code == 503
    assert duracion < 1.0
    assert resp.json()["detalle"]["postgres"] == {"ok": False, "detalle": "timeout"}


@pytest.mark.asyncio
async def test_workers_ready_incluye_orquestador_200_y_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_falso = _mock_dependencias_sanas(monkeypatch)
    app_workers_salud = salud_workers.crear_app_salud()

    # 1. Con pulso vigente en Redis -> 200 en salud.py y en main_interna
    for aplicacion in (app_workers_salud, app_interna):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=aplicacion), base_url="http://testserver"
        ) as cliente:
            resp = await cliente.get("/health/ready")

        assert resp.status_code == 200
        cuerpo = resp.json()
        assert cuerpo["postgres"] is True
        assert cuerpo["redis"] is True
        assert cuerpo["boveda"] is True
        assert cuerpo["orquestador"] is True
        assert cuerpo["detalle"]["orquestador"] == {"ok": True, "detalle": "ok"}

    # 2. Sin pulsos vigentes de orquestador -> 503
    redis_falso.zcount = AsyncMock(return_value=0)
    for aplicacion in (app_workers_salud, app_interna):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=aplicacion), base_url="http://testserver"
        ) as cliente:
            resp = await cliente.get("/health/ready")

        assert resp.status_code == 503
        cuerpo = resp.json()
        assert cuerpo["orquestador"] is False
        assert cuerpo["detalle"]["orquestador"] == {"ok": False, "detalle": "sin_pulso_vigente"}
