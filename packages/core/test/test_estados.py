"""Pruebas unitarias para la máquina de estados canónica de KalliCode."""

import pytest
from kallicode_core.pipeline.estados import (
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
    """Valida que los estados finales no puedan transitar a ningún otro estado."""
    assert len(TRANSICIONES_VALIDAS[EstadoJob.COMPLETADO]) == 0
    assert len(TRANSICIONES_VALIDAS[EstadoJob.FALLIDO]) == 0
    assert not es_transicion_valida(EstadoJob.COMPLETADO, EstadoJob.BUILD)
    assert not es_transicion_valida(EstadoJob.FALLIDO, EstadoJob.TRIAGE)


@pytest.mark.parametrize(
    ("origen", "destino", "esperado"),
    [
        (EstadoJob.CREADO, EstadoJob.TRIAGE, True),
        (EstadoJob.TRIAGE, EstadoJob.DESIGN, True),
        (EstadoJob.GATE1_ESPERA, EstadoJob.BUILD, True),
        (EstadoJob.GATE1_ESPERA, EstadoJob.GATE1_RECHAZADO, True),
        (EstadoJob.BUILD, EstadoJob.VERIFICACION_INTERNA, True),
        (EstadoJob.DEPLOY, EstadoJob.COMPLETADO, True),
        # Transiciones inválidas prohibidas (saltos ilegales)
        (EstadoJob.CREADO, EstadoJob.BUILD, False),
        (EstadoJob.DESIGN, EstadoJob.DEPLOY, False),
        (EstadoJob.GATE1_ESPERA, EstadoJob.COMPLETADO, False),
        ("estado_invalido", EstadoJob.BUILD, False),
    ],
)
def test_reglas_de_transicion(
    origen: EstadoJob | str,
    destino: EstadoJob | str,
    esperado: bool,
) -> None:
    assert es_transicion_valida(origen, destino) is esperado


def test_obtener_destinos_posibles() -> None:
    destinos_gate1 = obtener_destinos_posibles(EstadoJob.GATE1_ESPERA)
    assert EstadoJob.BUILD in destinos_gate1
    assert EstadoJob.GATE1_RECHAZADO in destinos_gate1
    assert EstadoJob.FALLIDO in destinos_gate1
