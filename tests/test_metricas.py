"""KC-BE-10-3: scrape privado, atribución por tier y conciliación."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
import yaml
from kallicode_api import metricas
from kallicode_api.conciliar_llm import conciliar
from kallicode_api.main import app as publica
from kallicode_api.main_interna import app as interna
from kallicode_core import comercial
from kallicode_core.config import PrecioLLM, get_settings
from kallicode_core.db import sesion_sistema, todos
from kallicode_core.llm import cliente
from kallicode_core.llm.tiers import Tier
from kallicode_core.seguridad import emitir_svc_token
from kallicode_core.telemetria import (
    CLAVE_AUDITORIA,
    CLAVE_ESCALADOS,
    CLAVE_HTTP,
    CLAVE_ORQUESTADOR,
    PREFIJO_DLQ,
    STREAM_DLQ,
    STREAMS,
)
from kallicode_workers import main as workers
from kallicode_workers.orquestador._comun import paso_dict
from prometheus_client.parser import text_string_to_metric_families
from redis.asyncio import Redis
from sqlalchemy import text


def _muestras(respuesta: httpx.Response) -> dict:
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.headers["content-type"].startswith("text/plain; version=")
    return {
        (muestra.name, frozenset(muestra.labels.items())): muestra.value
        for metrica in text_string_to_metric_families(respuesta.text)
        for muestra in metrica.samples
    }


def test_conciliacion_exige_desviacion_inferior_al_diez_por_ciento():
    proveedor = dict.fromkeys(Tier, Decimal(100))
    estimados = [{"tier": t, "coste": Decimal(109)} for t in Tier]
    assert conciliar(estimados, proveedor)
    estimados[0]["coste"] = Decimal(110)
    assert not conciliar(estimados, proveedor)


def test_reglas_de_alerta_referencian_las_tres_senales():
    ruta = Path(__file__).resolve().parents[1] / "infra/prometheus/backend-alertas.yml"
    reglas = yaml.safe_load(ruta.read_text())["groups"][0]["rules"]
    assert len(reglas) == 3
    assert "kc_auditoria_cadenas_rotas > 0" in reglas[0]["expr"]
    assert 'kc_stream_longitud{stream="kc:dlq"}[10m]' in reglas[1]["expr"]
    assert "kc_llm_tickets_cerrados_sobrecoste > 0" in reglas[2]["expr"]


async def test_paso_agotado_persiste_consumo_aunque_no_haya_resultado(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_max_reintentos_esquema", 1)
    monkeypatch.setattr(cliente, "_obtener_cliente_http", object)
    monkeypatch.setattr(cliente, "_llamar", AsyncMock(return_value=("{}", 13, 5)))
    monkeypatch.setattr(cliente, "_circuitos", {})
    guardar = AsyncMock(return_value=True)
    monkeypatch.setattr(cliente, "_persistir_consumos", guardar)

    with pytest.raises(cliente.LLMEscaladoHumano):
        await cliente.ejecutar_paso(
            "design.adversarial_review", {}, tenant_id="org_test", ticket_id="tk_test"
        )

    guardar.assert_awaited_once()
    tenant, ticket, etapa, intentos = guardar.await_args.args
    assert (tenant, ticket, etapa) == ("org_test", "tk_test", "design")
    assert [(i.tier, i.entrada, i.salida) for i in intentos] == [(Tier.FABLE, 13, 5)]


async def test_scrape_parcial_con_el_estado_de_cada_fuente(monkeypatch):
    async def fallo():
        raise TimeoutError

    async def ok():
        return []

    monkeypatch.setattr(metricas, "_series_redis", fallo)
    monkeypatch.setattr(metricas, "_series_postgres", ok)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=interna), base_url="http://testserver"
    ) as api:
        muestras = _muestras(await api.get("/metrics"))
    assert muestras["kc_metricas_fuente_disponible", frozenset({("fuente", "redis")})] == 0
    assert muestras["kc_metricas_fuente_disponible", frozenset({("fuente", "postgres")})] == 1


@pytest.fixture
async def redis_metricas(monkeypatch):
    # La DB 15 se reserva para esta prueba local. Nunca se limpian datos previos.
    s = get_settings()
    origen = urlsplit(s.redis_url)
    query = urlencode([(k, v) for k, v in parse_qsl(origen.query) if k != "db"])
    url = origen._replace(path="/15", query=query)
    async with Redis.from_url(url.geturl(), password=s.redis_password, decode_responses=True) as r:
        assert not await r.dbsize(), (
            "La DB 15 de Redis debe estar vacía para la prueba de métricas."
        )
        monkeypatch.setattr(comercial, "redis_cliente", lambda: r)
        monkeypatch.setattr(workers, "redis_cliente", lambda: r)
        try:
            yield r
        finally:
            reintentos = [clave async for clave in r.scan_iter("kc:retries:kc:inbox:*")]
            await r.delete(
                CLAVE_AUDITORIA,
                CLAVE_ESCALADOS,
                CLAVE_HTTP,
                CLAVE_ORQUESTADOR,
                "kc:inbox",
                STREAM_DLQ,
                f"{PREFIJO_DLQ}kc:inbox",
                *reintentos,
            )


@pytest.fixture
async def datos_metricas():
    tenant, ticket, previo, job = (uuid4().hex for _ in range(4))
    params = {"tenant": tenant, "ticket": ticket, "previo": previo, "job": job}
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:tenant, 'Prueba métricas')"),
            params,
        )
        await db.execute(
            text("""
            INSERT INTO core.tickets
                (id, tenant_id, titulo, descripcion, tipo, etapa, cerrado_en)
            VALUES
                (:ticket, :tenant, 'Actual', 'Métricas', 'bug', 'triage', NULL),
                (:previo, :tenant, 'Previo', 'Métricas', 'bug', 'produccion', now() - interval '2 days')
        """),
            params,
        )
        await db.execute(
            text("""
            INSERT INTO core.usage_tokens
                (tenant_id, ticket_id, etapa, tier, modelo, tokens_in, tokens_out, duracion_ms, creado_en)
            VALUES (:tenant, :previo, 'triage', 'flash', 'flash-test', 1000000, 0, 100, now() - interval '2 days')
        """),
            params,
        )
        await db.execute(
            text("""
            INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
            VALUES (:job, :tenant, :ticket, 'triage', 'P2')
        """),
            params,
        )
    try:
        yield params
    finally:
        await _limpiar_tenant_de_prueba(tenant)


async def _limpiar_tenant_de_prueba(tenant: str) -> None:
    params = {"tenant": tenant}
    async with sesion_sistema() as db:
        # El FK borra los llm_steps al borrar su job, respetando su política append-only.
        for tabla in (
            "core.idempotency_keys",
            "core.jobs",
            "core.tickets",
            "core.ticket_counters",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:tenant"), params)
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:tenant"), params)
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:tenant"), params)
        await db.execute(text("DELETE FROM core.organizations WHERE id=:tenant"), params)


@pytest.mark.integracion
async def test_metricas_de_api_publica_api_privada_y_worker(
    redis_metricas, datos_metricas, monkeypatch
):
    r = redis_metricas
    tenant, ticket, job = (datos_metricas[k] for k in ("tenant", "ticket", "job"))
    s = get_settings()
    monkeypatch.setattr(
        s,
        "llm_precios_usd_por_millon",
        {
            "flash": PrecioLLM(entrada=1, salida=2),
            "pro": PrecioLLM(entrada=5, salida=10),
            "fable": PrecioLLM(entrada=10, salida=20),
        },
    )
    for tier in Tier:
        monkeypatch.setattr(s, f"llm_{tier}_model", f"{tier}-test")
    monkeypatch.setattr(s, "persistir_tier_fable", True)
    monkeypatch.setattr(s, "llm_max_reintentos_esquema", 1)
    monkeypatch.setattr(cliente, "_circuitos", {})
    precio_proveedor = {
        Tier.FLASH: Decimal("0.14"),
        Tier.PRO: Decimal("1.30"),
        Tier.FABLE: Decimal("3.80"),
    }
    inicio = datetime.now(UTC)

    def proveedor(request):
        modelo = json.loads(request.content)["model"]
        if modelo == s.llm_fable_model:
            salida = {"type": "bug", "summary_en": "Issue", "symptoms": [], "confidence": 1}
            return httpx.Response(
                200,
                json={
                    "content": [{"text": json.dumps(salida)}],
                    "usage": {
                        "input_tokens": 290_000,
                        "cache_read_input_tokens": 10_000,
                        "output_tokens": 40_000,
                    },
                },
            )
        entrada, salida = (100_000, 20_000) if modelo == s.llm_flash_model else (200_000, 30_000)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": entrada, "completion_tokens": salida},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(proveedor)) as llm:
        monkeypatch.setattr(cliente, "_obtener_cliente_http", lambda: llm)
        resultado = await cliente.ejecutar_paso(
            "triage.classify", {}, tenant_id=tenant, ticket_id=ticket
        )
    assert resultado.tier is Tier.FABLE and resultado.consumo_registrado

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=publica), base_url="http://testserver"
        ) as pub,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=interna), base_url="http://testserver"
        ) as priv,
    ):
        assert not any(getattr(ruta, "path", None) == "/metrics" for ruta in publica.routes)
        assert (await pub.get("/metrics")).status_code == 404
        assert (await pub.get("/api/v1/tickets/KC-99999")).status_code == 401
        assert (await pub.post("/api/v1/webhooks/github/token-inexistente")).status_code == 401

        headers = {
            "Authorization": f"Bearer {emitir_svc_token('orquestador', tenant)}",
            "Idempotency-Key": uuid4().hex,
        }
        paso = await priv.post(
            f"/internal/v1/jobs/{job}/steps",
            json=paso_dict("triage", resultado, entrada_ref="mem://in", salida_ref="mem://out"),
            headers=headers,
        )
        assert paso.status_code == 200, paso.text

        async with sesion_sistema() as db:
            usos = await todos(
                db,
                """
                SELECT tier, tokens_in, tokens_out FROM core.usage_tokens WHERE ticket_id=:ticket
            """,
                datos_metricas,
            )
            await db.execute(
                text("""
                UPDATE core.jobs SET estado='produccion', terminado_en=now() WHERE id=:job
            """),
                datos_metricas,
            )
        assert {u["tier"]: (u["tokens_in"], u["tokens_out"]) for u in usos} == {
            "flash": (100_000, 20_000),
            "pro": (200_000, 30_000),
            "fable": (300_000, 40_000),
        }
        assert len(usos) == 3

        await r.xgroup_create("kc:inbox", workers._GRUPO, id="0", mkstream=True)
        mid = await r.xadd("kc:inbox", {"tenant_id": tenant})
        await r.xreadgroup(workers._GRUPO, "prueba", {"kc:inbox": ">"})

        async def falla(_):
            raise RuntimeError("Error de prueba")

        for _ in range(3):
            await workers.procesar_mensaje(r, "kc:inbox", mid, {"tenant_id": tenant}, falla)
        await workers.comprobar_cadenas_auditoria()

        pulso_visible = asyncio.Event()
        pulso = workers.pulso_orquestador

        async def registrar_pulso(instancia):
            await pulso(instancia)
            pulso_visible.set()

        monkeypatch.setattr(workers, "pulso_orquestador", registrar_pulso)
        orquestador = asyncio.create_task(asyncio.Event().wait())
        publicador = asyncio.create_task(workers._publicar_pulso_orquestador(orquestador))
        try:
            await asyncio.wait_for(pulso_visible.wait(), timeout=3)
            muestras = _muestras(await priv.get("/metrics"))
        finally:
            orquestador.cancel()
            await asyncio.gather(orquestador, publicador, return_exceptions=True)
        assert _muestras(await priv.get("/metrics"))["kc_orquestador_activo", frozenset()] == 0

    def muestra(nombre, **etiquetas):
        return muestras[nombre, frozenset(etiquetas.items())]

    assert muestra("kc_metricas_fuente_disponible", fuente="redis") == 1
    assert muestra("kc_metricas_fuente_disponible", fuente="postgres") == 1
    assert (
        muestra(
            "kc_http_latencia_segundos_count",
            superficie="publica",
            ruta="/api/v1/tickets/{numero}",
            metodo="GET",
        )
        == 1
    )
    assert (
        muestra(
            "kc_http_latencia_segundos_count",
            superficie="publica",
            ruta="/api/v1/webhooks/github/{token}",
            metodo="POST",
        )
        == 1
    )
    assert (
        muestra(
            "kc_http_latencia_segundos_bucket",
            superficie="interna",
            ruta="/internal/v1/jobs/{job_id}/steps",
            metodo="POST",
            le="+Inf",
        )
        == 1
    )
    assert muestra("kc_http_errores_total", superficie="publica", codigo="NO_AUTENTICADO") == 1
    assert muestra("kc_http_errores_total", superficie="publica", codigo="FIRMA_INVALIDA") == 1
    assert muestra("kc_llm_escalados_total", tier_origen="flash", tier_destino="pro") == 1
    assert muestra("kc_llm_escalados_total", tier_origen="pro", tier_destino="fable") == 1
    assert (
        muestra("kc_llm_tokens", tenant_id=tenant, etapa="triage", tier="fable", direccion="salida")
        == 40_000
    )
    assert muestra(
        "kc_llm_coste_usd", tenant_id=tenant, etapa="triage", tier="pro"
    ) == pytest.approx(1.30)
    assert muestra(
        "kc_llm_coste_por_ticket_cerrado_usd", tenant_id=tenant, etapa="triage"
    ) == pytest.approx(3.12)
    assert muestra("kc_llm_coste_mediano_ticket_cerrado_usd", tenant_id=tenant) == 1
    assert muestra("kc_llm_tickets_cerrados_sobrecoste", tenant_id=tenant) == 1
    for stream in (*STREAMS, "kc:dlq"):
        assert muestra("kc_stream_longitud", stream=stream) == int(stream in {"kc:inbox", "kc:dlq"})
    assert muestra("kc_dlq_entradas_total", stream="kc:inbox") == 1
    assert muestra("kc_orquestador_activo") == 1
    assert muestra("kc_auditoria_ultima_verificacion_timestamp_seconds") > 0

    filas = await metricas.consultar_consumo_pipeline(
        inicio, datetime.now(UTC) + timedelta(seconds=1)
    )
    assert conciliar((fila for fila in filas if fila["tenant_id"] == tenant), precio_proveedor)
