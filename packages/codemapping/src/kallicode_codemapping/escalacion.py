"""Escalación humana de una ingesta bloqueada (KC-CM-10-6).

Cuando el worker de ingesta (KC-CM-10-1) llega a un bloqueo que reintentar no arregla, en vez de
dejar solo una línea en el log y un mensaje en la DLQ:

1. Clasifica el bloqueo (`clasificar_bloqueo`): credenciales del repositorio vencidas, repositorio
   corrupto, cadena semántica sin tiers (`LLMEscaladoHumano`) o reintentos agotados.
2. Pausa el snapshot del commit en `pendiente_intervencion` (`PausaSnapshots.pausar`): mientras
   siga así el worker no vuelve a ejecutar ese commit, aunque le lleguen eventos nuevos.
3. Pide a Backend que abra la escalación y notifique al responsable (`ClienteEscalacion`, contrato
   `kallicode_core.cm_escalacion`), con el paso, el tier, el lote, la referencia de entrada, el
   `trace_id` y un diagnóstico acotado. Nunca con código del cliente ni credenciales.

Resuelta la escalación, `Procesador.reanudar` (worker.py) libera la pausa —el snapshot vuelve a
`fallido`, el estado reanudable que leen `GraphLoader.find_resumable_snapshot` y
`SnapshotManager`— y reencola el evento: la ingesta sigue desde su último checkpoint.

El orden importa: primero se pausa (durable, en PostgreSQL) y después se notifica. Si la
notificación falla, el snapshot queda pausado igual y el fallo queda registrado en su `stats` y
en la DLQ para reenviarla; nunca se pierde el estado de pausa.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from kallicode_core.cm_escalacion import (
    MAX_FRAMES,
    MAX_TIERS,
    MENSAJE_MAX_CHARS,
    RUTA_ENDPOINT,
    SERVICIO,
    SEVERIDAD_POR_MOTIVO,
    Diagnostico,
    EscalacionIngestaIn,
    EscalacionIngestaOut,
    FrameDiagnostico,
    MotivoEscalacion,
    clave_idempotencia,
)
from kallicode_core.llm.cliente import LLMEscaladoHumano

from kallicode_codemapping.logging_setup import get_logger

if TYPE_CHECKING:  # graph_loader -> nuggets -> llm_enrich -> escalacion: solo para tipos
    from kallicode_codemapping.graph_loader import GraphLoader

logger = get_logger("codemapping.escalacion")

ESTADO_PAUSA = "pendiente_intervencion"
# Al resolver, el snapshot vuelve a 'fallido': es el estado reanudable que comparten
# GraphLoader.find_resumable_snapshot (ESTADOS_REANUDABLES) y SnapshotManager.
ESTADO_TRAS_RESOLVER = "fallido"
PASO_INGESTA = "ingesta"
RAMA_DESCONOCIDA = "desconocida"


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
class ErrorNoRecuperable(RuntimeError):
    """El ejecutor declara que reintentar no sirve (credenciales vencidas, repo corrupto...)."""

    def __init__(self, mensaje: str, motivo: MotivoEscalacion = "otro") -> None:
        super().__init__(mensaje)
        self.motivo: MotivoEscalacion = motivo


class EscaladoSemantico(LLMEscaladoHumano):
    """La cadena semántica agotó los tiers en un lote concreto de un paso.

    Subclase de LLMEscaladoHumano: quien ya lo captura (nuggets, realimentación) no cambia.
    Añade el tier efectivo y el índice del lote para la escalación.
    """

    def __init__(
        self, paso_id: str, motivo: str, intentos: list[dict[str, Any]], lote: int | None
    ) -> None:
        super().__init__(paso_id, motivo, intentos)
        self.lote = lote

    @property
    def tier(self) -> str | None:
        return tiers_de(self.intentos)[-1] if self.intentos else None


class ErrorNotificacionEscalacion(RuntimeError):
    """Backend no aceptó la escalación (4xx) o no respondió tras los reintentos."""


# Patrones de la salida de git (GitSyncError / CalledProcessError). En minúsculas.
_CREDENCIALES = re.compile(
    r"authentication failed|could not read (username|password)|invalid username or password|"
    r"permission denied \(publickey\)|access denied|bad credentials|token (has )?expired|"
    r"requested url returned error: 40[13]|http basic: access denied"
)
_CORRUPCION = re.compile(
    r"corrupt|bad object|loose object .* is corrupt|did not send all necessary objects|"
    r"index file (smaller than expected|corrupt)|unable to read tree|missing blob|"
    r"fatal: packed object|object file .* is empty|broken link from"
)


def clasificar_bloqueo(exc: BaseException) -> MotivoEscalacion | None:
    """Motivo de escalación si el fallo es irrecuperable; None si merece un reintento."""
    if isinstance(exc, ErrorNoRecuperable):
        return exc.motivo
    if isinstance(exc, LLMEscaladoHumano):
        return "tiers_agotados"
    texto = str(exc)
    if isinstance(exc, subprocess.CalledProcessError):
        texto = f"{exc.stderr or ''} {exc.stdout or ''} {texto}"
    causa = exc.__cause__
    if isinstance(causa, subprocess.CalledProcessError):
        texto = f"{texto} {causa.stderr or ''}"
    texto = texto.lower()
    if _CREDENCIALES.search(texto):
        return "credenciales_expiradas"
    if _CORRUPCION.search(texto):
        return "repositorio_corrupto"
    return None


def tiers_de(intentos: list[dict[str, Any]]) -> list[str]:
    """Tiers probados, en orden y sin repetir. Solo el nombre: nada de la salida del modelo."""
    vistos: list[str] = []
    for intento in intentos:
        tier = intento.get("tier")
        if isinstance(tier, str) and tier not in vistos:
            vistos.append(tier)
    return vistos[:MAX_TIERS]


# ---------------------------------------------------------------------------
# Solicitud
# ---------------------------------------------------------------------------
def construir_solicitud(
    *,
    tenant_id: str,
    repo_id: str,
    commit_sha: str,
    trace_id: str,
    motivo: MotivoEscalacion,
    error: dict[str, Any],
    exc: BaseException,
    intentos: int,
    snapshot_id: str | None,
) -> EscalacionIngestaIn:
    """Arma la solicitud del contrato desde el error estructurado del worker.

    `error` es `worker.error_estructurado(exc)`: ya trae el mensaje con las credenciales de las
    URLs enmascaradas. Aquí se queda solo su primera línea (la salida de git o de un parser puede
    seguir con contenido del repositorio) acotada a MENSAJE_MAX_CHARS.
    """
    paso, tier, lote, tiers = PASO_INGESTA, None, None, []
    if isinstance(exc, LLMEscaladoHumano):
        paso = exc.paso_id
        tiers = tiers_de(exc.intentos)
        tier = tiers[-1] if tiers else None
        lote = exc.lote if isinstance(exc, EscaladoSemantico) else None
    mensaje = str(error.get("mensaje", "")).strip().splitlines()
    diagnostico = Diagnostico(
        tipo_error=str(error.get("tipo") or type(exc).__name__),
        mensaje=(mensaje[0] if mensaje else "")[:MENSAJE_MAX_CHARS],
        intentos=intentos,
        frames=[
            FrameDiagnostico(
                archivo=str(f["archivo"])[:200],
                linea=int(f["linea"] or 0),
                funcion=str(f["funcion"])[:200],
            )
            for f in list(error.get("frames") or [])[-MAX_FRAMES:]
        ],
        tiers_probados=tiers,
    )
    return EscalacionIngestaIn(
        tenant_id=tenant_id,
        repo_id=repo_id,
        commit_sha=commit_sha,
        snapshot_id=snapshot_id,
        trace_id=trace_id,
        motivo=motivo,
        severidad=SEVERIDAD_POR_MOTIVO[motivo],
        paso=paso,
        tier=tier,
        lote=lote,
        entrada_ref=f"{repo_id}@{commit_sha}",
        diagnostico=diagnostico,
    )


# ---------------------------------------------------------------------------
# Cliente HTTP (contrato cm-escalacion-v1)
# ---------------------------------------------------------------------------
def _token_servicio(tenant_id: str) -> str:
    from kallicode_core.seguridad import emitir_svc_token

    return emitir_svc_token(SERVICIO, tenant_id)


def _url_base() -> str:
    from kallicode_core.config import get_settings

    return str(get_settings().internal_api_base_url)


class ClienteEscalacion:
    """POST /internal/v1/codemapping/escalations con JWT de servicio e Idempotency-Key.

    Reintenta ante error de red, 429 y 5xx con espera exponencial; un 4xx es definitivo (el
    contrato no se arregla reintentando). La misma clave de idempotencia en cada intento hace que
    un reintento tras un timeout no abra dos escalaciones.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        emitir_token: Callable[[str], str] = _token_servicio,
        http: httpx.Client | None = None,
        reintentos: int = 3,
        espera_base_s: float = 0.5,
        timeout_s: float = 10.0,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = (base_url or _url_base()).rstrip("/") + RUTA_ENDPOINT
        self._emitir_token = emitir_token
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout_s, connect=5.0))
        self._reintentos = max(reintentos, 1)
        self._espera = espera_base_s
        self._dormir = dormir

    def notificar(self, solicitud: EscalacionIngestaIn) -> EscalacionIngestaOut:
        cabeceras = {
            "Authorization": f"Bearer {self._emitir_token(solicitud.tenant_id)}",
            "Idempotency-Key": clave_idempotencia(solicitud),
            "X-Trace-Id": solicitud.trace_id,
        }
        cuerpo = solicitud.model_dump(mode="json")
        ultimo = "sin respuesta"
        for intento in range(1, self._reintentos + 1):
            try:
                r = self._http.post(self._url, json=cuerpo, headers=cabeceras)
            except httpx.HTTPError as exc:
                ultimo = f"{type(exc).__name__}"
            else:
                if r.status_code in (200, 201):
                    return EscalacionIngestaOut.model_validate(r.json())
                ultimo = f"HTTP {r.status_code}"
                if r.status_code < 500 and r.status_code != 429:
                    raise ErrorNotificacionEscalacion(
                        f"Backend rechazó la escalación: {ultimo}: {r.text[:300]}"
                    )
            if intento < self._reintentos:
                self._dormir(self._espera * (2 ** (intento - 1)))
        raise ErrorNotificacionEscalacion(
            f"Backend no respondió a la escalación tras {self._reintentos} intentos: {ultimo}"
        )

    def close(self) -> None:
        self._http.close()


