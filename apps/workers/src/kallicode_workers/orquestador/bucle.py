"""Bucle del orquestador: reserva, ejecuta y transiciona las etapas activas."""

from __future__ import annotations

import asyncio

import httpx
from kallicode_core.config import get_settings
from kallicode_core.logging import bind_contexto, limpiar_contexto, log
from kallicode_core.pipeline import EstadoJob

from .cliente_interno import ClienteInterno
from .etapas import ETAPAS
from .politica import Decision, decidir
from .reserva import registrar_intento_fallido, reservar_siguiente


async def procesar_job(job: dict, cliente: ClienteInterno) -> None:
    """Ejecuta la etapa correspondiente al estado del job y decide qué hacer.

    Reglas:
      - La reserva ya ha transicionado el job a 'triage'. No hay que
        hacer una transición previa aquí.
      - Si la etapa no está implementada todavía, no se toca el job.
      - Si la etapa tiene éxito, se transiciona al siguiente estado.
      - Si la etapa falla y quedan intentos, se deja para el próximo ciclo.
      - Si se agotan los intentos, se escala a humano.
      - Si build pide replanificar más veces que MAX_ITERACIONES, se escala a humano.
      - Si falla el registro del resultado (transición, escalado o intento), no se
        pierde: un 409 se descarta porque el job ya cambió; otro error tras una etapa
        exitosa cuenta como intento fallido (KC-BE-06-3).
    """
    estado = EstadoJob(job["estado"])
    ejecutar = ETAPAS.get(estado)
    if ejecutar is None:
        log.debug("orquestador.etapa_sin_impl", estado=estado.value, job_id=job["id"])
        return

    iteraciones = job.get("iteraciones") or {}
    intentos_registrados = int(iteraciones.get(estado.value, 0))
    # Triage se reserva directamente sin transición; las etapas posteriores
    # ya se cuentan al entrar en ellas mediante la API de transiciones.
    intento = (
        intentos_registrados + 1 if estado is EstadoJob.TRIAGE else max(1, intentos_registrados)
    )
    error: Exception | None = None
    resultado: object = None

    try:
        resultado = await ejecutar(job, cliente, intento=intento)
    except Exception as exc:
        error = exc
        log.warning(
            "orquestador.etapa_fallo",
            job_id=job["id"],
            estado=estado.value,
            intento=intento,
            error=str(exc),
            exc_info=True,
        )

    try:
        if error is None and isinstance(resultado, dict) and resultado.get("needs_replan"):
            await _replanificar(job, cliente, intento, resultado)
            return
        await _aplicar(decidir(estado.value, intento, error), job, cliente, estado, intento, error)
    except Exception as exc:
        await _fallo_al_registrar(job, estado, intento, exc, etapa_exitosa=error is None)


