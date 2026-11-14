"""Doble en memoria de cm-grafo-v1 sin base de datos (KC-CM-09-4).

Que el doble responde exactamente lo mismo que PostgreSQL se prueba en
packages/codemapping/tests/test_contrato_consulta.py; aquí, su semántica sobre grafos pequeños.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from kallicode_core import cm_incidentes
from kallicode_core.cm_busqueda import BusquedaHibridaIn
from kallicode_core.cm_consulta import ConsultaNuggetsIn, VariablePlantilla
from kallicode_core.cm_grafo_doble import (
    ConsultaGrafoDoble,
    GrafoEnMemoria,
    IncidenteDoble,
    grafo_sintetico,
)
from kallicode_core.cm_impacto import ConsultaGrafoIn, ResolverNodosIn, TipoArista, VecinosIn
from kallicode_core.cm_incidentes import ConsultaIncidentesIn

# a -> b -> c -> a (ciclo), s -> s, c USES_TABLE t1, h USES_TABLE t1
GRAFO = GrafoEnMemoria(
    [("fa", "src/a.py", ["a", "b", "c"]), ("fh", "src/h.py", ["h", "s"])],
    ["t1"],
    [
        ("CALLS", "a", "b"),
        ("CALLS", "b", "c"),
        ("CALLS", "c", "a"),
        ("CALLS", "s", "s"),
        ("USES_TABLE", "c", "t1"),
        ("USES_TABLE", "h", "t1"),
        ("CALLS", "a", "fantasma"),  # destino sin nodo: se recorre pero no se devuelve
    ],
)
AHORA = datetime(2026, 11, 13, tzinfo=UTC)


def _vec(i: int) -> tuple[float, ...]:
    v = [0.0] * cm_incidentes.DIMENSION_EMBEDDING
    v[i] = 1.0
    return tuple(v)


def test_recorridos_con_ciclos_y_nodos_sin_registrar() -> None:
    doble = ConsultaGrafoDoble(GRAFO)
    dep = doble.dependencias(ConsultaGrafoIn(nodos=["a"], k=5))
    assert [(n.id, n.distancia) for n in dep.nodos] == [("a", 0), ("b", 1), ("c", 2), ("t1", 3)]
    imp = doble.impacto(ConsultaGrafoIn(nodos=["s"], k=5))
    assert [n.id for n in imp.nodos] == ["s"]
    assert [n.id for n in doble.semillas(ConsultaGrafoIn(ficheros=["src/a.py"]))] == [
        "a",
        "b",
        "c",
        "fa",
    ]
    assert [n.id for n in doble.resolver_nodos(ResolverNodosIn(ids=["t1", "x", "a"]))] == [
        "a",
        "t1",
    ]


def test_subgrafo_vecinos_y_autorreferencia() -> None:
    doble = ConsultaGrafoDoble(GRAFO)
    sub = doble.subgrafo(ConsultaGrafoIn(nodos=["s"], k=2))
    assert [(a.src_id, a.dst_id) for a in sub.aristas] == [("s", "s")]
    # La autorreferencia sale una vez por sentido, como el UNION ALL de la consulta real.
    ambas = doble.vecinos(VecinosIn(nodo="s"))
    assert [(a.src_id, a.dst_id) for a in ambas] == [("s", "s"), ("s", "s")]
    entrantes = doble.vecinos(VecinosIn(nodo="a", direccion="entrante", tipos=[TipoArista.CALLS]))
    assert [(a.src_id, a.dst_id) for a in entrantes] == [("c", "a")]


def test_dependencias_ocultas() -> None:
    r = ConsultaGrafoDoble(GRAFO).dependencias_ocultas(ConsultaGrafoIn(nodos=["a"], k=3))
    assert r.semillas == ["a"]
    assert [(d.id, d.tablas, d.distancia) for d in r.dependencias] == [("h", ["t1"], 2)]


def test_incidentes_por_distancia_similitud_y_recencia() -> None:
    doble = ConsultaGrafoDoble(GRAFO)
    for inc in (
        IncidenteDoble("i_lejos", "Lejos", ("t1",), creado_en=AHORA),
        IncidenteDoble("i_viejo", "Viejo", ("b",), creado_en=AHORA - timedelta(days=9)),
        IncidenteDoble("i_nuevo", "Nuevo", ("b",), creado_en=AHORA),
        IncidenteDoble("i_sim", "Similar", ("b",), creado_en=AHORA - timedelta(days=30),
                       embedding=_vec(1)),
        IncidenteDoble("i_otro", "Sin relación", ("h",), creado_en=AHORA),
    ):  # fmt: skip
        doble.registrar_incidente(inc)
    sin = doble.resolver_incidentes(ConsultaIncidentesIn(nodos=["a"], saltos=1))
    assert [i.id for i in sin.incidentes] == ["i_nuevo", "i_viejo", "i_sim"]
    con = doble.resolver_incidentes(
        ConsultaIncidentesIn(nodos=["a"], saltos=2, embedding=list(_vec(1)))
    )
    assert [i.id for i in con.incidentes] == ["i_sim", "i_nuevo", "i_viejo", "i_lejos"]
    assert con.incidentes[0].similitud == 1.0 and con.incidentes[1].similitud is None
    assert doble.resolver_incidentes(ConsultaIncidentesIn()).texto == ""


def test_nuggets_y_busqueda_registrados_o_vacios() -> None:
    doble = ConsultaGrafoDoble(GRAFO)
    consulta_n = ConsultaNuggetsIn(variable="nuggets", nodos=["a"])
    assert doble.resolver_nuggets(consulta_n).texto == ""
    doble.registrar_nuggets(
        VariablePlantilla.NUGGETS,
        [
            {
                "id": "ng_1",
                "regla": "Una factura pagada no se anula.",
                "tipo": "regla_negocio",
                "seccion": "S",
                "evidencia_ref": "c1",
                "prioridad": 1,
                "score": 0.9,
            }
        ],
    )
    assert doble.resolver_nuggets(consulta_n).texto == "- Una factura pagada no se anula."

    consulta_b = BusquedaHibridaIn(embedding=[0.1] * 1024, k=1)
    assert doble.buscar_hibrido(consulta_b).resultados == []
    fila = {
        "capa": "doc",
        "id": "c1",
        "referencia": "d1",
        "modulo": None,
        "titulo": "T",
        "contenido": "x",
        "estado_curacion": None,
        "similitud": 0.5,
    }
    doble.registrar_busqueda([fila, {**fila, "id": "c2"}])
    assert [r.id for r in doble.buscar_hibrido(consulta_b).resultados] == ["c1"]  # k = 1


def test_grafo_sintetico_es_determinista_y_del_tamano_declarado() -> None:
    g1, g2 = grafo_sintetico(20261113), grafo_sintetico(20261113)
    assert g1.aristas == g2.aristas and g1.aristas != grafo_sintetico(7).aristas
    assert len(g1.kinds) == 200 + 200 * 20 + 150
    assert 18_000 < len(g1.aristas) < 25_000
