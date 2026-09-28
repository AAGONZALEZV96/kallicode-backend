from hypothesis import given
from hypothesis import strategies as st
from kallicode_core.pipeline import (
    TRANSICIONES_VALIDAS,
    EstadoJob,
    es_transicion_valida,
)

TODOS_LOS_ESTADOS = list(EstadoJob)

# Estados terminales (sumideros sin transiciones salientes)
ESTADOS_TERMINALES = {e for e in TODOS_LOS_ESTADOS if len(TRANSICIONES_VALIDAS.get(e, set())) == 0}

estrategia_estados = st.sampled_from(TODOS_LOS_ESTADOS)


@given(origen=estrategia_estados, destino=estrategia_estados)
def test_propiedad_consistencia_grafo(origen: EstadoJob, destino: EstadoJob):
    """
    Propiedad 1: Consistencia formal del grafo.
    es_transicion_valida(origen, destino) es True si y solo si
    el destino está explícitamente en TRANSICIONES_VALIDAS[origen].
    """
    permitido_en_dict = destino in TRANSICIONES_VALIDAS.get(origen, set())
    resultado_funcion = es_transicion_valida(origen, destino)

    assert (
        resultado_funcion == permitido_en_dict
    ), f"Inconsistencia en {origen} -> {destino}: dict={permitido_en_dict}, funcion={resultado_funcion}"


@given(terminal=st.sampled_from(list(ESTADOS_TERMINALES)), destino=estrategia_estados)
def test_propiedad_estados_terminales_sin_salidas(terminal: EstadoJob, destino: EstadoJob):
    """
    Propiedad 2: Sumideros estrictos.
    Ningún estado terminal puede transicionar a ningún otro estado del sistema.
    """
    assert not es_transicion_valida(
        terminal, destino
    ), f"El estado terminal {terminal} no debería poder saltar a {destino}"


@given(origen=estrategia_estados)
def test_propiedad_no_auto_transiciones(origen: EstadoJob):
    """
    Propiedad 3: Irreflexividad del grafo.
    Ningún estado permite transicionar hacia sí mismo.
    """
    assert not es_transicion_valida(
        origen, origen
    ), f"El estado {origen} no debe permitir auto-transición"


@given(st.data())
def test_propiedad_caminos_aleatorios_terminan_en_sumidero(data):
    """
    Propiedad 4: Seguridad de ejecución de caminos generados por Hypothesis.
    Cualquier recorrido que parta en CREADO y tome aristas legales sucesivas
    solo se detiene al alcanzar un estado terminal.
    """
    estado_actual = EstadoJob.CREADO
    pasos = 0
    max_pasos = 25  # Evita bucles infinitos en etapas que permitan reintentos/iteraciones

    while estado_actual not in ESTADOS_TERMINALES and pasos < max_pasos:
        salidas = list(TRANSICIONES_VALIDAS.get(estado_actual, set()))
        if not salidas:
            break
        estado_actual = data.draw(st.sampled_from(salidas))
        pasos += 1

    if estado_actual in ESTADOS_TERMINALES:
        assert len(TRANSICIONES_VALIDAS.get(estado_actual, set())) == 0
    else:
        assert pasos == max_pasos