def _es_conflicto(exc: BaseException) -> bool:
    """409 de la API interna: el job ya no está donde el orquestador lo dejó."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 409


async def _fallo_al_registrar(
    job: dict, estado: EstadoJob, intento: int, exc: Exception, *, etapa_exitosa: bool
) -> None:
    """Falló la transición, el escalado o el registro del intento tras ejecutar la etapa."""
    if _es_conflicto(exc):
        # Cancelado, firmado o escalado por otra vía mientras corría la etapa: el
        # resultado ya no aplica y no hay nada que reintentar.
        log.warning(
            "orquestador.resultado_descartado",
            job_id=job["id"],
            estado=estado.value,
            intento=intento,
        )
        return

    log.error(
        "orquestador.resultado_no_registrado",
        job_id=job["id"],
        estado=estado.value,
        intento=intento,
        error=str(exc),
        exc_info=True,
    )
    if not etapa_exitosa:
        # La política ya contó el fallo; el próximo ciclo repite la decisión.
        return
    # La etapa terminó pero no quedó registrada: se cuenta como intento fallido para
    # que, si se repite, la política termine escalando en vez de reintentar sin fin.
    try:
        await registrar_intento_fallido(job["id"], job["tenant_id"], estado.value)
    except Exception:
        log.error("orquestador.intento_no_registrado", job_id=job["id"], exc_info=True)


async def _replanificar(job: dict, cliente: ClienteInterno, intento: int, resultado: dict) -> None:
    """Build pide rehacer el diseño: vuelve a design, o escala si ya dio demasiadas vueltas.

    Cada entrada a design suma `iteraciones["design"]` (la primera viene de triage).
    Con MAX_ITERACIONES entradas, la siguiente replanificación ya no vuelve al agente:
    se escala con motivo max_iteraciones (antes el job podía girar sin fin).
    """
    razon = resultado.get("replan_reason_en", "")
    entradas_design = int((job.get("iteraciones") or {}).get(EstadoJob.DESIGN.value, 0))
    maximo = get_settings().max_iteraciones_gate
    if entradas_design >= maximo:
        log.warning(
            "orquestador.replan_max_iteraciones",
            job_id=job["id"],
            entradas_design=entradas_design,
            maximo=maximo,
        )
        await cliente.escalar(
            job_id=job["id"],
            tenant_id=job["tenant_id"],
            etapa=EstadoJob.BUILD.value,
            motivo="max_iteraciones",
            severidad="alta",
            detalle={"replan_reason_en": razon, "entradas_design": entradas_design},
            intento=intento,
        )
        return
    await cliente.transicionar(
        job_id=job["id"],
        tenant_id=job["tenant_id"],
        a=EstadoJob.DESIGN.value,
        motivo="build_needs_replan",
        intento=intento,
        contexto={"replan_reason_en": razon},
    )


async def _aplicar(
    decision: Decision,
    job: dict,
    cliente: ClienteInterno,
    estado: EstadoJob,
    intento: int,
    error: Exception | None,
) -> None:
    """Registra en la API interna lo que decidió la política."""
    if decision.accion == "avanzar":
        siguiente = _siguiente_estado(estado)
        if siguiente is not None:
            await cliente.transicionar(
                job_id=job["id"],
                tenant_id=job["tenant_id"],
                a=siguiente.value,
                motivo="etapa_completada",
                intento=intento,
            )
        else:
            log.info(
                "orquestador.etapa_terminal_sin_siguiente",
                job_id=job["id"],
                estado=estado.value,
            )

    elif decision.accion == "reintentar":
        await registrar_intento_fallido(job["id"], job["tenant_id"], estado.value)

    elif decision.accion == "escalar":
        await cliente.escalar(
            job_id=job["id"],
            tenant_id=job["tenant_id"],
            etapa=estado.value,
            motivo=decision.motivo or "otro",
            severidad=decision.severidad or "media",
            detalle={"error": str(error) if error else None},
            intento=intento,
        )


def _siguiente_estado(estado: EstadoJob) -> EstadoJob | None:
    """Estado siguiente tras completar con éxito la etapa actual.

    Solo cubre las transiciones que el orquestador provoca. Las salidas de
    gate y las devoluciones humanas las decide la API al recibir la firma.

    IMPORTANTE: `triage` no aparece aquí porque el endpoint
    `POST /tickets/{numero}/triage-result` YA transiciona el job a
    `design` (o a `cerrado_duplicado`) al persistir la clasificación.
    Si el orquestador volviera a transicionar `triage → design` daría 409.
    """
    mapa: dict[EstadoJob, EstadoJob] = {
        # Design publica su spec y el endpoint la avanza a gate1.
        EstadoJob.BUILD: EstadoJob.QA,
    }
    return mapa.get(estado)


async def ciclo(parar: asyncio.Event, cliente: ClienteInterno | None = None) -> None:
    """Bucle principal del orquestador.

    Reserva un job, lo procesa y vuelve a empezar. Duerme cuando no hay
    trabajo para no martillear la base de datos. Se detiene cuando el
    evento `parar` se activa (lo hace `main.py` al recibir SIGTERM/SIGINT).
    """
    cliente = cliente or ClienteInterno()
    log.info("orquestador.arrancando")

    try:
        while not parar.is_set():
            try:
                async with reservar_siguiente() as job:
                    if job is None:
                        try:
                            await asyncio.wait_for(parar.wait(), timeout=2.0)
                        except TimeoutError:
                            pass
                        continue

                    limpiar_contexto()
                    bind_contexto(
                        trace_id=f"orch_{job['id']}",
                        tenant_id=job["tenant_id"],
                        actor="svc:orquestador",
                    )
                    try:
                        await procesar_job(job, cliente)
                    except Exception:
                        log.error(
                            "orquestador.job_error",
                            job_id=job["id"],
                            tenant_id=job["tenant_id"],
                            exc_info=True,
                        )
            except Exception:
                log.error("orquestador.reserva_error", exc_info=True)
                await asyncio.sleep(5)
    finally:
        await cliente.cerrar()
        log.info("orquestador.detenido")
