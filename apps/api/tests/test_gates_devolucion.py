"""Pedir cambios en un gate (KC-BE-06-3, cierra BE-F39).

Funciones puras de routers/gates.py: destino de la devolución (y el devolver_a que exige
gs_accion_coherencia_chk), corte por MAX_ITERACIONES y precondición del gate activo.
No requieren base de datos.
"""

from __future__ import annotations

import pytest
from kallicode_api.routers.gates import decidir_devolucion, exigir_gate_activo
from kallicode_core.errors import AppError
from kallicode_core.pipeline import EstadoJob, es_transicion_valida

MAXIMO = 5


@pytest.mark.parametrize(("gate", "destino"), [(1, "design"), (2, "build")])
def test_g1_y_g2_tienen_un_destino_fijo_aunque_el_cliente_no_envie_devolver_a(
    gate: int, destino: str
) -> None:
    devolucion = decidir_devolucion(gate, None, {}, MAXIMO)

    # Antes se guardaba devolver_a = None y gs_accion_coherencia_chk respondía 500.
    assert devolucion.destino == destino
    assert devolucion.estado_job == destino


@pytest.mark.parametrize("gate", [1, 2])
def test_g1_y_g2_aceptan_devolver_a_si_coincide_con_su_destino(gate: int) -> None:
    destino = {1: "design", 2: "build"}[gate]

    assert decidir_devolucion(gate, destino, {}, MAXIMO).destino == destino


@pytest.mark.parametrize(("gate", "otro"), [(1, "qa"), (2, "design")])
def test_g1_y_g2_rechazan_otro_devolver_a(gate: int, otro: str) -> None:
    with pytest.raises(AppError) as error:
        decidir_devolucion(gate, otro, {}, MAXIMO)

    assert error.value.codigo == "VALIDACION_ENTRADA"
    assert error.value.http == 422
    assert error.value.detalle == {
        "campos": [{"campo": "devolver_a", "error": "Solo aplica al Gate 3."}]
    }


@pytest.mark.parametrize(("devolver_a", "destino"), [(None, "qa"), ("design", "design")])
def test_g3_vuelve_a_devolver_a_o_por_defecto_a_qa(devolver_a: str | None, destino: str) -> None:
    assert decidir_devolucion(3, devolver_a, {}, MAXIMO).destino == destino


def test_cuenta_las_devoluciones_por_gate_sin_tocar_los_demas_contadores() -> None:
    iteraciones = {"design": 2, "gate1": 1, "gate2": 4}

    devolucion = decidir_devolucion(1, None, iteraciones, MAXIMO)

    assert devolucion.iteracion == 2
    assert devolucion.iteraciones == {"design": 2, "gate1": 2, "gate2": 4}
    # No muta el dict del job.
    assert iteraciones["gate1"] == 1


def test_sin_iteraciones_previas_es_la_primera() -> None:
    assert decidir_devolucion(2, None, None, MAXIMO).iteracion == 1


def test_hasta_max_iteraciones_vuelve_al_agente() -> None:
    devolucion = decidir_devolucion(1, None, {"gate1": MAXIMO - 1}, MAXIMO)

    assert devolucion.iteracion == MAXIMO
    assert not devolucion.escalado
    assert devolucion.estado_job == "design"


def test_la_siguiente_a_max_iteraciones_escala_a_una_persona() -> None:
    devolucion = decidir_devolucion(1, None, {"gate1": MAXIMO}, MAXIMO)

    assert devolucion.iteracion == MAXIMO + 1
    assert devolucion.escalado
    assert devolucion.estado_job == "escalado_humano"
    # El destino sigue registrándose en la firma (devolver_a no puede ser nulo).
    assert devolucion.destino == "design"


@pytest.mark.parametrize("gate", [1, 2, 3])
@pytest.mark.parametrize("escalado", [False, True])
def test_toda_devolucion_es_una_transicion_valida_de_la_maquina(gate: int, escalado: bool) -> None:
    previas = {f"gate{gate}": MAXIMO if escalado else 0}

    devolucion = decidir_devolucion(gate, None, previas, MAXIMO)

    assert es_transicion_valida(EstadoJob(f"gate{gate}"), EstadoJob(devolucion.estado_job))


def _ticket(gate: int | None) -> dict:
    return {"id": "tk_1", "gate_pendiente": gate}


def test_gate_activo_si_ticket_y_job_estan_en_el_gate() -> None:
    job = {"id": "jb_1", "estado": "gate2"}

    assert exigir_gate_activo(_ticket(2), job, 2) is job


@pytest.mark.parametrize(
    ("gate_ticket", "job"),
    [
        (None, {"id": "jb_1", "estado": "gate1"}),  # el ticket no espera un gate
        (2, {"id": "jb_1", "estado": "gate2"}),  # espera otro gate
        (1, None),  # sin job activo
        # El ticket aún muestra el gate pero el job ya no está ahí (escalado, devuelto).
        (1, {"id": "jb_1", "estado": "escalado_humano"}),
        (1, {"id": "jb_1", "estado": "design"}),
    ],
)
def test_gate_no_activo(gate_ticket: int | None, job: dict | None) -> None:
    with pytest.raises(AppError) as error:
        exigir_gate_activo(_ticket(gate_ticket), job, 1)

    assert error.value.codigo == "GATE_NO_ACTIVO"
    assert error.value.http == 409
