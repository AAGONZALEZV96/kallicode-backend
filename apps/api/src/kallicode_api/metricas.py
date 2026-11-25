"""Exposición privada de telemetría de la API y los workers (KC-BE-10-3)."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import pairwise
from time import time
from typing import Any

from fastapi import Request, Response
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, todos
from kallicode_core.llm.tiers import ORDEN_ESCALADO, Tier
from kallicode_core.logging import log
from kallicode_core.telemetria import (
    BUCKETS_HTTP,
    CLAVE_AUDITORIA,
    CLAVE_ESCALADOS,
    CLAVE_HTTP,
    CLAVE_ORQUESTADOR,
    ORQUESTADOR_TTL_S,
    PREFIJO_DLQ,
    STREAM_DLQ,
    STREAMS,
    conexion_metricas,
)
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    HistogramMetricFamily,
    Metric,
)
from redis.asyncio import Redis
from sqlalchemy import text

_VENTANA_DIAS = 30

# Coste estimado en USD con las tarifas configuradas, por tier y dirección.
_TARIFAS = """
    SELECT tier, (precio->>'entrada')::numeric AS entrada, (precio->>'salida')::numeric AS salida
    FROM jsonb_each(CAST(:precios AS jsonb)) AS p(tier, precio)
"""
_COSTE = "SUM(u.tokens_in * p.entrada + u.tokens_out * p.salida) / 1000000"
_SIN_TARIFA = "(p.tier IS NULL AND (u.tokens_in > 0 OR u.tokens_out > 0))"

_SQL_CONSUMO = f"""
    WITH tarifas AS ({_TARIFAS})
    SELECT u.tenant_id, u.etapa, u.tier,
           SUM(u.tokens_in) AS entrada, SUM(u.tokens_out) AS salida,
           CASE WHEN NOT bool_or({_SIN_TARIFA}) THEN COALESCE({_COSTE}, 0) END AS coste
    FROM core.usage_tokens u
    LEFT JOIN tarifas p USING (tier)
    WHERE u.creado_en >= :desde AND u.creado_en < :hasta
      AND (NOT :solo_pipeline OR u.origen = 'pipeline')
    GROUP BY u.tenant_id, u.etapa, u.tier
"""

# Cohorte por fecha de cierre. Cada ticket conserva el coste de todos sus pasos,
# incluso si comenzaron antes de la ventana. Se publican agregados por tenant;
# los IDs de ticket se usan en SQL y quedan fuera de las series.
_SQL_CERRADOS = f"""
    WITH tarifas AS ({_TARIFAS}), por_etapa AS (
        SELECT t.tenant_id, t.id, t.cerrado_en, u.etapa,
               COALESCE({_COSTE}, 0) AS coste,
               COALESCE(bool_or({_SIN_TARIFA}), false) AS sin_tarifa
        FROM core.tickets t
        LEFT JOIN core.usage_tokens u
            ON u.tenant_id = t.tenant_id AND u.ticket_id = t.id AND u.creado_en < :hasta
        LEFT JOIN tarifas p ON p.tier = u.tier
        WHERE t.cerrado_en >= :desde AND t.cerrado_en < :hasta
        GROUP BY t.tenant_id, t.id, t.cerrado_en, u.etapa
    ), por_ticket AS (
        SELECT tenant_id, id, cerrado_en, SUM(coste) AS coste
        FROM por_etapa
        GROUP BY tenant_id, id, cerrado_en
        HAVING NOT bool_or(sin_tarifa)
    ), por_tenant AS (
        SELECT tenant_id, count(*) AS tickets,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY coste)
                    FILTER (WHERE cerrado_en < :recientes) AS mediana
        FROM por_ticket GROUP BY tenant_id
    ), fuera_de_umbral AS (
        SELECT t.tenant_id, count(*) FILTER (
            WHERE t.cerrado_en >= :recientes AND t.coste > 2 * m.mediana
        ) AS sobrecostes
        FROM por_ticket t JOIN por_tenant m USING (tenant_id)
        WHERE m.mediana IS NOT NULL
        GROUP BY t.tenant_id
    ), promedios AS (
        SELECT a.tenant_id, a.etapa, SUM(a.coste) AS coste
        FROM por_etapa a JOIN por_ticket t USING (tenant_id, id)
        WHERE a.etapa IS NOT NULL
        GROUP BY a.tenant_id, a.etapa
        UNION ALL
        SELECT tenant_id, 'total', SUM(coste) FROM por_ticket GROUP BY tenant_id
    )
    SELECT p.tenant_id, p.etapa, p.coste / m.tickets AS coste_medio,
           m.tickets, m.mediana, f.sobrecostes
    FROM promedios p JOIN por_tenant m USING (tenant_id)
    LEFT JOIN fuera_de_umbral f USING (tenant_id)
