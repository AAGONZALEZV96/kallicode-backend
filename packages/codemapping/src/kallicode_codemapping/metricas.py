"""Métricas Prometheus/OpenMetrics para el paquete y worker de CodeMapping (KC-CM-10-3).

Etiquetas estrictamente acotadas (baja cardinalidad):
- Nunca incluye `tenant_id`, `repo_id` ni `commit_sha` en labels.
"""

from __future__ import annotations

import logging
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


# ---------------------------------------------------------------------------
# Índices HNSW y vigencia de los embeddings (KC-CM-11-4). Etiquetas acotadas: `indice` (los tres de
# vec.hnsw_catalogo), `capa` (symbol, definition) y `motivo` (obsoleto, modelo, sin_embedding).
# ---------------------------------------------------------------------------
UMBRAL_VIGENCIA = 0.99

HNSW_TAMANO_BYTES = Gauge(
    "kc_codemapping_hnsw_tamano_bytes",
    "Tamaño de cada índice HNSW de vec.* (suma de sus particiones).",
    labelnames=("indice",),
    registry=REGISTRY_CODEMAPPING,
)

HNSW_HINCHAZON_PCT = Gauge(
    "kc_codemapping_hnsw_hinchazon_porcentaje",
    "Bytes de índice por fila viva frente a la línea base tras la última construcción o REINDEX.",
    labelnames=("indice",),
    registry=REGISTRY_CODEMAPPING,
)

HNSW_MUERTAS_PCT = Gauge(
    "kc_codemapping_hnsw_tuplas_muertas_porcentaje",
    "Tuplas muertas de la tabla del índice HNSW pendientes de VACUUM.",
    labelnames=("indice",),
    registry=REGISTRY_CODEMAPPING,
)

HNSW_SEGUNDOS_DESDE_VACUUM = Gauge(
    "kc_codemapping_hnsw_segundos_desde_vacuum",
    "Segundos desde el último VACUUM (manual o automático) de la tabla del índice HNSW.",
    labelnames=("indice",),
    registry=REGISTRY_CODEMAPPING,
)

EMBEDDINGS_VIGENCIA_RATIO = Gauge(
    "kc_codemapping_embeddings_vigencia_ratio",
    "Fracción de nodos cuyo embedding corresponde a su texto vigente en cm.* (todos los tenants).",
    labelnames=("capa",),
    registry=REGISTRY_CODEMAPPING,
)

EMBEDDINGS_NO_VIGENTES = Gauge(
    "kc_codemapping_embeddings_no_vigentes",
    "Nodos con embedding obsoleto o sin embedding (todos los tenants).",
    labelnames=("capa", "estado"),
    registry=REGISTRY_CODEMAPPING,
)

EMBEDDINGS_TENANTS_BAJO_UMBRAL = Gauge(
    "kc_codemapping_embeddings_tenants_bajo_umbral",
    f"Tenants con vigencia de embeddings por debajo de {UMBRAL_VIGENCIA:.0%}.",
    labelnames=("capa",),
    registry=REGISTRY_CODEMAPPING,
)

EMBEDDINGS_REEMBEBIDOS_TOTAL = Counter(
    "kc_codemapping_embeddings_reembebidos_total",
    "Embeddings regenerados por el paso de reparación de la vigencia, por motivo.",
    labelnames=("motivo",),
    registry=REGISTRY_CODEMAPPING,
)

VIGENCIA_TRAS_REFRESCO = Histogram(
    "kc_codemapping_embeddings_vigencia_tras_refresco",
    "Vigencia de los embeddings del tenant al terminar cada refresco.",
    buckets=(0.5, 0.8, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999, 1.0),
    registry=REGISTRY_CODEMAPPING,
)

SQL_ESTADO_HNSW = (
    "SELECT indice, tamano_bytes, hinchazon_pct, muertas_pct,"
    " extract(epoch FROM now() - ultimo_vacuum) FROM vec.estado_hnsw()"
)
SQL_VIGENCIA_GLOBAL = (
    "SELECT capa, ratio, obsoletos, sin_embedding, tenants_bajo_umbral"
    " FROM vec.vigencia_global(%s::text, %s::numeric)"
)


def registrar_reparacion_vigencia(motivos: dict[str, int], ratio: float) -> None:
    """Contabiliza un paso de reparación de vigencia (vigencia.reparar_vigencia)."""
    for motivo, n in motivos.items():
        EMBEDDINGS_REEMBEBIDOS_TOTAL.labels(motivo=motivo).inc(n)
    VIGENCIA_TRAS_REFRESCO.observe(ratio)


