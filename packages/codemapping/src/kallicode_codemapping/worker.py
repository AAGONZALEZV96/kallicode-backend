"""Worker de ingesta de CodeMapping sobre Redis Streams (KC-CM-10-1 / KC-CM-10-3).

    uv run --package codemapping python -m kallicode_codemapping.worker

Consume el stream `kc:codemapping:ingesta` con un consumer group (XREADGROUP) y semántica
at-least-once: un mensaje solo se confirma (XACK) cuando quedó resuelto (procesado, omitido por
duplicado, reprogramado o enviado a la DLQ). Si el proceso muere a mitad, el mensaje queda
pendiente y otro consumidor lo recupera con XAUTOCLAIM.

Evento (campos del mensaje; todos texto):

    tenant_id  repo_id  repo_url  commit_sha  [modo = refresh | full]  [trace_id]

Idempotencia: antes de ejecutar se reserva de forma atómica la clave (tenant_id, repo_id,
commit_sha) en core.idempotency_keys (endpoint `codemapping.ingesta`, `clave` = repo_id:commit_sha),
con el mismo patrón INSERT ... ON CONFLICT DO NOTHING que usa la API (status_code 0 = en proceso,
200 = terminada). Reinyectar un evento ya procesado no vuelve a ejecutar el pipeline, así que no
duplica snapshots, nodos ni aristas. Una reserva en proceso se mantiene viva con un latido; si el
worker muere, vence a los `lease_segundos` y otro la toma (compare-and-set: gana uno solo).

Fallos: la reserva se libera y el evento se reprograma con backoff exponencial (ZSET
`kc:codemapping:ingesta:retry`, que un script Lua devuelve al stream de forma atómica). Tras
`max_intentos` —o de inmediato si el evento es inválido o el error no es recuperable— se publica en
`kc:dlq` (el mismo formato de los demás workers, más trace_id y el error estructurado) y se
confirma el original: la DLQ nunca bloquea al grupo.

Escalación humana (KC-CM-10-6): si el fallo es irrecuperable —credenciales vencidas, repositorio
corrupto, tiers del LLM agotados— o se agotaron los reintentos, antes de la DLQ el `Escalador`
pausa el snapshot del commit en `pendiente_intervencion` y pide a Backend abrir la escalación con
contexto diagnóstico y trace_id (contrato cm-escalacion-v1). Mientras el commit siga en pausa, los
eventos que lleguen para él se confirman sin ejecutar nada; `Procesador.reanudar` levanta la pausa
tras la resolución y reencola el evento.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Literal

from kallicode_core.cm_escalacion import MotivoEscalacion
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from kallicode_codemapping.escalacion import (
    ClienteEscalacion,
    ErrorNoRecuperable,  # noqa: F401 - vive en escalacion.py; reexportado para ejecutores
    Escalador,
    FabricaLoader,
    PausaSnapshots,
    ResultadoEscalacion,
    clasificar_bloqueo,
)
from kallicode_codemapping.graph_loader import GraphLoader
from kallicode_codemapping.logging_setup import get_logger
from kallicode_codemapping.metricas import (
    EVENTOS_PROCESADOS_TOTAL,
    actualizar_estado_colas,
    iniciar_servidor_metrics,
    medir_fase,
    refrescar_metricas_vectoriales,
)
from kallicode_codemapping.secretos import enmascarar_url

logger = get_logger("codemapping.worker")

STREAM = "kc:codemapping:ingesta"
GRUPO = "kc-codemapping"
STREAM_REINTENTOS = "kc:codemapping:ingesta:retry"
DLQ = "kc:dlq"
DLQ_MAXLEN = 5_000
ENDPOINT_IDEMPOTENCIA = "codemapping.ingesta"
MENSAJE_MAX_CHARS = 2_000
FRAMES_MAX = 20
_SHA = re.compile(r"^[0-9a-fA-F]{7,64}$")


class ConfigWorker(BaseSettings):
    """Parámetros del worker (variables CM_WORKER_*)."""

    model_config = SettingsConfigDict(env_prefix="CM_WORKER_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    redis_password: str | None = None
    store_database_url: str = Field(
        default="postgresql+psycopg://postgres:dev@localhost:5432/kallicode",
        description="PostgreSQL de Kallicode (cm.* y core.idempotency_keys)",
    )
    max_intentos: int = Field(default=5, ge=1, le=20, description="Intentos antes de la DLQ")
    backoff_base_segundos: float = Field(default=2.0, gt=0)
    backoff_max_segundos: float = Field(default=300.0, gt=0)
    lease_segundos: int = Field(
        default=3600, ge=30, description="Una reserva en proceso sin latido vence a los N s"
    )
    reclamar_inactivos_ms: int = Field(
        default=60_000, ge=1_000, description="Un mensaje pendiente se recupera tras N ms sin ack"
    )
    bloqueo_ms: int = Field(default=2_000, ge=10, description="Espera máxima de XREADGROUP")
    lote: int = Field(default=1, ge=1, le=50, description="Mensajes por lectura")
    escalacion_habilitada: bool = Field(
        default=True, description="Pausar y escalar a Backend los bloqueos irrecuperables"
    )
    escalacion_base_url: str | None = Field(
        default=None, description="API interna; por defecto KC_INTERNAL_API_BASE_URL"
    )
    metrics_habilitadas: bool = Field(default=True, description="Exponer /metrics para Prometheus")
    metrics_host: str = Field(default="0.0.0.0")
    metrics_port: int = Field(default=9108, ge=1, le=65535)
    metricas_vectoriales_cada_s: float = Field(
        default=300.0,
        ge=0,
        description="Cada cuánto se leen vec.estado_hnsw() y vec.vigencia_global() (0 = nunca)",
    )


# ---------------------------------------------------------------------------
# Evento
# ---------------------------------------------------------------------------
class EventoInvalido(ValueError):
    """El mensaje no cumple el contrato: reintentar no lo arregla, va directo a la DLQ."""


class EventoIngesta(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=200)
    repo_id: str = Field(min_length=1, max_length=200)
    repo_url: str = Field(min_length=1, max_length=2_000)
    commit_sha: str
    modo: Literal["full", "refresh"] = "refresh"
    trace_id: str | None = Field(default=None, max_length=200)
    intentos: int = Field(default=0, ge=0, le=1_000)

    @field_validator("commit_sha")
    @classmethod
    def _sha(cls, valor: str) -> str:
        if not _SHA.match(valor):
            raise ValueError("commit_sha debe ser un SHA hexadecimal de 7 a 64 caracteres")
        return valor.lower()

    @property
    def clave(self) -> str:
        """Clave de idempotencia dentro del tenant: (repo_id, commit_sha)."""
        return f"{self.repo_id}:{self.commit_sha}"

    @classmethod
    def desde_campos(cls, campos: dict[str, str]) -> EventoIngesta:
        try:
            return cls.model_validate(campos)
        except ValidationError as exc:
            detalle = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_url=False)
            )
            raise EventoInvalido(f"evento de ingesta inválido: {detalle}") from exc


# ---------------------------------------------------------------------------
# Idempotencia en core.idempotency_keys
# ---------------------------------------------------------------------------
Reserva = Literal["reservada", "completada", "en_curso"]


class AlmacenIdempotencia:
    """Reserva atómica por (tenant_id, repo_id, commit_sha). Cada llamada es una transacción."""

    def __init__(self, fabrica: FabricaLoader, lease_segundos: int = 3600) -> None:
        self.fabrica = fabrica
        self.lease_segundos = lease_segundos

    def reservar(self, evento: EventoIngesta, trace_id: str, worker: str) -> Reserva:
        marca = json.dumps({"estado": "en_proceso", "trace_id": trace_id, "worker": worker})
        with self.fabrica(evento.tenant_id) as loader, loader.fase("idempotencia") as cur:
            cur.execute(
                """INSERT INTO core.idempotency_keys
                       (tenant_id, endpoint, clave, respuesta, status_code)
                   VALUES (%s, %s, %s, %s::jsonb, 0)
                   ON CONFLICT (tenant_id, endpoint, clave) DO NOTHING
                   RETURNING 1""",
                (evento.tenant_id, ENDPOINT_IDEMPOTENCIA, evento.clave, marca),
            )
            if cur.fetchone():
                return "reservada"
            cur.execute(
                """SELECT status_code,
                          creado_en < now() - make_interval(secs => %s) AS vencida
                     FROM core.idempotency_keys
                    WHERE tenant_id = %s AND endpoint = %s AND clave = %s""",
                (self.lease_segundos, evento.tenant_id, ENDPOINT_IDEMPOTENCIA, evento.clave),
            )
            fila = cur.fetchone()
            if fila is None:  # la liberaron entre el INSERT y el SELECT: se reintenta luego
                return "en_curso"
            status, vencida = int(fila[0]), bool(fila[1])
            if status != 0:
                return "completada"
            if not vencida:
                return "en_curso"
            # Reserva sin latido: el UPDATE condicionado deja ganar a un solo worker.
            cur.execute(
                """UPDATE core.idempotency_keys
                      SET creado_en = now(), respuesta = %s::jsonb
                    WHERE tenant_id = %s AND endpoint = %s AND clave = %s
                      AND status_code = 0
                      AND creado_en < now() - make_interval(secs => %s)
                RETURNING 1""",
                (
                    marca,
                    evento.tenant_id,
                    ENDPOINT_IDEMPOTENCIA,
                    evento.clave,
                    self.lease_segundos,
                ),
            )
            return "reservada" if cur.fetchone() else "en_curso"

    def renovar(self, evento: EventoIngesta) -> None:
        """Latido: mantiene viva la reserva mientras el pipeline corre."""
        with self.fabrica(evento.tenant_id) as loader, loader.fase("idempotencia") as cur:
            cur.execute(
                """UPDATE core.idempotency_keys SET creado_en = now()
                    WHERE tenant_id = %s AND endpoint = %s AND clave = %s AND status_code = 0""",
                (evento.tenant_id, ENDPOINT_IDEMPOTENCIA, evento.clave),
            )

    def completar(self, evento: EventoIngesta, resumen: dict[str, Any]) -> None:
        with self.fabrica(evento.tenant_id) as loader, loader.fase("idempotencia") as cur:
            cur.execute(
                """UPDATE core.idempotency_keys SET status_code = 200, respuesta = %s::jsonb
                    WHERE tenant_id = %s AND endpoint = %s AND clave = %s""",
                (
                    json.dumps({"estado": "completado", **resumen}),
                    evento.tenant_id,
                    ENDPOINT_IDEMPOTENCIA,
                    evento.clave,
                ),
            )

    def liberar(self, evento: EventoIngesta) -> None:
        """Tras un fallo: borra la reserva en proceso para que el reintento pueda tomarla."""
        with self.fabrica(evento.tenant_id) as loader, loader.fase("idempotencia") as cur:
            cur.execute(
                """DELETE FROM core.idempotency_keys
                    WHERE tenant_id = %s AND endpoint = %s AND clave = %s AND status_code = 0""",
                (evento.tenant_id, ENDPOINT_IDEMPOTENCIA, evento.clave),
            )

    def snapshot_completo(self, evento: EventoIngesta) -> str | None:
        """Id del snapshot completo de ese commit, si ya existe (segunda barrera de idempotencia).

        Cubre el caso de una clave ya purgada por TTL (KC-DB-10-3) o de un `completar` que no
        llegó a escribirse: reprocesar el commit crearía otro snapshot.
        """
        with self.fabrica(evento.tenant_id) as loader, loader.fase("lectura") as cur:
            cur.execute(
                """SELECT id FROM cm.repo_snapshots
                    WHERE tenant_id = %s AND commit_sha = %s AND estado = 'completo'
                    ORDER BY finalizado_en DESC NULLS LAST LIMIT 1""",
                (evento.tenant_id, evento.commit_sha),
            )
            fila = cur.fetchone()
        return str(fila[0]) if fila else None


class Latido:
    """Hilo que renueva la reserva cada lease/3 mientras corre el pipeline."""

    def __init__(self, almacen: AlmacenIdempotencia, evento: EventoIngesta) -> None:
        self._almacen = almacen
        self._evento = evento
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, name="latido-ingesta", daemon=True)

    def _correr(self) -> None:
        while not self._parar.wait(self._almacen.lease_segundos / 3):
            try:
                self._almacen.renovar(self._evento)
            except Exception:
                logger.warning("no se pudo renovar la reserva de idempotencia", exc_info=True)

    def __enter__(self) -> Latido:
        self._hilo.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._parar.set()
        self._hilo.join(timeout=5)


# ---------------------------------------------------------------------------
# Errores estructurados y DLQ
# ---------------------------------------------------------------------------
def error_estructurado(exc: BaseException) -> dict[str, Any]:
    """Tipo, mensaje y frames del error, acotados y sin credenciales en las URLs."""
    frames = [
        {"archivo": os.path.basename(f.filename), "linea": f.lineno, "funcion": f.name}
        for f in traceback.extract_tb(exc.__traceback__)[-FRAMES_MAX:]
    ]
    mensaje = re.sub(r"(\w+://)[^/\s@]+@", r"\1***@", str(exc))
    return {
        "tipo": type(exc).__name__,
        "mensaje": mensaje[:MENSAJE_MAX_CHARS],
        "frames": frames,
    }


def _traza_texto(est: dict[str, Any]) -> str:
    """Traza legible (campo `traceback` de la DLQ) construida desde el error ya enmascarado."""
    lineas = [f"  {f['archivo']}:{f['linea']} en {f['funcion']}" for f in est["frames"]]
    return "\n".join([*lineas, f"{est['tipo']}: {est['mensaje']}"])


def backoff_segundos(intentos: int, base: float, maximo: float) -> float:
    """Espera tras el intento N (1, 2, 3...): base * 2^(N-1), acotada a `maximo`."""
    return float(min(maximo, base * (2 ** max(intentos - 1, 0))))


# Devuelve al stream los reintentos vencidos de forma atómica: sin script, dos workers podrían
# leer el mismo miembro (duplicado) o uno perderlo si cae entre ZREM y XADD.
LUA_PROMOVER = """
local vencidos = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
for _, miembro in ipairs(vencidos) do
  redis.call('ZREM', KEYS[1], miembro)
  local campos = cjson.decode(miembro)
  local args = {}
  for clave, valor in pairs(campos) do
    table.insert(args, clave)
    table.insert(args, valor)
  end
  redis.call('XADD', KEYS[2], '*', unpack(args))
