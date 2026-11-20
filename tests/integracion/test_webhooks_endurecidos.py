"""KC-BE-07-2: Webhooks endurecidos — ventana anti-replay, límites por proveedor,
rotación de secreto con convivencia y rate limiting con Retry-After."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_api.servicios import boveda, firmas
from kallicode_core import comercial
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text


class RedisStreamDoble:
    """Envuelve el redis_doble añadiendo soporte de xadd para webhooks."""

    def __init__(self, base):
        self._base = base
        self.streams: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        return getattr(self._base, name)

    async def xadd(self, stream: str, fields: dict, **kwargs):
        self.streams.append((stream, fields))
        return f"{len(self.streams)}-0"


@pytest_asyncio.fixture
async def entorno_webhooks(redis_doble, monkeypatch):
    redis_con_stream = RedisStreamDoble(redis_doble)
    monkeypatch.setattr(comercial, "redis_cliente", lambda: redis_con_stream)

    tenant_id = f"org_wh_{uuid.uuid4().hex[:10]}"
    user_id = f"usr_wh_{uuid.uuid4().hex[:10]}"
    conn_gh_id = f"cn_gh_{uuid.uuid4().hex[:10]}"
    conn_help_id = f"cn_hp_{uuid.uuid4().hex[:10]}"
    token_gh = f"wh_gh_{uuid.uuid4().hex[:16]}"
    token_help = f"wh_hp_{uuid.uuid4().hex[:16]}"
    secreto_inicial = "whsec_secreto_inicial_1234567890"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Org Webhooks KC07')"),
            {"id": tenant_id},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol, password_hash) "
                "VALUES (:uid, :t, :em, 'Owner WH', 'owner', 'hash_dummy')"
            ),
            {"uid": user_id, "t": tenant_id, "em": f"{user_id}@kallicode.dev"},
        )
        await db.execute(
            text(
                """
                INSERT INTO core.connections (
                    id, tenant_id, categoria, proveedor, nombre, config,
                    secreto_ref, ruta_token, estado
                )
                VALUES
                    (:cgh, :t, 'repositorio', 'github', 'GH Webhook', '{"repo":"acme/app"}'::jsonb,
                     :sr_gh, :rt_gh, 'conectada'),
                    (:chp, :t, 'tickets', 'kallicode_help', 'Help Webhook', '{}'::jsonb,
                     :sr_hp, :rt_hp, 'conectada')
                """
            ),
            {
                "cgh": conn_gh_id,
                "chp": conn_help_id,
                "t": tenant_id,
                "sr_gh": f"cn--{conn_gh_id}",
                "sr_hp": f"cn--{conn_help_id}",
                "rt_gh": token_gh,
                "rt_hp": token_help,
            },
        )
        await db.commit()

    await boveda.guardar(tenant_id, f"cn--{conn_gh_id}", {"webhook_secret": secreto_inicial})
    await boveda.guardar(tenant_id, f"cn--{conn_help_id}", {"webhook_secret": secreto_inicial})

    jwt_owner = emitir_access_token(user_id=user_id, tenant_id=tenant_id, rol="owner")

    try:
        yield {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "conn_gh_id": conn_gh_id,
            "conn_help_id": conn_help_id,
            "token_gh": token_gh,
            "token_help": token_help,
            "secreto_inicial": secreto_inicial,
            "auth_headers": {"Authorization": f"Bearer {jwt_owner}"},
            "redis": redis_con_stream,
        }
    finally:
        async with sesion_sistema() as db:
            await db.execute(
                text("DELETE FROM core.webhook_inbox WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("DELETE FROM core.connections WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("DELETE FROM core.users WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("DELETE FROM core.organizations WHERE id = :t"),
                {"t": tenant_id},
            )
            await db.commit()


@pytest.mark.integracion
async def test_anti_replay_rechaza_entrega_antigua_y_deduplica_en_ventana(
    entorno_webhooks,
) -> None:
    """KC-BE-07-2: entregas fuera de ventana temporal reciben 401 de cuerpo mínimo;
    dentro de ventana se aceptan una vez y se deduplican por (connection_id, delivery_id)."""
    ctx = entorno_webhooks
    cuerpo = json.dumps({"action": "opened", "number": 42}).encode("utf-8")
    ahora = datetime.now(UTC)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        # 1. Entrega antigua (10 minutos atrás, fuera de ventana de 300s) -> 401 cuerpo mínimo
        ts_antiguo = str(int((ahora - timedelta(minutes=10)).timestamp()))
        firma_antigua = firmas.firmar_payload(ctx["secreto_inicial"], cuerpo, ts_antiguo)
        resp_replay = await cliente.post(
            f"/api/v1/webhooks/github/{ctx['token_gh']}",
            content=cuerpo,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": firma_antigua,
                "X-Webhook-Timestamp": ts_antiguo,
                "X-GitHub-Delivery": "deliv-replay-001",
                "X-GitHub-Event": "issues",
            },
        )
        assert resp_replay.status_code == 401
        assert resp_replay.content == b""

        # 2. Entrega sin marca temporal -> 401 cuerpo mínimo
        firma_sin_ts = firmas.firmar_payload(ctx["secreto_inicial"], cuerpo)
        resp_sin_ts = await cliente.post(
            f"/api/v1/webhooks/github/{ctx['token_gh']}",
            content=cuerpo,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": firma_sin_ts,
                "X-GitHub-Delivery": "deliv-sints-001",
                "X-GitHub-Event": "issues",
            },
        )
        assert resp_sin_ts.status_code == 401
        assert resp_sin_ts.content == b""

        # 3. Entrega válida dentro de ventana -> 202 y persistida en inbox
        ts_vigente = str(int(ahora.timestamp()))
        firma_vigente = firmas.firmar_payload(ctx["secreto_inicial"], cuerpo, ts_vigente)
        cabeceras_validas = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": firma_vigente,
            "X-Webhook-Timestamp": ts_vigente,
            "X-GitHub-Delivery": "deliv-valida-001",
            "X-GitHub-Event": "issues",
        }
        resp_ok = await cliente.post(
            f"/api/v1/webhooks/github/{ctx['token_gh']}",
            content=cuerpo,
            headers=cabeceras_validas,
        )
        assert resp_ok.status_code == 202
        assert len(ctx["redis"].streams) == 1

        # 4. Reproducción exacta dentro de la ventana -> 202 idempotente sin duplicar en BD ni stream
        resp_dup = await cliente.post(
            f"/api/v1/webhooks/github/{ctx['token_gh']}",
            content=cuerpo,
            headers=cabeceras_validas,
        )
        assert resp_dup.status_code == 202
        assert len(ctx["redis"].streams) == 1

    async with sesion_sistema() as db:
        conteo = await uno(
            db,
            "SELECT count(*) AS n FROM core.webhook_inbox WHERE connection_id = :c",
            {"c": ctx["conn_gh_id"]},
        )
    assert conteo["n"] == 1


@pytest.mark.integracion
async def test_limites_payload_por_proveedor_y_help_mayor(
    entorno_webhooks,
    monkeypatch,
) -> None:
    """KC-BE-07-2: límite 1 MB para proveedores estándar y 4 MB para Kallicode Help;
    superarlo devuelve 413 PAYLOAD_EXCESIVO del catálogo."""
    ctx = entorno_webhooks
    ahora_ts = str(int(datetime.now(UTC).timestamp()))

    # Simular plan Business con permite_help=True para el webhook de Kallicode Help
    monkeypatch.setattr(
        comercial,
        "plan_vigente",
        lambda db, tid: _coro({"codigo": "business", "permite_help": True}),
    )

    payload_2mb = b'{"descripcion":"' + (b"x" * 2_000_000) + b'"}'
    payload_6mb = b'{"descripcion":"' + (b"y" * 4_500_000) + b'"}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        # 1. GitHub rechaza 2 MB (> 1 MB) con 413 PAYLOAD_EXCESIVO
        resp_gh_grande = await cliente.post(
            f"/api/v1/webhooks/github/{ctx['token_gh']}",
            content=payload_2mb,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": firmas.firmar_payload(
                    ctx["secreto_inicial"], payload_2mb, ahora_ts
                ),
                "X-Webhook-Timestamp": ahora_ts,
                "X-GitHub-Delivery": "deliv-gh-2mb",
            },
        )
        assert resp_gh_grande.status_code == 413
        assert resp_gh_grande.json()["error"]["codigo"] == "PAYLOAD_EXCESIVO"

        # 2. Kallicode Help ACEPTA 2 MB (< 4 MB) porque incluye evidencia adjunta
        resp_help_ok = await cliente.post(
            f"/api/v1/webhooks/help/{ctx['token_help']}",
            content=payload_2mb,
            headers={
                "Content-Type": "application/json",
                "X-KC-Signature": firmas.firmar_payload(
                    ctx["secreto_inicial"], payload_2mb, ahora_ts
                ),
                "X-Webhook-Timestamp": ahora_ts,
                "X-KC-Delivery": "deliv-help-2mb",
            },
        )
        assert resp_help_ok.status_code == 202

        # 3. Kallicode Help rechaza 4.5 MB (> 4 MB) con 413 PAYLOAD_EXCESIVO
        resp_help_6mb = await cliente.post(
            f"/api/v1/webhooks/help/{ctx['token_help']}",
            content=payload_6mb,
            headers={
                "Content-Type": "application/json",
                "X-KC-Signature": firmas.firmar_payload(
                    ctx["secreto_inicial"], payload_6mb, ahora_ts
                ),
                "X-Webhook-Timestamp": ahora_ts,
                "X-KC-Delivery": "deliv-help-6mb",
            },
        )
        assert resp_help_6mb.status_code == 413
        assert resp_help_6mb.json()["error"]["codigo"] == "PAYLOAD_EXCESIVO"


async def _coro(valor):
    return valor


@pytest.mark.integracion
async def test_rotacion_secreto_convivencia_ambos_validos_hasta_expirar(
    entorno_webhooks,
) -> None:
    """KC-BE-07-2: tras rotar el secreto, tanto el nuevo como el antiguo siguen aceptándose
    durante la ventana de convivencia y el antiguo falla al expirar."""
    ctx = entorno_webhooks
    ahora_ts = str(int(datetime.now(UTC).timestamp()))

    with patch("kallicode_api.routers.connections.registrar_evento", return_value="sello_mock"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
            # 1. Rotar secreto con ventana de convivencia de 600s
            resp_rot = await cliente.post(
                f"/api/v1/connections/{ctx['conn_gh_id']}/webhook-secret/rotate",
                headers=ctx["auth_headers"],
                json={"ventana_convivencia_s": 600},
            )
            assert resp_rot.status_code == 200, resp_rot.text
            data_rot = resp_rot.json()
            assert data_rot["convivencia_activa"] is True
            secreto_nuevo = data_rot["webhook_secret"]
            assert secreto_nuevo != ctx["secreto_inicial"]

            # 2. Entrega firmada con el SECRETO ANTIGUO sigue siendo aceptada (202)
            cuerpo_ant = b'{"event":"push","ref":"refs/heads/main"}'
            resp_con_antiguo = await cliente.post(
                f"/api/v1/webhooks/github/{ctx['token_gh']}",
                content=cuerpo_ant,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": firmas.firmar_payload(
                        ctx["secreto_inicial"], cuerpo_ant, ahora_ts
                    ),
                    "X-Webhook-Timestamp": ahora_ts,
                    "X-GitHub-Delivery": "deliv-rot-antiguo-1",
                    "X-GitHub-Event": "push",
                },
            )
            assert resp_con_antiguo.status_code == 202

            # 3. Entrega firmada con el SECRETO NUEVO también es aceptada (202)
            cuerpo_nvo = b'{"event":"push","ref":"refs/heads/develop"}'
            resp_con_nuevo = await cliente.post(
                f"/api/v1/webhooks/github/{ctx['token_gh']}",
                content=cuerpo_nvo,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": firmas.firmar_payload(
                        secreto_nuevo, cuerpo_nvo, ahora_ts
                    ),
                    "X-Webhook-Timestamp": ahora_ts,
                    "X-GitHub-Delivery": "deliv-rot-nuevo-1",
                    "X-GitHub-Event": "push",
                },
            )
            assert resp_con_nuevo.status_code == 202

            # 4. Expirar la ventana de convivencia del secreto antiguo en bóveda
            secreto_boveda = await boveda.leer(ctx["tenant_id"], f"cn--{ctx['conn_gh_id']}")
            assert secreto_boveda is not None
            secreto_boveda["webhook_secret_anterior_expira_en"] = (
                datetime.now(UTC) - timedelta(seconds=5)
            ).isoformat()
            await boveda.guardar(ctx["tenant_id"], f"cn--{ctx['conn_gh_id']}", secreto_boveda)

            # 5. Ahora el secreto antiguo debe ser rechazado con 401
            resp_expirado = await cliente.post(
                f"/api/v1/webhooks/github/{ctx['token_gh']}",
                content=cuerpo_ant,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": firmas.firmar_payload(
                        ctx["secreto_inicial"], cuerpo_ant, ahora_ts
                    ),
                    "X-Webhook-Timestamp": ahora_ts,
                    "X-GitHub-Delivery": "deliv-rot-antiguo-expirado",
                    "X-GitHub-Event": "push",
                },
            )
            assert resp_expirado.status_code == 401
            assert resp_expirado.content == b""


@pytest.mark.integracion
async def test_rate_limit_por_conexion_devuelve_429_con_retry_after(
    entorno_webhooks,
    monkeypatch,
) -> None:
    """KC-BE-07-2: superar el límite de peticiones por conexión devuelve 429 con cabecera Retry-After."""
    ctx = entorno_webhooks
    ahora_ts = str(int(datetime.now(UTC).timestamp()))

    # Configurar límite bajo (2 peticiones/minuto) para la prueba
    settings_actual = comercial.get_settings()
    monkeypatch.setattr(settings_actual, "rate_limit_webhook", 2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        for idx in range(2):
            cuerpo = json.dumps({"seq": idx}).encode("utf-8")
            resp = await cliente.post(
                f"/api/v1/webhooks/github/{ctx['token_gh']}",
                content=cuerpo,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": firmas.firmar_payload(
                        ctx["secreto_inicial"], cuerpo, ahora_ts
                    ),
                    "X-Webhook-Timestamp": ahora_ts,
                    "X-GitHub-Delivery": f"deliv-rl-{idx}",
                    "X-GitHub-Event": "ping",
                },
            )
            assert resp.status_code == 202

        # La 3ra petición supera el límite de 2 -> 429 con cabecera Retry-After
        cuerpo_exceso = b'{"seq": 99}'
        resp_429 = await cliente.post(
            f"/api/v1/webhooks/github/{ctx['token_gh']}",
            content=cuerpo_exceso,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": firmas.firmar_payload(
                    ctx["secreto_inicial"], cuerpo_exceso, ahora_ts
                ),
                "X-Webhook-Timestamp": ahora_ts,
                "X-GitHub-Delivery": "deliv-rl-exceso",
                "X-GitHub-Event": "ping",
            },
        )
        assert resp_429.status_code == 429
        assert resp_429.json()["error"]["codigo"] == "RATE_LIMIT_EXCEDIDO"
        assert "Retry-After" in resp_429.headers
        assert int(resp_429.headers["Retry-After"]) >= 1