"""


class _Exposicion:
    """Registro efímero para que las etiquetas desaparezcan al salir de la ventana."""

    def __init__(self, metricas: list[Metric]):
        self.metricas = metricas

    def collect(self) -> Iterator[Metric]:
        yield from self.metricas


def _series_http(datos: dict[str, str]) -> list[Metric]:
    latencia = HistogramMetricFamily(
        "kc_http_latencia_segundos",
        "Tiempo hasta la respuesta HTTP.",
        labels=["superficie", "ruta", "metodo"],
    )
    errores = CounterMetricFamily(
        "kc_http_errores",
        "Respuestas HTTP de error por código.",
        labels=["superficie", "codigo"],
    )
    buckets: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    for clave, valor in datos.items():
        partes = clave.split("|")
        if partes[0] == "error":
            errores.add_metric(partes[1:], int(valor))
        else:
            superficie, metodo, ruta, bucket = partes
            buckets[superficie, ruta, metodo][bucket] = valor

    for etiquetas, valores in buckets.items():
        acumulado = 0
        muestras = []
        for indice, limite in enumerate((*map(str, BUCKETS_HTTP), "+Inf")):
            acumulado += int(valores.get(str(indice), 0))
            muestras.append((limite, acumulado))
        latencia.add_metric(etiquetas, muestras, float(valores["suma"]))
    return [latencia, errores]


async def _series_redis(redis: Redis | None) -> list[Metric]:
    # Una ida a Redis toma la misma vista de contadores y colas para todas las réplicas.
    async with conexion_metricas(redis) as cliente, cliente.pipeline(transaction=False) as pipe:
        pipe.hgetall(CLAVE_HTTP)
        pipe.hgetall(CLAVE_ESCALADOS)
        pipe.hgetall(CLAVE_AUDITORIA)
        pipe.zcount(CLAVE_ORQUESTADOR, time() - ORQUESTADOR_TTL_S, "+inf")
        pipe.mget([f"{PREFIJO_DLQ}{stream}" for stream in STREAMS])
        for stream in (*STREAMS, STREAM_DLQ):
            pipe.xlen(stream)
        http, escalados, auditoria, replicas, dlq, *longitudes = await pipe.execute()

    saltos = CounterMetricFamily(
        "kc_llm_escalados",
        "Cambios de tier durante la ejecución.",
        labels=["tier_origen", "tier_destino"],
    )
    for origen, destino in pairwise(ORDEN_ESCALADO):
        saltos.add_metric([origen, destino], int(escalados.get(f"{origen}|{destino}", 0)))

    colas = GaugeMetricFamily(
        "kc_stream_longitud", "Entradas retenidas en el stream (XLEN).", labels=["stream"]
    )
    for stream, longitud in zip((*STREAMS, STREAM_DLQ), longitudes, strict=True):
        colas.add_metric([stream], longitud)

    entradas_dlq = CounterMetricFamily(
        "kc_dlq_entradas", "Mensajes enviados a la DLQ.", labels=["stream"]
    )
    for stream, cantidad in zip(STREAMS, dlq, strict=True):
        entradas_dlq.add_metric([stream], int(cantidad or 0))

    series = [
        *_series_http(http),
        saltos,
        colas,
        entradas_dlq,
        GaugeMetricFamily(
            "kc_orquestador_activo", "Hay al menos un pulso vigente.", value=int(replicas > 0)
        ),
        GaugeMetricFamily("kc_orquestador_replicas", "Réplicas con pulso vigente.", value=replicas),
    ]
    if auditoria:
        series.extend(
            (
                GaugeMetricFamily(
                    "kc_auditoria_cadenas_rotas",
                    "Cadenas rotas en la última verificación.",
                    value=int(auditoria["rotas"]),
                ),
                GaugeMetricFamily(
                    "kc_auditoria_ultima_verificacion_timestamp_seconds",
                    "Fecha de la última verificación de auditoría (Unix).",
                    value=float(auditoria["verificada_en"]),
                ),
            )
        )
    return series


def _series_costes(
    consumos: list[dict], cerrados: list[dict], precios: dict[str, Any]
) -> list[Metric]:
    etiquetas = ["tenant_id", "etapa", "tier"]
    tokens = GaugeMetricFamily(
        "kc_llm_tokens", "Tokens registrados en la ventana móvil.", labels=[*etiquetas, "direccion"]
    )
    coste = GaugeMetricFamily(
        "kc_llm_coste_usd", "Coste estimado de la ventana móvil.", labels=etiquetas
    )
    for fila in consumos:
        claves = [fila[label] for label in etiquetas]
        for direccion in ("entrada", "salida"):
            tokens.add_metric([*claves, direccion], float(fila[direccion]))
        if fila["coste"] is not None:
            coste.add_metric(claves, float(fila["coste"]))

    configurado = GaugeMetricFamily(
        "kc_llm_precio_configurado", "Disponibilidad de la tarifa por tier.", labels=["tier"]
    )
    for tier in Tier:
        configurado.add_metric([tier], int(tier in precios))

    promedio = GaugeMetricFamily(
        "kc_llm_coste_por_ticket_cerrado_usd",
        "Coste medio por etapa y ticket cerrado con tarifa en la ventana.",
        labels=["tenant_id", "etapa"],
    )
    tickets = GaugeMetricFamily(
        "kc_llm_tickets_cerrados_valorados",
        "Tickets cerrados con coste valorado.",
        labels=["tenant_id"],
    )
    mediana = GaugeMetricFamily(
        "kc_llm_coste_mediano_ticket_cerrado_usd",
        "Mediana de tickets cerrados en la ventana, excluidas las últimas 24 horas.",
        labels=["tenant_id"],
    )
    sobrecostes = GaugeMetricFamily(
        "kc_llm_tickets_cerrados_sobrecoste",
        "Tickets cerrados en las últimas 24 horas cuyo coste supera el doble de la mediana previa.",
        labels=["tenant_id"],
    )
    for fila in cerrados:
        tenant, etapa = fila["tenant_id"], fila["etapa"]
        promedio.add_metric([tenant, etapa], float(fila["coste_medio"]))
        if etapa == "total":
            tickets.add_metric([tenant], fila["tickets"])
            if fila["mediana"] is not None:
                mediana.add_metric([tenant], float(fila["mediana"]))
                sobrecostes.add_metric([tenant], fila["sobrecostes"])
    return [tokens, coste, configurado, promedio, tickets, mediana, sobrecostes]


def _tarifas_configuradas() -> dict[str, dict[str, str]]:
    return {
        tier: precio.model_dump(mode="json")
        for tier, precio in get_settings().llm_precios_usd_por_millon.items()
    }


async def consultar_consumo_pipeline(desde: datetime, hasta: datetime) -> list[dict]:
    """Devuelve el consumo que corresponde al informe del proveedor del pipeline."""
    async with sesion_sistema() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        return await todos(
            db,
            _SQL_CONSUMO,
            {
                "desde": desde,
                "hasta": hasta,
                "precios": json.dumps(_tarifas_configuradas()),
                "solo_pipeline": True,
            },
        )


async def _series_postgres() -> list[Metric]:
    hasta = datetime.now(UTC)
    desde = hasta - timedelta(days=_VENTANA_DIAS)
    precios = _tarifas_configuradas()
    params = {"desde": desde, "hasta": hasta, "precios": json.dumps(precios)}
    async with sesion_sistema() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(text("SET LOCAL statement_timeout = '5s'"))
        consumos = await todos(db, _SQL_CONSUMO, params | {"solo_pipeline": False})
        cerrados = await todos(db, _SQL_CERRADOS, params | {"recientes": hasta - timedelta(days=1)})

    return [
        *_series_costes(consumos, cerrados, precios),
        GaugeMetricFamily(
            "kc_llm_ventana_inicio_timestamp_seconds",
            "Inicio de la ventana (Unix).",
            value=desde.timestamp(),
        ),
        GaugeMetricFamily(
            "kc_llm_ventana_fin_timestamp_seconds",
            "Fin exclusivo de la ventana (Unix).",
            value=hasta.timestamp(),
        ),
    ]


async def _leer_fuente(fuente, leer, segundos: float) -> tuple[str, list[Metric] | None]:
    try:
        async with asyncio.timeout(segundos):
            return fuente, await leer()
    except Exception as exc:
        # Una fuente caída deja visibles las otras; los datos ausentes no se publican como ceros.
        log.warning("metricas.fuente_no_disponible", fuente=fuente, error=type(exc).__name__)
        return fuente, None


async def metrics(request: Request) -> Response:
    disponible = GaugeMetricFamily(
        "kc_metricas_fuente_disponible",
        "La fuente respondió durante la consulta.",
        labels=["fuente"],
    )
    metricas = []
    for fuente, series in await asyncio.gather(
        _leer_fuente(
            "redis", partial(_series_redis, getattr(request.state, "redis_metricas", None)), 2
        ),
        _leer_fuente("postgres", _series_postgres, 6),
    ):
        disponible.add_metric([fuente], int(series is not None))
        metricas.extend(series or [])
    registro = CollectorRegistry()
    registro.register(_Exposicion([disponible, *metricas]))
    return Response(generate_latest(registro), media_type=CONTENT_TYPE_LATEST)
