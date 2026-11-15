"""Corte por MAX_ITERACIONES y captura de excepciones del orquestador (KC-BE-06-3).

procesar_job con un cliente interno simulado: no requiere base de datos ni API.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from kallicode_core.pipeline import EstadoJob
from kallicode_workers.orquestador import bucle

pytestmark = pytest.mark.asyncio

MAXIMO = 5  # Settings.max_iteraciones_gate por defecto


def _job(estado: str, iteraciones: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": "jb_1",
        "tenant_id": "t_1",
        "ticket_id": "tk_1",
        "estado": estado,
        "iteraciones": iteraciones if iteraciones is not None else {estado: 1},
    }


def _http_error(status: int) -> httpx.HTTPStatusError:
    peticion = httpx.Request("PATCH", "http://interna/jobs/jb_1/state")
    return httpx.HTTPStatusError(
        f"HTTP {status}", request=peticion, response=httpx.Response(status, request=peticion)
    )


@pytest.fixture
def cliente() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def intentos(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """registrar_intento_fallido simulado (escribe en core.jobs)."""
    registrar = AsyncMock()
    monkeypatch.setattr(bucle, "registrar_intento_fallido", registrar)
    return registrar


def _etapa(monkeypatch: pytest.MonkeyPatch, estado: EstadoJob, resultado: Any) -> None:
    """Reemplaza la etapa: devuelve `resultado` o lo lanza si es una excepción."""

    async def ejecutar(job: dict, cliente: Any, *, intento: int) -> Any:
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    monkeypatch.setitem(bucle.ETAPAS, estado, ejecutar)


# ---------------------------------------------------------------- replanificación


async def test_replanificar_bajo_el_maximo_vuelve_a_design(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    _etapa(monkeypatch, EstadoJob.BUILD, {"needs_replan": True, "replan_reason_en": "scope"})

    await bucle.procesar_job(_job("build", {"build": 1, "design": MAXIMO - 1}), cliente)

    cliente.transicionar.assert_awaited_once()
    assert cliente.transicionar.await_args.kwargs["a"] == "design"
    assert cliente.transicionar.await_args.kwargs["motivo"] == "build_needs_replan"
    cliente.escalar.assert_not_awaited()


async def test_replanificar_con_max_iteraciones_escala_en_vez_de_girar(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    _etapa(monkeypatch, EstadoJob.BUILD, {"needs_replan": True, "replan_reason_en": "scope"})

    await bucle.procesar_job(_job("build", {"build": 1, "design": MAXIMO}), cliente)

    cliente.transicionar.assert_not_awaited()
    cliente.escalar.assert_awaited_once()
    argumentos = cliente.escalar.await_args.kwargs
    assert argumentos["motivo"] == "max_iteraciones"
    assert argumentos["etapa"] == "build"
    assert argumentos["detalle"] == {"replan_reason_en": "scope", "entradas_design": MAXIMO}


async def test_el_maximo_sale_de_la_configuracion(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    class Ajustes:
        max_iteraciones_gate = 2

    monkeypatch.setattr(bucle, "get_settings", lambda: Ajustes())
    _etapa(monkeypatch, EstadoJob.BUILD, {"needs_replan": True})

    await bucle.procesar_job(_job("build", {"build": 1, "design": 2}), cliente)

    cliente.escalar.assert_awaited_once()


# ---------------------------------------------------------- captura de excepciones


async def test_un_409_al_transicionar_se_descarta_sin_contar_un_fallo(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    """El job cambió mientras corría la etapa (p. ej. lo cancelaron)."""
    _etapa(monkeypatch, EstadoJob.BUILD, {"ok": True})
    cliente.transicionar.side_effect = _http_error(409)

    await bucle.procesar_job(_job("build"), cliente)

    intentos.assert_not_awaited()
    cliente.escalar.assert_not_awaited()


async def test_otro_error_al_transicionar_cuenta_como_intento_fallido(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    _etapa(monkeypatch, EstadoJob.BUILD, {"ok": True})
    cliente.transicionar.side_effect = _http_error(503)

    await bucle.procesar_job(_job("build"), cliente)

    intentos.assert_awaited_once_with("jb_1", "t_1", "build")


async def test_si_falla_el_escalado_no_se_propaga_ni_cuenta_otro_intento(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    """La etapa falló y tocaba escalar: el próximo ciclo repite la decisión."""
    _etapa(monkeypatch, EstadoJob.BUILD, RuntimeError("boom"))
    cliente.escalar.side_effect = _http_error(500)

    await bucle.procesar_job(_job("build", {"build": 2}), cliente)

    cliente.escalar.assert_awaited_once()
    intentos.assert_not_awaited()


async def test_si_falla_registrar_el_intento_tampoco_se_propaga(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    _etapa(monkeypatch, EstadoJob.BUILD, {"ok": True})
    cliente.transicionar.side_effect = _http_error(503)
    intentos.side_effect = OSError("sin base de datos")

    await bucle.procesar_job(_job("build"), cliente)

    intentos.assert_awaited_once()


async def test_un_409_en_la_replanificacion_tambien_se_descarta(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    _etapa(monkeypatch, EstadoJob.BUILD, {"needs_replan": True})
    cliente.transicionar.side_effect = _http_error(409)

    await bucle.procesar_job(_job("build", {"build": 1, "design": 1}), cliente)

    intentos.assert_not_awaited()


async def test_el_camino_feliz_sigue_igual(
    monkeypatch: pytest.MonkeyPatch, cliente: AsyncMock, intentos: AsyncMock
) -> None:
    _etapa(monkeypatch, EstadoJob.BUILD, {"ok": True})

    await bucle.procesar_job(_job("build"), cliente)

    cliente.transicionar.assert_awaited_once()
    assert cliente.transicionar.await_args.kwargs["a"] == "qa"
    intentos.assert_not_awaited()
