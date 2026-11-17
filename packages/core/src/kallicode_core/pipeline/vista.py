"""Pipeline que ve el cliente en el detalle del ticket (KC-BE-06-5, cierra BE-F42).

Traduce la etapa visible del ticket (`core.tickets.etapa`) y su gate pendiente a los
pasos del stepper. Antes era una lista armada dentro del endpoint, que dejaba todo como
«pendiente» en los tickets cancelados, duplicados o en cola por cuota.

Fuentes que debe respetar (las comprueba tests/integracion/test_pipeline_visible.py):
  - El orden de `core.pipeline_stages.orden_flujo` (migración 049).
  - El espejo job → ticket del trigger T9 (`core.mirror_ticket_stage`): qué etapa ve el
    cliente para cada estado del job y en qué etapa espera cada gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, get_args

from .estados import GATES, EstadoJob

EtapaFlujo = Literal["triage", "design", "build", "qa", "security", "deploy", "produccion"]
"""Las etapas del stepper, en orden."""

EtapaTicket = EtapaFlujo | Literal["en_cola_por_cuota", "cancelado", "cerrado_duplicado"]
"""Los valores posibles de core.tickets.etapa (tk_etapa_chk)."""

EstadoPaso = Literal["done", "actual", "pendiente"]
"""Lo que el portal sabe pintar (TicketDetalleEsquema.pipeline[].estado)."""

ETAPAS_FLUJO: Final[tuple[EtapaFlujo, ...]] = get_args(EtapaFlujo)

ETAPA_DE_GATE: Final[dict[int, EtapaFlujo]] = {1: "design", 2: "qa", 3: "deploy"}
"""Etapa visible mientras el job espera cada gate (trigger T9)."""

# Estado del job → etapa que ve el cliente (trigger T9). Los estados de escape no
# tienen etapa propia: pausado, escalado, cancelado y duplicado conservan o reemplazan
# la etapa del ticket por otra vía.
_ETAPA_DE_ESTADO: Final[dict[EstadoJob, EtapaFlujo]] = {
    EstadoJob.PENDIENTE_ASIGNACION: "triage",
    EstadoJob.TRIAGE: "triage",
    EstadoJob.DESIGN: "design",
    EstadoJob.BUILD: "build",
    EstadoJob.QA: "qa",
    EstadoJob.SECURITY: "security",
    EstadoJob.DEPLOY_PREFLIGHT: "deploy",
    EstadoJob.DEPLOY: "deploy",
    EstadoJob.PRODUCCION: "produccion",
    **{gate: ETAPA_DE_GATE[numero] for gate, numero in GATES.items()},
}


@dataclass(frozen=True)
class PasoPipeline:
    """Un paso del stepper del detalle del ticket."""

    etapa: EtapaFlujo
    estado: EstadoPaso
    gate: int | None = None
    """Gate que espera este paso, solo si es el actual."""

    def a_dict(self) -> dict[str, object]:
        return {"etapa": self.etapa, "estado": self.estado, "gate": self.gate}


def etapa_visible(estado_job: EstadoJob | str | None) -> EtapaFlujo | None:
    """Etapa que ve el cliente para un estado del job; None para los de escape."""
    if estado_job is None:
        return None
    try:
        return _ETAPA_DE_ESTADO.get(EstadoJob(estado_job))
    except ValueError:
        return None


def _reanuda_en(estado_previo: EstadoJob | str | None) -> EtapaFlujo | None:
    """Dónde sigue un job pausado por cuota: solo puede volver a triage o a build."""
    if estado_previo is None:
        return None
    if estado_previo in (EstadoJob.PENDIENTE_ASIGNACION, EstadoJob.TRIAGE):
        return "triage"
    return "build" if etapa_visible(estado_previo) is not None else None


def mapear_pipeline(
    etapa: EtapaTicket | str,
    gate_pendiente: int | None,
    estado_previo: EstadoJob | str | None = None,
) -> list[PasoPipeline]:
    """Pasos del stepper para un ticket.

    `estado_previo` es el estado del job antes de salir del flujo (el `de_estado` de su
    última transición a cancelado, cerrado_duplicado o pausado_por_cuota). Solo se usa
    en esas etapas; si no se conoce, ningún paso queda hecho ni actual.

    - Etapa del flujo: las anteriores hechas, ella actual y las siguientes pendientes.
    - produccion: todas hechas (terminó).
    - en_cola_por_cuota: las anteriores hechas y actual la etapa donde se reanuda.
    - cerrado_duplicado: hechas hasta la etapa donde se detectó (triage), incluida.
    - cancelado: hechas las anteriores a la etapa donde se canceló; ninguna actual.

    Lanza ValueError ante una etapa que no admite tk_etapa_chk.
    """
    hechas: int
    actual: int | None
    if etapa == "produccion":
        hechas, actual = len(ETAPAS_FLUJO), None
    elif etapa in ETAPAS_FLUJO:
        actual = ETAPAS_FLUJO.index(etapa)
        hechas = actual
    elif etapa == "en_cola_por_cuota":
        reanuda = _reanuda_en(estado_previo)
        actual = ETAPAS_FLUJO.index(reanuda) if reanuda else None
        hechas = actual or 0
    elif etapa in ("cancelado", "cerrado_duplicado"):
        detenida = etapa_visible(estado_previo)
        actual = None
        if detenida is None:
            hechas = 0
        else:
            hechas = ETAPAS_FLUJO.index(detenida) + (1 if etapa == "cerrado_duplicado" else 0)
    else:
        raise ValueError(f"Etapa de ticket desconocida: {etapa!r}")

    etapa_del_gate = ETAPA_DE_GATE.get(gate_pendiente) if gate_pendiente else None
    pasos: list[PasoPipeline] = []
    for posicion, nombre in enumerate(ETAPAS_FLUJO):
        if posicion == actual:
            gate = gate_pendiente if nombre == etapa_del_gate else None
            pasos.append(PasoPipeline(nombre, "actual", gate))
        elif posicion < hechas:
            pasos.append(PasoPipeline(nombre, "done"))
        else:
            pasos.append(PasoPipeline(nombre, "pendiente"))
    return pasos
