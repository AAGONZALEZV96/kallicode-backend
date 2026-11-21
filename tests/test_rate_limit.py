"""Pruebas de cubo de fichas en Redis y cabeceras X-RateLimit-* (KC-BE-07-6)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app as app_publica
from kallicode_api.main_interna import app as app_interna
from kallicode_core import comercial, limites
from kallicode_core.config import get_settings
from kallicode_core.errors import AppError

pytestmark = pytest.mark.asyncio


class RelojSimulado:
    """Reloj determinista para probar recarga continua y cruces de ventana."""

    def __init__(self, inicio: float = 1_700_000_000.0) -> None:
        self.ahora = inicio

    def __call__(self) -> float:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora += segundos


async def test_cubo_fichas_elimina_efecto_borde_en_cambio_de_ventana(
    redis_doble,
    monkeypatch,
) -> None:
    """En t=59s se agota el cubo; en t=61s no se permite el doble del límite."""
    reloj = RelojSimulado(inicio=59.0)
    monkeypatch.setattr(limites, "ahora_segundos", reloj)

    clave = "kc:t:org_borde:rl:u:u_1"
    capacidad = 10

    # En t=59s (1s antes del cambio de minuto) consume las 10 fichas disponibles
    for esperado in range(capacidad - 1, -1, -1):
        estado = await limites.consumir_cubo(clave, capacidad, ventana_s=60.0, nombre="usuario")
        assert estado.permitido is True
        assert estado.restante == esperado

    # La petición 11 en t=59s es rechazada
    with pytest.raises(AppError) as exc_info:
        await limites.consumir_cubo(clave, capacidad, ventana_s=60.0, nombre="usuario")
    assert exc_info.value.codigo == "RATE_LIMIT_EXCEDIDO"
    assert exc_info.value.http == 429

    # Cruza el borde de minuto a t=61s (solo transcurrieron 2s -> 2 * (10/60) = 0.333 fichas)
    reloj.avanzar(2.0)
    with pytest.raises(AppError):
        await limites.consumir_cubo(clave, capacidad, ventana_s=60.0, nombre="usuario")

    # Tras 6 segundos completos (6 * (10/60) = 1.0 ficha) recupera exactamente 1 ficha, nunca 10
    reloj.avanzar(4.0)
    recuperado = await limites.consumir_cubo(clave, capacidad, ventana_s=60.0, nombre="usuario")
    assert recuperado.permitido is True
    assert recuperado.restante == 0

    with pytest.raises(AppError):
        await limites.consumir_cubo(clave, capacidad, ventana_s=60.0, nombre="usuario")


async def test_cuatro_limites_del_catalogo_usan_cubo_de_fichas(
    redis_doble,
    monkeypatch,
) -> None:
    """RL-1 (usuario/tenant), RL-2 (typing), RL-3 (webhook) y Login usan el cubo atómico."""
    reloj = RelojSimulado()
    monkeypatch.setattr(limites, "ahora_segundos", reloj)

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_usuario", 2)
    monkeypatch.setattr(settings, "rate_limit_tenant", 5)
    monkeypatch.setattr(settings, "rate_limit_typing", 2)
    monkeypatch.setattr(settings, "rate_limit_webhook", 2)
    monkeypatch.setattr(settings, "rate_limit_login", 2)

    # 1. RL-1: Usuario + Tenant
    st_u, st_t = await comercial.rl_usuario("u_cat", "org_cat")
    assert st_u.limite == 2 and st_u.restante == 1
    assert st_t.limite == 5 and st_t.restante == 4
    await comercial.rl_usuario("u_cat", "org_cat")
    with pytest.raises(AppError) as err_rl1:
        await comercial.rl_usuario("u_cat", "org_cat")
    assert err_rl1.value.http == 429

    # 2. RL-2: Tipeo en vivo
    st_typ = await comercial.rl_typing("org_cat", "u_cat")
    assert st_typ.limite == 2 and st_typ.restante == 1
    await comercial.rl_typing("org_cat", "u_cat")
    with pytest.raises(AppError) as err_rl2:
        await comercial.rl_typing("org_cat", "u_cat")
    assert err_rl2.value.http == 429

    # 3. RL-3: Webhook por conexión
    st_wh = await comercial.rl_webhook("org_cat", "cn_cat")
    assert st_wh.limite == 2 and st_wh.restante == 1
    await comercial.rl_webhook("org_cat", "cn_cat")
    with pytest.raises(AppError) as err_rl3:
        await comercial.rl_webhook("org_cat", "cn_cat")
    assert err_rl3.value.http == 429

    # 4. Login por IP
    st_log = await comercial.rl_login("203.0.113.10")
    assert st_log.limite == 2 and st_log.restante == 1
    await comercial.rl_login("203.0.113.10")
    with pytest.raises(AppError) as err_login:
        await comercial.rl_login("203.0.113.10")
    assert err_login.value.http == 429


async def test_respuestas_tres_superficies_y_rl1_usuario_tenant_incluyen_cabeceras(
    redis_doble,
    monkeypatch,
) -> None:
    """Toda respuesta de /api/v1 (y las tres superficies) expone X-RateLimit-* con usuario y tenant."""
    reloj = RelojSimulado()
    monkeypatch.setattr(limites, "ahora_segundos", reloj)

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_usuario", 3)
    monkeypatch.setattr(settings, "rate_limit_tenant", 10)

    # Superficie 1: Portal /api/v1 con RL-1 activo (usuario + tenant)
    async def _usuario_con_rl() -> UsuarioActual:
        await comercial.rl_usuario("u_hdr", "org_hdr")
        return UsuarioActual(
            user_id="u_hdr",
            tenant_id="org_hdr",
            rol="admin",
            restringido=False,
        )

    app_publica.dependency_overrides[usuario_actual] = _usuario_con_rl
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app_publica),
            base_url="http://test",
        ) as client:
            # Endpoint no autenticado en /api/v1 (422) incluye X-RateLimit-* sin consumir cubo de usuario
            r1 = await client.post("/api/v1/auth/login", json={})
            assert r1.status_code == 422
            assert "X-RateLimit-Limit" in r1.headers
            assert "X-RateLimit-Remaining" in r1.headers
            assert "X-RateLimit-Reset" in r1.headers

            # Probamos ruta protegida (/api/v1/auth/me o /api/v1/users/me) que ejecuta usuario_actual
            r_me_1 = await client.get("/api/v1/users/me")
            assert r_me_1.headers["X-RateLimit-Limit"] == "3"
            assert r_me_1.headers["X-RateLimit-Remaining"] == "2"
            assert int(r_me_1.headers["X-RateLimit-Reset"]) >= 1
            assert r_me_1.headers["X-RateLimit-Limit-Usuario"] == "3"
            assert r_me_1.headers["X-RateLimit-Remaining-Usuario"] == "2"
            assert r_me_1.headers["X-RateLimit-Limit-Tenant"] == "10"
            assert r_me_1.headers["X-RateLimit-Remaining-Tenant"] == "9"

            r_me_2 = await client.get("/api/v1/users/me")
            assert r_me_2.headers["X-RateLimit-Remaining"] == "1"
            assert r_me_2.headers["X-RateLimit-Remaining-Tenant"] == "8"

            r_me_3 = await client.get("/api/v1/users/me")
            assert r_me_3.headers["X-RateLimit-Remaining"] == "0"
            assert r_me_3.headers["X-RateLimit-Remaining-Tenant"] == "7"

            # La 4ta petición excede el límite de usuario -> 429 con Retry-After + X-RateLimit-*
            r_me_4 = await client.get("/api/v1/users/me")
            assert r_me_4.status_code == 429
            assert int(r_me_4.headers["Retry-After"]) >= 1
            assert r_me_4.headers["X-RateLimit-Limit"] == "3"
            assert r_me_4.headers["X-RateLimit-Remaining"] == "0"
            assert int(r_me_4.headers["X-RateLimit-Reset"]) >= 1
            assert r_me_4.headers["X-RateLimit-Remaining-Usuario"] == "0"
            assert "X-RateLimit-Remaining-Tenant" not in r_me_4.headers

            # Superficie 2: Webhooks (/api/v1/webhooks/...)
            r_wh = await client.post("/api/v1/webhooks/github/inexistente", content=b"{}")
            assert "X-RateLimit-Limit" in r_wh.headers
            assert "X-RateLimit-Remaining" in r_wh.headers
            assert "X-RateLimit-Reset" in r_wh.headers

        # Superficie 3: API interna (/internal/v1/...)
        async with AsyncClient(
            transport=ASGITransport(app=app_interna),
            base_url="http://test",
        ) as client_int:
            r_int = await client_int.get("/internal/v1/inexistente")
            assert "X-RateLimit-Limit" in r_int.headers
            assert "X-RateLimit-Remaining" in r_int.headers
            assert "X-RateLimit-Reset" in r_int.headers
    finally:
        app_publica.dependency_overrides.clear()


async def test_secuencia_cabeceras_typing_y_momento_exacto_429(
    redis_doble,
    monkeypatch,
) -> None:
    """Consume RL-2 (tipeo en vivo) en /api/v1/tickets/dedup-preview y verifica la secuencia exacta."""
    reloj = RelojSimulado()
    monkeypatch.setattr(limites, "ahora_segundos", reloj)

    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_usuario", 100)
    monkeypatch.setattr(settings, "rate_limit_tenant", 1000)
    monkeypatch.setattr(settings, "rate_limit_typing", 4)

    async def _usuario_typing() -> UsuarioActual:
        await comercial.rl_usuario("u_typ", "org_typ")
        return UsuarioActual(
            user_id="u_typ",
            tenant_id="org_typ",
            rol="member",
            restringido=False,
        )

    app_publica.dependency_overrides[usuario_actual] = _usuario_typing
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app_publica),
            base_url="http://test",
        ) as client:
            secuencia_remaining: list[int] = []
            for idx in range(4):
                # Texto < 10 chars evita consultar embeddings/BD pero ejecuta RL-2 completo
                resp = await client.post(
                    "/api/v1/tickets/dedup-preview",
                    json={"texto": f"abc {idx}"},
                )
                assert resp.status_code == 200, resp.text
                assert resp.headers["X-RateLimit-Limit"] == "4"
                rem = int(resp.headers["X-RateLimit-Remaining"])
                reset = int(resp.headers["X-RateLimit-Reset"])
                assert rem == 3 - idx
                assert reset >= 1
                secuencia_remaining.append(rem)

            assert secuencia_remaining == [3, 2, 1, 0]

            # La 5ta petición (N + 1) recibe 429 en el momento exacto
            resp_429 = await client.post(
                "/api/v1/tickets/dedup-preview",
                json={"texto": "abc 4"},
            )
            assert resp_429.status_code == 429
            assert resp_429.json()["error"]["codigo"] == "RATE_LIMIT_EXCEDIDO"
            assert int(resp_429.headers["Retry-After"]) >= 1
            assert resp_429.headers["X-RateLimit-Limit"] == "4"
            assert resp_429.headers["X-RateLimit-Remaining"] == "0"
            assert int(resp_429.headers["X-RateLimit-Reset"]) >= 1
    finally:
        app_publica.dependency_overrides.clear()
