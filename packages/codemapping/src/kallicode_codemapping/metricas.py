"""Métricas Prometheus/OpenMetrics para el paquete y worker de CodeMapping (KC-CM-10-3).

Etiquetas estrictamente acotadas (baja cardinalidad):
- Nunca incluye `tenant_id`, `repo_id` ni `commit_sha` en labels.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY_CODEMAPPING = CollectorRegistry(auto_describe=True)

DURACION_FASE_INGESTA = Histogram(
    "kc_codemapping_ingesta_fase_duracion_segundos",
    "Duración de ejecución por fase del pipeline de ingesta de CodeMapping.",
    labelnames=("fase",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 180.0),
    registry=REGISTRY_CODEMAPPING,
)

EVENTOS_PROCESADOS_TOTAL = Counter(
    "kc_codemapping_worker_eventos_total",
    "Total de eventos procesados por el worker de ingesta según su desenlace.",
    labelnames=("resultado",),
    registry=REGISTRY_CODEMAPPING,
)

ERRORES_INGESTA_TOTAL = Counter(
    "kc_codemapping_worker_errores_total",
    "Total de errores ocurridos durante el procesamiento de ingesta por fase.",
    labelnames=("fase",),
    registry=REGISTRY_CODEMAPPING,
)

LONGITUD_COLA_STREAM = Gauge(
    "kc_codemapping_stream_longitud",
    "Longitud actual del stream principal de ingesta en Redis.",
    registry=REGISTRY_CODEMAPPING,
)

LONGITUD_COLA_REINTENTOS = Gauge(
    "kc_codemapping_reintentos_cola_longitud",
    "Cantidad de eventos programados en la cola de reintentos con backoff.",
    registry=REGISTRY_CODEMAPPING,
)

MENSAJES_DLQ_TOTAL = Gauge(
    "kc_codemapping_dlq_mensajes",
    "Cantidad acumulada o actual de mensajes enviados a la Dead Letter Queue.",
    registry=REGISTRY_CODEMAPPING,
)

LATENCIA_EMBEDDINGS_TEI = Histogram(
    "kc_codemapping_tei_embeddings_duracion_segundos",
    "Latencia de llamadas al servicio de embeddings TEI.",
    labelnames=("operacion",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY_CODEMAPPING,
)


@contextmanager
def medir_fase(fase: str) -> Iterator[None]:
    """Context manager para registrar la duración de una fase de ingesta."""
    inicio = time.perf_counter()
    try:
        yield
    except Exception:
        ERRORES_INGESTA_TOTAL.labels(fase=fase).inc()
        raise
    finally:
        DURACION_FASE_INGESTA.labels(fase=fase).observe(max(0.0, time.perf_counter() - inicio))


@contextmanager
def medir_embedding_tei(operacion: str = "embed") -> Iterator[None]:
    """Context manager para registrar latencia de embeddings TEI."""
    inicio = time.perf_counter()
    try:
        yield
    finally:
        LATENCIA_EMBEDDINGS_TEI.labels(operacion=operacion).observe(
            max(0.0, time.perf_counter() - inicio)
        )


def actualizar_estado_colas(
    redis_cliente: Any, stream: str, stream_reintentos: str, dlq: str
) -> None:
    """Actualiza los gauges de longitud de cola principal, reintentos y DLQ desde Redis."""
    try:
        LONGITUD_COLA_STREAM.set(int(redis_cliente.xlen(stream)))
        LONGITUD_COLA_REINTENTOS.set(int(redis_cliente.zcard(stream_reintentos)))
        MENSAJES_DLQ_TOTAL.set(int(redis_cliente.xlen(dlq)))
    except Exception:
        pass


def renderizar_metrics() -> tuple[bytes, str]:
    """Genera el payload en formato OpenMetrics/Prometheus y su Content-Type."""
    return generate_latest(REGISTRY_CODEMAPPING), CONTENT_TYPE_LATEST


class _HandlerMetrics(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/metrics", "/metrics/"):
            self.send_response(404)
            self.end_headers()
            return
        payload, content_type = renderizar_metrics()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def iniciar_servidor_metrics(host: str = "0.0.0.0", port: int = 9108) -> ThreadingHTTPServer:
    """Inicia en segundo plano el servidor HTTP de /metrics para Prometheus."""
    servidor = ThreadingHTTPServer((host, port), _HandlerMetrics)
    hilo = threading.Thread(target=servidor.serve_forever, name="cm-metrics-http", daemon=True)
    hilo.start()
    return servidor