end
return #vencidos
"""

Ejecutor = Callable[[EventoIngesta], dict[str, Any]]


def ejecutar_pipeline(evento: EventoIngesta) -> dict[str, Any]:
    """Ejecutor real: corre el pipeline de CodeMapping del tenant del evento."""
    from kallicode_codemapping.build_graph import full_build
    from kallicode_codemapping.config import build_settings
    from kallicode_codemapping.refresh import run_refresh

    cfg = build_settings(tenant_id=evento.tenant_id, repo_url=evento.repo_url)
    if evento.modo == "full":
        full_build(cfg)
    else:
        run_refresh(cfg)
    return {"modo": evento.modo, "origen": "pipeline"}


# ---------------------------------------------------------------------------
# Procesador
# ---------------------------------------------------------------------------
class Procesador:
    """Resuelve un mensaje: idempotencia, ejecución, reintento o DLQ, y ack."""

    def __init__(
        self,
        redis: Any,
        almacen: AlmacenIdempotencia,
        ejecutor: Ejecutor = ejecutar_pipeline,
        config: ConfigWorker | None = None,
        consumidor: str | None = None,
        reloj: Callable[[], float] = time.time,
        escalador: Escalador | None = None,
    ) -> None:
        self.redis = redis
        self.almacen = almacen
        self.ejecutor = ejecutor
        self.config = config or ConfigWorker()
        self.consumidor = consumidor or f"{socket.gethostname()}-{os.getpid()}"
        self.reloj = reloj
        self.escalador = escalador

    # -- mensaje ----------------------------------------------------------
    def procesar(self, mid: str, campos: dict[str, str]) -> str:
        """Desenlace: omitido | omitido_snapshot | en_curso | en_pausa | ok | reintento | dlq."""
        trace_id = campos.get("trace_id") or f"cm_{uuid.uuid4().hex[:16]}"
        extra = {"trace_id": trace_id, "tenant_id": campos.get("tenant_id")}
        try:
            evento = EventoIngesta.desde_campos({**campos, "trace_id": trace_id})
        except EventoInvalido as exc:
            self._a_dlq(mid, campos, trace_id, exc, intentos=0)
            EVENTOS_PROCESADOS_TOTAL.labels(resultado="dlq").inc()
            return "dlq"

        reserva = self.almacen.reservar(evento, trace_id, self.consumidor)
        if reserva == "completada":
            logger.info("ingesta duplicada omitida (ya procesada)", extra=extra)
            self._ack(mid)
            EVENTOS_PROCESADOS_TOTAL.labels(resultado="omitido").inc()
            return "omitido"
        if reserva == "en_curso":
            # Otro worker la está procesando. Sin ack: queda pendiente y XAUTOCLAIM la
            # revisará después (si el otro terminó, saldrá como duplicada; si murió, la toma).
            logger.info("ingesta en curso en otro worker: se revisa más tarde", extra=extra)
            EVENTOS_PROCESADOS_TOTAL.labels(resultado="en_curso").inc()
            return "en_curso"

        try:
            pausado = self._en_pausa(evento)
            if pausado:
                # Pendiente de intervención humana: no se ejecuta ni se reintenta. La reanudación
                # reencola el evento cuando se resuelva la escalación.
                self.almacen.liberar(evento)
                logger.info(
                    f"commit en pausa por escalación (snapshot {pausado}): no se ejecuta",
                    extra=extra,
                )
                self._ack(mid)
                EVENTOS_PROCESADOS_TOTAL.labels(resultado="en_pausa").inc()
                return "en_pausa"
            existente = self.almacen.snapshot_completo(evento)
            if existente:
                self.almacen.completar(
                    evento, {"origen": "snapshot_existente", "snapshot_id": existente}
                )
                logger.info("commit ya tiene snapshot completo: no se reprocesa", extra=extra)
                self._ack(mid)
                EVENTOS_PROCESADOS_TOTAL.labels(resultado="omitido_snapshot").inc()
                return "omitido_snapshot"
            with Latido(self.almacen, evento), medir_fase(evento.modo):
                resumen = self.ejecutor(evento)
            self.almacen.completar(evento, {**resumen, "trace_id": trace_id})
        except Exception as exc:
            desenlace = self._fallo(mid, evento, trace_id, exc)
            EVENTOS_PROCESADOS_TOTAL.labels(resultado=desenlace).inc()
            return desenlace
        self._ack(mid)
        logger.info("ingesta procesada", extra=extra)
        EVENTOS_PROCESADOS_TOTAL.labels(resultado="ok").inc()
        return "ok"

    # -- fallos -----------------------------------------------------------
    def _fallo(self, mid: str, evento: EventoIngesta, trace_id: str, exc: Exception) -> str:
        extra = {"trace_id": trace_id, "tenant_id": evento.tenant_id}
        # Ni el log ni la DLQ llevan el mensaje crudo: puede traer la URL del repo con su token.
        est = error_estructurado(exc)
        logger.error(f"ingesta fallida: {est['tipo']}: {est['mensaje']}", extra=extra)
        try:
            self.almacen.liberar(evento)
        except Exception:
            logger.exception("no se pudo liberar la reserva", extra=extra)
        intentos = evento.intentos + 1
        motivo = clasificar_bloqueo(exc)
        if motivo is not None or intentos >= self.config.max_intentos:
            escalacion = self._escalar(
                evento, trace_id, motivo or "reintentos_agotados", est, exc, intentos
            )
            self._a_dlq(mid, evento.model_dump(mode="json"), trace_id, exc, intentos, escalacion)
            return "dlq"
        espera = backoff_segundos(
            intentos, self.config.backoff_base_segundos, self.config.backoff_max_segundos
        )
        self._reprogramar(evento, intentos, trace_id, espera)
        self._ack(mid)
        return "reintento"

    def _reprogramar(
        self, evento: EventoIngesta, intentos: int, trace_id: str, espera: float
    ) -> None:
        miembro = {
            **{k: str(v) for k, v in evento.model_dump(mode="json").items() if v is not None},
            "trace_id": trace_id,
            "intentos": str(intentos),
            "retry_id": uuid.uuid4().hex,  # dos reintentos idénticos no se funden en un miembro
        }
        # Primero se agenda y después se confirma el original: si el proceso cae en medio queda
        # un duplicado (lo absorbe la idempotencia), nunca un evento perdido.
        self.redis.zadd(
            STREAM_REINTENTOS, {json.dumps(miembro, sort_keys=True): self.reloj() + espera}
        )

    def _escalar(
        self,
        evento: EventoIngesta,
        trace_id: str,
        motivo: MotivoEscalacion,
        est: dict[str, Any],
        exc: Exception,
        intentos: int,
    ) -> ResultadoEscalacion | None:
        if self.escalador is None:
            return None
        return self.escalador.escalar(
            tenant_id=evento.tenant_id,
            repo_id=evento.repo_id,
            commit_sha=evento.commit_sha,
            trace_id=trace_id,
            motivo=motivo,
            error=est,
            exc=exc,
            intentos=intentos,
        )

    def _en_pausa(self, evento: EventoIngesta) -> str | None:
        if self.escalador is None:
            return None
        return self.escalador.pausa.en_pausa(evento.tenant_id, evento.commit_sha)

    # -- reanudación ------------------------------------------------------
    def reanudar(self, evento: EventoIngesta, resuelto_por: str) -> str | None:
        """Tras resolver la escalación: levanta la pausa y reencola el evento.

        El snapshot vuelve a 'fallido' (reanudable): la ingesta sigue desde su último checkpoint.
        Devuelve el id del mensaje reencolado, o None si el commit no estaba en pausa.
        """
        if self.escalador is None:
            raise RuntimeError("el procesador no tiene escalador configurado")
        snapshot_id = self.escalador.pausa.reanudar(
            evento.tenant_id, evento.commit_sha, resuelto_por
        )
        if snapshot_id is None:
            return None
        campos = {
            k: str(v)
            for k, v in evento.model_dump(mode="json").items()
            if v is not None and k != "intentos"
        }
        campos["reanudado_de"] = snapshot_id
        mid = str(self.redis.xadd(STREAM, campos))
        logger.info(
            f"escalación resuelta por {resuelto_por}: snapshot {snapshot_id} reanudado",
            extra={"trace_id": evento.trace_id, "tenant_id": evento.tenant_id},
        )
        return mid

    def _a_dlq(
        self,
        mid: str,
        campos: dict[str, Any],
        trace_id: str,
        exc: BaseException,
        intentos: int,
        escalacion: ResultadoEscalacion | None = None,
    ) -> None:
        estructurado = error_estructurado(exc)
        entrada = {
            "original_stream": STREAM,
            "original_mid": str(mid),
            "payload": json.dumps(_sin_credenciales(campos), sort_keys=True),
            "attempts": str(intentos),
            "error": f"{estructurado['tipo']}: {estructurado['mensaje']}",
            "traceback": _traza_texto(estructurado),
            "error_estructurado": json.dumps(estructurado, ensure_ascii=False),
            "trace_id": trace_id,
            "tenant_id": str(campos.get("tenant_id", "")),
            "repo_id": str(campos.get("repo_id", "")),
            "commit_sha": str(campos.get("commit_sha", "")),
        }
        if escalacion is not None:
            entrada["motivo_escalacion"] = escalacion.motivo
            entrada["escalation_id"] = escalacion.escalation_id or ""
            entrada["escalacion_notificada"] = "1" if escalacion.notificada else "0"
            entrada["snapshot_id"] = escalacion.snapshot_id or ""
        self.redis.xadd(DLQ, entrada, maxlen=DLQ_MAXLEN, approximate=True)
        self.redis.incr(f"kc:metrics:dlq:{STREAM}")
        self._ack(mid)
        logger.error(
            "ingesta enviada a la DLQ",
            extra={"trace_id": trace_id, "tenant_id": campos.get("tenant_id")},
        )

    def _ack(self, mid: str) -> None:
        self.redis.xack(STREAM, GRUPO, mid)

    # -- mantenimiento del stream ----------------------------------------
    def promover_reintentos(self, limite: int = 100) -> int:
        """Devuelve al stream los reintentos cuyo backoff ya venció."""
        return int(
            self.redis.eval(
                LUA_PROMOVER, 2, STREAM_REINTENTOS, STREAM, str(self.reloj()), str(limite)
            )
        )

    def reclamar_pendientes(self) -> list[tuple[str, dict[str, str]]]:
        """Mensajes que otro consumidor dejó sin confirmar hace más de `reclamar_inactivos_ms`."""
        resultado = self.redis.xautoclaim(
            STREAM,
            GRUPO,
            self.consumidor,
            min_idle_time=self.config.reclamar_inactivos_ms,
            start_id="0-0",
            count=self.config.lote,
        )
        mensajes = (
            resultado[1] if isinstance(resultado, (tuple, list)) and len(resultado) > 1 else []
        )
        return [(str(mid), dict(campos or {})) for mid, campos in mensajes]


def _sin_credenciales(campos: dict[str, Any]) -> dict[str, Any]:
    limpio = dict(campos)
    if isinstance(limpio.get("repo_url"), str):
        limpio["repo_url"] = enmascarar_url(limpio["repo_url"])
    return limpio


# ---------------------------------------------------------------------------
# Bucle
# ---------------------------------------------------------------------------
def crear_grupo(redis: Any) -> None:
    try:
        redis.xgroup_create(STREAM, GRUPO, id="0", mkstream=True)
    except Exception as exc:  # BUSYGROUP: el grupo ya existe
        if "BUSYGROUP" not in str(exc):
            raise


def ciclo(procesador: Procesador, parar: threading.Event, pendientes_cada: float = 15.0) -> None:
    """Bucle del worker: reintentos vencidos, pendientes huérfanos y mensajes nuevos."""
    redis = procesador.redis
    conf = procesador.config
    crear_grupo(redis)
    ultimo_barrido = 0.0
    ultima_lectura_vectorial: float | None = None
    while not parar.is_set():
        try:
            procesador.promover_reintentos()
            actualizar_estado_colas(redis, STREAM, STREAM_REINTENTOS, DLQ)
            cada = conf.metricas_vectoriales_cada_s
            if cada > 0 and (
                ultima_lectura_vectorial is None
                or procesador.reloj() - ultima_lectura_vectorial >= cada
            ):
                ultima_lectura_vectorial = procesador.reloj()
                refrescar_metricas_vectoriales(conf.store_database_url)
            if procesador.reloj() - ultimo_barrido >= pendientes_cada:
                ultimo_barrido = procesador.reloj()
                for mid, campos in procesador.reclamar_pendientes():
                    procesador.procesar(mid, campos)
            lotes = redis.xreadgroup(
                GRUPO,
                procesador.consumidor,
                {STREAM: ">"},
                count=conf.lote,
                block=conf.bloqueo_ms,
            )
            for _stream, mensajes in lotes or []:
                for mid, campos in mensajes:
                    procesador.procesar(str(mid), dict(campos or {}))
        except Exception:
            logger.exception("error en el bucle del worker; se reintenta en 5 s")
            parar.wait(5)


@contextmanager
def _loader_por_tenant(dsn: str) -> Iterator[FabricaLoader]:
    def fabrica(tenant_id: str) -> AbstractContextManager[GraphLoader]:
        return GraphLoader(dsn=dsn, tenant_id=tenant_id)

    yield fabrica


def main() -> None:  # pragma: no cover - arranque del proceso
    import redis as redis_lib

    logging.getLogger("codemapping").setLevel(logging.INFO)
    conf = ConfigWorker()
    if conf.metrics_habilitadas:
        iniciar_servidor_metrics(conf.metrics_host, conf.metrics_port)
    cliente = redis_lib.Redis.from_url(
        conf.redis_url, password=conf.redis_password, decode_responses=True
    )
    parar = threading.Event()
    for senal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(senal, lambda *_: parar.set())
    with _loader_por_tenant(conf.store_database_url) as fabrica:
        almacen = AlmacenIdempotencia(fabrica, conf.lease_segundos)
        escalador = (
            Escalador(ClienteEscalacion(conf.escalacion_base_url), PausaSnapshots(fabrica))
            if conf.escalacion_habilitada
            else None
        )
        ciclo(Procesador(cliente, almacen, config=conf, escalador=escalador), parar)


if __name__ == "__main__":  # pragma: no cover
    main()