# ---------------------------------------------------------------------------
# Pausa del snapshot en PostgreSQL
# ---------------------------------------------------------------------------
class FabricaLoader(Protocol):
    def __call__(self, tenant_id: str) -> AbstractContextManager[GraphLoader]: ...


class PausaSnapshots:
    """Estado `pendiente_intervencion` del snapshot de un commit. Cada llamada es una transacción.

    El detalle de la escalación vive en `stats.escalacion` del snapshot (motivo, trace_id,
    escalation_id, si se notificó, quién la resolvió): el portal y la reanudación lo leen de ahí.
    """

    def __init__(self, fabrica: FabricaLoader) -> None:
        self.fabrica = fabrica

    def pausar(self, tenant_id: str, commit_sha: str, detalle: dict[str, Any]) -> str:
        """Pausa el último snapshot sin terminar del commit, o crea uno pausado si no había.

        Sin snapshot (p. ej. las credenciales fallaron en el clon, antes de crearlo) se registra
        uno nuevo ya pausado, para que el bloqueo sea visible y la reanudación tenga dónde leerlo.
        """
        marca = json.dumps(detalle, ensure_ascii=False)
        with self.fabrica(tenant_id) as loader, loader.fase("escalacion") as cur:
            cur.execute(
                """UPDATE cm.repo_snapshots
                      SET estado = 'pendiente_intervencion',
                          stats = stats || jsonb_build_object('escalacion', %s::jsonb)
                    WHERE tenant_id = %s
                      AND id = (SELECT id FROM cm.repo_snapshots
                                 WHERE tenant_id = %s AND commit_sha = %s
                                   AND estado IN ('en_progreso', 'fallido',
                                                  'pendiente_intervencion')
                                 ORDER BY iniciado_en DESC
                                 LIMIT 1
                                 FOR UPDATE)
                RETURNING id""",
                (marca, tenant_id, tenant_id, commit_sha),
            )
            fila = cur.fetchone()
            if fila:
                return str(fila[0])
            snapshot_id = f"snap_{uuid.uuid4().hex[:20]}"
            cur.execute(
                """INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado, stats)
                   VALUES (%s, %s, %s, %s, 'pendiente_intervencion',
                           jsonb_build_object('escalacion', %s::jsonb))""",
                (snapshot_id, tenant_id, commit_sha, RAMA_DESCONOCIDA, marca),
            )
            return snapshot_id

    def anotar(self, tenant_id: str, snapshot_id: str, campos: dict[str, Any]) -> None:
        """Agrega campos a `stats.escalacion` (escalation_id, resultado de la notificación)."""
        with self.fabrica(tenant_id) as loader, loader.fase("escalacion") as cur:
            cur.execute(
                """UPDATE cm.repo_snapshots
                      SET stats = jsonb_set(stats, '{escalacion}',
                                            coalesce(stats->'escalacion', '{}'::jsonb)
                                            || %s::jsonb)
                    WHERE tenant_id = %s AND id = %s""",
                (json.dumps(campos, ensure_ascii=False), tenant_id, snapshot_id),
            )

    def en_pausa(self, tenant_id: str, commit_sha: str) -> str | None:
        """Id del snapshot pausado del commit, si lo hay."""
        with self.fabrica(tenant_id) as loader, loader.fase("lectura") as cur:
            cur.execute(
                """SELECT id FROM cm.repo_snapshots
                    WHERE tenant_id = %s AND commit_sha = %s
                      AND estado = 'pendiente_intervencion'
                    ORDER BY iniciado_en DESC
                    LIMIT 1""",
                (tenant_id, commit_sha),
            )
            fila = cur.fetchone()
        return str(fila[0]) if fila else None

    def reanudar(self, tenant_id: str, commit_sha: str, resuelto_por: str) -> str | None:
        """Levanta la pausa: el snapshot vuelve a 'fallido' (reanudable) con la resolución anotada.

        Devuelve el id del snapshot, o None si el commit no estaba en pausa.
        """
        resolucion = json.dumps({"estado": "resuelta", "resuelto_por": resuelto_por})
        with self.fabrica(tenant_id) as loader, loader.fase("escalacion") as cur:
            cur.execute(
                """UPDATE cm.repo_snapshots
                      SET estado = 'fallido',
                          stats = jsonb_set(stats, '{escalacion}',
                                            coalesce(stats->'escalacion', '{}'::jsonb)
                                            || %s::jsonb
                                            || jsonb_build_object('resuelta_en', now()))
                    WHERE tenant_id = %s AND commit_sha = %s
                      AND estado = 'pendiente_intervencion'
                RETURNING id""",
                (resolucion, tenant_id, commit_sha),
            )
            filas = cur.fetchall()
        return str(filas[0][0]) if filas else None


