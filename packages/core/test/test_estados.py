"""Pruebas unitarias para la máquina de estados canónica de KalliCode.

Los 16 estados del enum y su matriz de transiciones coinciden con el CHECK
`job_estado_chk` de core.jobs (fuente de verdad, decisión D06).
"""

import pytest
from kallicode_core.pipeline.estados import (
    GATES,
    TERMINALES,
    TRANSICIONES_VALIDAS,
    EstadoJob,
    es_transicion_valida,
    obtener_destinos_posibles,
)


def test_conteo_exacto_16_estados() -> None:
    """Verifica que el enum contenga con precisión los 16 estados contratados."""
    assert len(EstadoJob) == 16, f"Se esperaban 16 estados, pero hay {len(EstadoJob)}"


def test_integridad_matriz_transiciones() -> None:
    """Verifica que todos los estados tengan una entrada en la matriz de transiciones."""
    for estado in EstadoJob:
        assert estado in TRANSICIONES_VALIDAS, f"Falta definir transiciones para {estado}"


def test_estados_terminales_sin_salidas() -> None:
    """Valida que los estados terminales no puedan transitar a ningún otro estado."""
    for terminal in TERMINALES:
        assert len(TRANSICIONES_VALIDAS[terminal]) == 0
        assert obtener_destinos_posibles(terminal) == frozenset()

    assert not es_transicion_valida(EstadoJob.PRODUCCION, EstadoJob.BUILD)
    assert not es_transicion_valida(EstadoJob.CANCELADO, EstadoJob.TRIAGE)
    assert not es_transicion_valida(EstadoJob.CERRADO_DUPLICADO, EstadoJob.DESIGN)


def test_gates_definidos_correctamente() -> None:
    """Los tres gates del pipeline están declarados y apuntan a 1, 2 y 3."""
    assert GATES[EstadoJob.GATE1] == 1
    assert GATES[EstadoJob.GATE2] == 2
    assert GATES[EstadoJob.GATE3] == 3


@pytest.mark.parametrize(
    ("origen", "destino", "esperado"),
    [
        # Transiciones válidas del flujo principal
        (EstadoJob.PENDIENTE_ASIGNACION, EstadoJob.TRIAGE, True),
        (EstadoJob.TRIAGE, EstadoJob.DESIGN, True),
        (EstadoJob.DESIGN, EstadoJob.GATE1, True),
        (EstadoJob.GATE1, EstadoJob.BUILD, True),
        (EstadoJob.BUILD, EstadoJob.QA, True),
        (EstadoJob.QA, EstadoJob.GATE2, True),
        (EstadoJob.GATE2, EstadoJob.SECURITY, True),
        (EstadoJob.SECURITY, EstadoJob.DEPLOY_PREFLIGHT, True),
        (EstadoJob.DEPLOY_PREFLIGHT, EstadoJob.GATE3, True),
        (EstadoJob.GATE3, EstadoJob.DEPLOY, True),
        (EstadoJob.DEPLOY, EstadoJob.PRODUCCION, True),
        # Devoluciones de gate y ciclos
        (EstadoJob.GATE1, EstadoJob.DESIGN, True),
        (EstadoJob.GATE2, EstadoJob.BUILD, True),
        (EstadoJob.GATE3, EstadoJob.DESIGN, True),
        (EstadoJob.GATE3, EstadoJob.BUILD, True),
        (EstadoJob.GATE3, EstadoJob.QA, True),
        # Estados de escape
        (EstadoJob.PENDIENTE_ASIGNACION, EstadoJob.PAUSADO_POR_CUOTA, True),
        (EstadoJob.BUILD, EstadoJob.PAUSADO_POR_CUOTA, True),
        (EstadoJob.TRIAGE, EstadoJob.ESCALADO_HUMANO, True),
        (EstadoJob.BUILD, EstadoJob.ESCALADO_HUMANO, True),
        (EstadoJob.ESCALADO_HUMANO, EstadoJob.TRIAGE, True),
        # Transiciones inválidas prohibidas (saltos ilegales)
        (EstadoJob.PENDIENTE_ASIGNACION, EstadoJob.BUILD, False),
        (EstadoJob.DESIGN, EstadoJob.DEPLOY, False),
        (EstadoJob.GATE1, EstadoJob.PRODUCCION, False),
        (EstadoJob.GATE1, EstadoJob.QA, False),
        ("estado_invalido", EstadoJob.BUILD, False),
        (EstadoJob.TRIAGE, "estado_invalido", False),
    ],
)
def test_reglas_de_transicion(
    origen: EstadoJob | str,
    destino: EstadoJob | str,
    esperado: bool,
) -> None:
    assert es_transicion_valida(origen, destino) is esperado


def test_obtener_destinos_posibles() -> None:
    destinos_gate1 = obtener_destinos_posibles(EstadoJob.GATE1)
    assert EstadoJob.BUILD in destinos_gate1
    assert EstadoJob.DESIGN in destinos_gate1
    assert EstadoJob.CANCELADO in destinos_gate1

    destinos_gate3 = obtener_destinos_posibles(EstadoJob.GATE3)
    assert EstadoJob.DEPLOY in destinos_gate3
    assert EstadoJob.DESIGN in destinos_gate3
    assert EstadoJob.BUILD in destinos_gate3
    assert EstadoJob.QA in destinos_gate3
    assert EstadoJob.CANCELADO in destinos_gate3


def test_obtener_destinos_de_estado_invalido() -> None:
    """Un string que no corresponde a ningún estado devuelve frozenset vacío."""
    assert obtener_destinos_posibles("estado_invalido") == frozenset()


def test_es_transicion_valida_con_estados_string() -> None:
    """`es_transicion_valida` acepta strings además de miembros del enum."""
    assert es_transicion_valida("pendiente_asignacion", "triage") is True
    assert es_transicion_valida("triage", "build") is False
