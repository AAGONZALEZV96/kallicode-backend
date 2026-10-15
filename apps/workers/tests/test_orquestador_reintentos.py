"""Tests de la política de reintentos y escalado (KC-BE-04-1).

No requieren base de datos: la política es una función pura.
"""

from __future__ import annotations

import pytest
from kallicode_workers.orquestador.politica import (
    MAX_INTENTOS_POR_ETAPA,
    decidir,
)


def test_sin_error_avanza() -> None:
    d = decidir("triage", intento=1, error=None)
    assert d.accion == "avanzar"
    assert d.motivo is None
    assert d.severidad is None


def test_primer_fallo_reintenta() -> None:
    d = decidir("triage", intento=1, error=RuntimeError("boom"))
    assert d.accion == "reintentar"
    assert d.motivo is None


def test_segundo_fallo_escala() -> None:
    d = decidir("triage", intento=MAX_INTENTOS_POR_ETAPA, error=RuntimeError("boom"))
    assert d.accion == "escalar"
    assert d.motivo == "validacion_persistente"
    assert d.severidad in {"alta", "media"}


def test_tercer_fallo_sigue_escalando() -> None:
    d = decidir("triage", intento=MAX_INTENTOS_POR_ETAPA + 1, error=RuntimeError("x"))
    assert d.accion == "escalar"


@pytest.mark.parametrize("etapa", ["triage", "design", "build", "seguridad"])
def test_severidad_alta_en_etapas_criticas(etapa: str) -> None:
    d = decidir(etapa, intento=MAX_INTENTOS_POR_ETAPA, error=RuntimeError("x"))
    assert d.severidad == "alta"


@pytest.mark.parametrize("etapa", ["qa", "deploy"])
def test_severidad_media_en_etapas_menos_criticas(etapa: str) -> None:
    d = decidir(etapa, intento=MAX_INTENTOS_POR_ETAPA, error=RuntimeError("x"))
    assert d.severidad == "media"


def test_decision_es_inmutable() -> None:
    import dataclasses

    d = decidir("triage", intento=1, error=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.accion = "escalar"  # type: ignore[misc]