# ---------------------------------------------------------------------------
# Orquestación: pausar y notificar
# ---------------------------------------------------------------------------
class Notificador(Protocol):
    def notificar(self, solicitud: EscalacionIngestaIn) -> EscalacionIngestaOut: ...


class Pausa(Protocol):
    def pausar(self, tenant_id: str, commit_sha: str, detalle: dict[str, Any]) -> str: ...
    def anotar(self, tenant_id: str, snapshot_id: str, campos: dict[str, Any]) -> None: ...
    def en_pausa(self, tenant_id: str, commit_sha: str) -> str | None: ...
    def reanudar(self, tenant_id: str, commit_sha: str, resuelto_por: str) -> str | None: ...


@dataclass(frozen=True)
class ResultadoEscalacion:
    motivo: MotivoEscalacion
    snapshot_id: str | None
    escalation_id: str | None
    notificada: bool


class Escalador:
    """Pausa el snapshot y abre la escalación en Backend. Nunca lanza: el worker debe poder
    mandar el mensaje a la DLQ y confirmarlo pase lo que pase con la escalación."""

    def __init__(self, notificador: Notificador, pausa: Pausa) -> None:
        self.notificador = notificador
        self.pausa = pausa

    def escalar(
        self,
        *,
        tenant_id: str,
        repo_id: str,
        commit_sha: str,
        trace_id: str,
        motivo: MotivoEscalacion,
        error: dict[str, Any],
        exc: BaseException,
        intentos: int,
    ) -> ResultadoEscalacion:
        extra = {"trace_id": trace_id, "tenant_id": tenant_id, "motivo": motivo}
        snapshot_id: str | None = None
        try:
            snapshot_id = self.pausa.pausar(
                tenant_id,
                commit_sha,
                {
                    "estado": "abierta",
                    "motivo": motivo,
                    "trace_id": trace_id,
                    "repo_id": repo_id,
                    "notificada": False,
                },
            )
        except Exception:
            logger.exception("no se pudo pausar el snapshot de la ingesta escalada", extra=extra)

        solicitud = construir_solicitud(
            tenant_id=tenant_id,
            repo_id=repo_id,
            commit_sha=commit_sha,
            trace_id=trace_id,
            motivo=motivo,
            error=error,
            exc=exc,
            intentos=intentos,
            snapshot_id=snapshot_id,
        )
        escalation_id: str | None = None
        try:
            respuesta = self.notificador.notificar(solicitud)
            escalation_id = respuesta.escalation_id
            anotacion: dict[str, Any] = {"notificada": True, "escalation_id": escalation_id}
            logger.warning("ingesta escalada a un humano", extra=extra)
        except Exception as fallo:
            anotacion = {"notificada": False, "error_notificacion": str(fallo)[:300]}
            logger.error(f"no se pudo notificar la escalación: {type(fallo).__name__}", extra=extra)
        if snapshot_id:
            try:
                self.pausa.anotar(tenant_id, snapshot_id, anotacion)
            except Exception:
                logger.exception("no se pudo anotar la escalación en el snapshot", extra=extra)
        return ResultadoEscalacion(
            motivo=motivo,
            snapshot_id=snapshot_id,
            escalation_id=escalation_id,
            notificada=escalation_id is not None,
        )
