"""Bucle del orquestador: reserva, ejecuta y transiciona las etapas activas."""

from __future__ import annotations

import asyncio

from kallicode_core.logging import bind_contexto, limpiar_contexto, log
from kallicode_core.pipeline import EstadoJob

from .cliente_interno import ClienteInterno
from .etapas import ETAPAS
from .politica import decidir
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

    if error is None and isinstance(resultado, dict) and resultado.get("needs_replan"):
        await cliente.transicionar(
            job_id=job["id"],
            tenant_id=job["tenant_id"],
            a=EstadoJob.DESIGN.value,
            motivo="build_needs_replan",
            intento=intento,
            contexto={"replan_reason_en": resultado.get("replan_reason_en", "")},
        )
        return

    decision = decidir(estado.value, intento, error)

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
