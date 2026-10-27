"""Tests del contrato GrafoCliente y su doble de prueba (KC-BE-04-1).

No requieren base de datos: el doble es Python puro.
"""

from __future__ import annotations

import dataclasses

import pytest
from kallicode_core.grafo import (
    GrafoCliente,
    GrafoClienteDoble,
    Impacto,
    Subgrafo,
    grafo_cliente,
)


def test_subgrafo_vacio_por_defecto() -> None:
    s = Subgrafo()
    assert s.esta_vacio()
    assert s.nodos == ()
    assert s.aristas == ()


def test_impacto_vacio_por_defecto() -> None:
    i = Impacto()
    assert i.esta_vacio()
    assert i.radio == 0
    assert i.riesgo == "bajo"
    assert i.modulos == ()
    assert i.tablas == ()


def test_subgrafo_es_inmutable() -> None:
    s = Subgrafo(nodos=({"id": "n1"},))
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.nodos = ()  # type: ignore[misc]


def test_subgrafo_con_nodos_no_esta_vacio() -> None:
    assert not Subgrafo(nodos=({"id": "n1"},)).esta_vacio()
    assert not Subgrafo(aristas=({"de": "n1", "a": "n2"},)).esta_vacio()


def test_impacto_con_radio_no_esta_vacio() -> None:
    assert not Impacto(radio=3).esta_vacio()
    assert not Impacto(modulos=("m1",)).esta_vacio()
    assert not Impacto(tablas=("t1",)).esta_vacio()


@pytest.mark.asyncio
async def test_doble_devuelve_vacio_por_defecto() -> None:
    c = GrafoClienteDoble()
    assert (await c.subgrafo_ticket("t1", "KC-1")).esta_vacio()
    assert (await c.impacto("t1", "n1", k=2)).esta_vacio()
    assert await c.inventario_ui("t1") == []


@pytest.mark.asyncio
async def test_doble_registra_y_devuelve_subgrafo() -> None:
    c = GrafoClienteDoble()
    esperado = Subgrafo(
        nodos=({"id": "n1"}, {"id": "n2"}),
        aristas=({"de": "n1", "a": "n2"},),
    )
    c.registrar_subgrafo("KC-1", esperado)

    s = await c.subgrafo_ticket("t1", "KC-1")
    assert s == esperado
    assert not s.esta_vacio()


@pytest.mark.asyncio
async def test_doble_registra_y_devuelve_impacto() -> None:
    c = GrafoClienteDoble()
    esperado = Impacto(radio=3, riesgo="alto", modulos=("m1", "m2"), tablas=("t1",))
    c.registrar_impacto("n1", esperado)

    assert await c.impacto("t1", "n1") == esperado


@pytest.mark.asyncio
async def test_doble_ignora_el_tenant_para_el_subgrafo() -> None:
    """El doble indexa por ticket_id, no por tenant (es un mock para tests).

    La implementación real (Sprint 10, KC-BE-10-6) sí debe filtrar por
    tenant; aquí dejamos constancia explícita para no confundir el
    comportamiento del doble con el del contrato real.
    """
    c = GrafoClienteDoble()
    c.registrar_subgrafo("KC-1", Subgrafo(nodos=({"id": "n1"},)))

    s_a = await c.subgrafo_ticket("tenant_a", "KC-1")
    s_b = await c.subgrafo_ticket("tenant_b", "KC-1")
    assert s_a == s_b


@pytest.mark.asyncio
async def test_fabrica_devuelve_doble_en_sprint_4() -> None:
    c = grafo_cliente()
    assert isinstance(c, GrafoClienteDoble)
    # runtime_checkable permite verificar cumplimiento estructural en runtime.
    assert isinstance(c, GrafoCliente)
