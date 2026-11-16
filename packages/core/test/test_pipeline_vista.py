"""Pipeline que ve el cliente (KC-BE-06-5, cierra BE-F42). Funciones puras: sin base de datos."""

from __future__ import annotations

import pytest
from kallicode_core.pipeline import (
    ETAPA_DE_GATE,
    ETAPAS_FLUJO,
    EstadoJob,
    PasoPipeline,
    etapa_visible,
    mapear_pipeline,
)
from kallicode_core.pipeline.estados import GATES


def _estados(pasos: list[PasoPipeline]) -> list[str]:
    return [p.estado for p in pasos]


def _resumen(pasos: list[PasoPipeline]) -> str:
    """'D' hecha, 'A' actual, '.' pendiente, en el orden del flujo."""
    return "".join({"done": "D", "actual": "A", "pendiente": "."}[p.estado] for p in pasos)


def test_el_flujo_tiene_el_orden_del_stepper() -> None:
    assert ETAPAS_FLUJO == ("triage", "design", "build", "qa", "security", "deploy", "produccion")


@pytest.mark.parametrize(
    ("etapa", "esperado"),
    [
        ("triage", "A......"),
        ("design", "DA....."),
        ("build", "DDA...."),
        ("qa", "DDDA..."),
        ("security", "DDDDA.."),
        ("deploy", "DDDDDA."),
    ],
)
def test_etapa_del_flujo(etapa: str, esperado: str) -> None:
    pasos = mapear_pipeline(etapa, None)

    assert [p.etapa for p in pasos] == list(ETAPAS_FLUJO)
    assert _resumen(pasos) == esperado


def test_en_produccion_todo_esta_hecho() -> None:
    # Antes «produccion» quedaba como paso actual de un ticket ya terminado.
    assert _resumen(mapear_pipeline("produccion", None)) == "DDDDDDD"


@pytest.mark.parametrize(("gate", "etapa"), [(1, "design"), (2, "qa"), (3, "deploy")])
def test_el_gate_pendiente_va_en_su_etapa_actual(gate: int, etapa: str) -> None:
    pasos = mapear_pipeline(etapa, gate)

    con_gate = [(p.etapa, p.gate) for p in pasos if p.gate is not None]
    assert con_gate == [(etapa, gate)]
    assert next(p for p in pasos if p.etapa == etapa).estado == "actual"


def test_un_gate_que_no_corresponde_a_la_etapa_actual_no_se_muestra() -> None:
    # Datos inconsistentes (gate 2 con el ticket en build): no se inventa un gate.
    assert all(p.gate is None for p in mapear_pipeline("build", 2))


# ------------------------------------------------- fuera del flujo (antes: todo pendiente)


@pytest.mark.parametrize(
    ("previo", "esperado"),
    [
        ("triage", "......."),  # cancelado en triage: nada terminado
        ("build", "DD....."),  # cancelado en build: triage y diseño hechos
        ("gate1", "D......"),  # cancelado esperando el Gate 1 (etapa design)
        ("gate2", "DDD...."),  # esperando el Gate 2 (etapa qa)
        ("deploy_preflight", "DDDDD.."),  # en deploy
    ],
)
def test_cancelado_muestra_lo_hecho_hasta_donde_se_detuvo(previo: str, esperado: str) -> None:
    pasos = mapear_pipeline("cancelado", None, previo)

    assert _resumen(pasos) == esperado
    assert "actual" not in _estados(pasos)


def test_duplicado_deja_hecho_el_triage_donde_se_detecto() -> None:
    assert _resumen(mapear_pipeline("cerrado_duplicado", None, "triage")) == "D......"


@pytest.mark.parametrize(
    ("previo", "esperado"),
    [
        ("pendiente_asignacion", "A......"),  # en cola desde la creación: espera el triage
        ("build", "DDA...."),  # pausado en build: se reanuda ahí
        ("gate1", "DDA...."),  # aprobado el Gate 1 sin cuota: se reanuda en build
    ],
)
def test_en_cola_por_cuota_marca_donde_se_reanuda(previo: str, esperado: str) -> None:
    assert _resumen(mapear_pipeline("en_cola_por_cuota", None, previo)) == esperado


@pytest.mark.parametrize("etapa", ["cancelado", "cerrado_duplicado", "en_cola_por_cuota"])
@pytest.mark.parametrize("previo", [None, "escalado_humano", "estado_inexistente"])
def test_sin_estado_previo_conocido_nada_queda_hecho_ni_actual(
    etapa: str, previo: str | None
) -> None:
    assert _resumen(mapear_pipeline(etapa, None, previo)) == "......."


def test_el_estado_previo_no_cambia_una_etapa_del_flujo() -> None:
    assert _resumen(mapear_pipeline("qa", None, "triage")) == "DDDA..."


def test_una_etapa_que_no_existe_es_un_error() -> None:
    with pytest.raises(ValueError, match="Etapa de ticket desconocida"):
        mapear_pipeline("revision", None)


def test_a_dict_tiene_la_forma_del_endpoint() -> None:
    paso = mapear_pipeline("design", 1)[1]

    assert paso.a_dict() == {"etapa": "design", "estado": "actual", "gate": 1}


# ----------------------------------------------------- espejo del trigger T9


@pytest.mark.parametrize("estado", list(EstadoJob))
def test_cada_estado_del_job_tiene_una_decision(estado: EstadoJob) -> None:
    """Un estado nuevo en EstadoJob obliga a decidir qué etapa ve el cliente."""
    escapes = {
        EstadoJob.PAUSADO_POR_CUOTA,
        EstadoJob.ESCALADO_HUMANO,
        EstadoJob.CANCELADO,
        EstadoJob.CERRADO_DUPLICADO,
    }
    if estado in escapes:
        assert etapa_visible(estado) is None
    else:
        assert etapa_visible(estado) in ETAPAS_FLUJO


def test_cada_gate_espera_en_la_etapa_del_trigger() -> None:
    for estado, numero in GATES.items():
        assert etapa_visible(estado) == ETAPA_DE_GATE[numero]


def test_etapa_visible_acepta_texto_y_rechaza_lo_desconocido() -> None:
    assert etapa_visible("deploy_preflight") == "deploy"
    assert etapa_visible("pendiente_asignacion") == "triage"
    assert etapa_visible("nada") is None
    assert etapa_visible(None) is None