def actualizar_metricas_vectoriales(conn: Any, modelo: str = "bge-m3") -> None:
    """Actualiza los gauges de HNSW y de vigencia desde vec.estado_hnsw() y vec.vigencia_global().

    `conn` es una conexión psycopg (cualquier rol con EXECUTE sobre las dos funciones, migración 060).
    Una métrica sin valor (índice inexistente, sin línea base) no se publica.
    """
    for indice, tamano, hinchazon, muertas, desde_vacuum in conn.execute(SQL_ESTADO_HNSW):
        if tamano is not None:
            HNSW_TAMANO_BYTES.labels(indice=indice).set(float(tamano))
        if hinchazon is not None:
            HNSW_HINCHAZON_PCT.labels(indice=indice).set(float(hinchazon))
        if muertas is not None:
            HNSW_MUERTAS_PCT.labels(indice=indice).set(float(muertas))
        if desde_vacuum is not None:
            HNSW_SEGUNDOS_DESDE_VACUUM.labels(indice=indice).set(float(desde_vacuum))
    for capa, ratio, obsoletos, sin_embedding, bajo_umbral in conn.execute(
        SQL_VIGENCIA_GLOBAL, (modelo, UMBRAL_VIGENCIA)
    ):
        EMBEDDINGS_VIGENCIA_RATIO.labels(capa=capa).set(float(ratio))
        EMBEDDINGS_NO_VIGENTES.labels(capa=capa, estado="obsoleto").set(float(obsoletos))
        EMBEDDINGS_NO_VIGENTES.labels(capa=capa, estado="sin_embedding").set(float(sin_embedding))
        EMBEDDINGS_TENANTS_BAJO_UMBRAL.labels(capa=capa).set(float(bajo_umbral))


def refrescar_metricas_vectoriales(dsn: str, modelo: str = "bge-m3") -> bool:
    """Abre una conexión breve y actualiza las métricas vectoriales; un fallo no detiene al worker."""
    import psycopg

    esquema, sep, resto = dsn.partition("://")
    libpq = f"{esquema.split('+', 1)[0]}://{resto}" if sep else dsn
    try:
        with psycopg.connect(libpq, connect_timeout=5, autocommit=True) as conn:
            conn.execute("SET statement_timeout = '30s'")
            actualizar_metricas_vectoriales(conn, modelo)
        return True
    except Exception:
        logging.getLogger("codemapping").warning(
            "no se pudieron leer las métricas vectoriales", exc_info=True
        )
        return False


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


# ---------------------------------------------------------------------------
# Cierre transitivo materializado e impacto (KC-CM-11-2)
# Etiquetas acotadas: `operacion` (impacto, dependencias), `fuente` (materializada, recursiva),
# `truncado` (true, false), `estado` (vigente, excedida, error).
# ---------------------------------------------------------------------------
IMPACTO_CONSULTAS_TOTAL = Counter(
    "kc_codemapping_impacto_consultas_total",
    "Total de consultas de impacto/dependencias por ruta ejecutada (materializada vs recursiva).",
    labelnames=("operacion", "fuente", "truncado"),
    registry=REGISTRY_CODEMAPPING,
)

IMPACTO_DURACION_SEGUNDOS = Histogram(
    "kc_codemapping_impacto_duracion_segundos",
    "Latencia de ejecución de consultas de impacto y dependencias.",
    labelnames=("operacion", "fuente"),
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=REGISTRY_CODEMAPPING,
)

REACHABILITY_REFRESCO_TOTAL = Counter(
    "kc_codemapping_reachability_refresco_total",
    "Total de ejecuciones de cm.refrescar_reachability() en el refresco diario, por estado.",
    labelnames=("estado",),
    registry=REGISTRY_CODEMAPPING,
)

REACHABILITY_REFRESCO_DURACION_SEGUNDOS = Histogram(
    "kc_codemapping_reachability_refresco_duracion_segundos",
    "Duración de cm.refrescar_reachability() durante el refresco incremental.",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0),
    registry=REGISTRY_CODEMAPPING,
)


def registrar_consulta_impacto(
    operacion: str, fuente: str, truncado: bool, duracion_segundos: float
) -> None:
    """Registra ruta ejecutada (materializada o caída al CTE recursivo) y latencia."""
    IMPACTO_CONSULTAS_TOTAL.labels(
        operacion=operacion,
        fuente=fuente,
        truncado="true" if truncado else "false",
    ).inc()
    IMPACTO_DURACION_SEGUNDOS.labels(operacion=operacion, fuente=fuente).observe(
        max(0.0, duracion_segundos)
    )


def registrar_refresco_reachability(estado: str, duracion_segundos: float) -> None:
    """Registra resultado y duración del refresco incremental de cm.reachability."""
    REACHABILITY_REFRESCO_TOTAL.labels(estado=estado).inc()
    REACHABILITY_REFRESCO_DURACION_SEGUNDOS.observe(max(0.0, duracion_segundos))
