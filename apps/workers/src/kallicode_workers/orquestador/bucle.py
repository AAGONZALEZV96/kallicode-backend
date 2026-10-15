"""Bucle del orquestador: reserva, ejecuta, transiciona.

Sprint 4 (KC-BE-04-1): núcleo del bucle + etapa de triage.
Sprint 6 (KC-BE-06-1/2): etapas de design, build, QA, seguridad y deploy.
"""

from __future__ import annotations

import asyncio

from kallicode_core.logging import bind_contexto, limpiar_contexto, log
from kallicode_core.pipeline import EstadoJob

from .cliente_interno import ClienteInterno
from .etapas import ETAPAS
from .politica import decidir
from .reserva import reservar_siguiente


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
    intento = int(iteraciones.get(estado.value, 0)) + 1
    error: Exception | None = None

    try:
        await ejecutar(job, cliente, intento=intento)
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
    """
    mapa: dict[EstadoJob, EstadoJob] = {
        EstadoJob.TRIAGE: EstadoJob.DESIGN,
        # Sprint 6 añadirá:
        #   DESIGN: GATE1 (pero lo decide la firma humana)
        #   BUILD: QA
        #   QA: GATE2 (idem)
        #   SECURITY: DEPLOY_PREFLIGHT
        #   DEPLOY: PRODUCCION
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
                job = await reservar_siguiente()
            except Exception:
                log.error("orquestador.reserva_error", exc_info=True)
                await asyncio.sleep(5)
                continue

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
    finally:
        await cliente.cerrar()
        log.info("orquestador.detenido")
