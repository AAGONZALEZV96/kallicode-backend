"""Contrato de recorrido del grafo sin base de datos (KC-CM-09-1).

- Inspección del SQL de las ocho consultas: toda tabla de `cm` lleva su propio filtro de tenant,
  también la del término recursivo; los recorridos tienen CYCLE y cota de saltos.
- Validación de la entrada y construcción de las respuestas.

La semántica contra PostgreSQL (ciclos, aislamiento, equivalencia y latencia) está en
packages/codemapping/tests/test_impacto.py.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from kallicode_core import cm_impacto
from kallicode_core.cm_impacto import (
    CONSULTAS,
    MAX_ARISTAS,
    MAX_NODOS,
    ConsultaGrafoIn,
    TipoArista,
)
from pydantic import ValidationError

# FROM/JOIN cm.<tabla> <alias>: el alias es obligatorio (lo exige el propio chequeo).
_REFERENCIA = re.compile(r"\b(?:FROM|JOIN)\s+cm\.(\w+)(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE)
_PALABRAS_SQL = {"on", "where", "join", "cross", "left", "group", "order", "limit", "union"}


def referencias_sin_tenant(sql: str) -> list[str]:
    """Referencias a tablas de cm sin `<alias>.tenant_id = core.current_tenant()`.

    Falla también si una tabla aparece sin alias, si dos referencias comparten alias (el filtro de
    una no puede cubrir a la otra) o si hay menciones a `cm.` que el patrón no reconoce.
    """
    problemas: list[str] = []
    referencias = _REFERENCIA.findall(sql)
    menciones = re.findall(r"\bcm\.\w+", sql)
    if len(menciones) != len(referencias):
        problemas.append(f"{len(menciones)} menciones a cm.* y {len(referencias)} FROM/JOIN")
    alias_vistos: set[str] = set()
    for tabla, alias in referencias:
        if not alias or alias.lower() in _PALABRAS_SQL:
            problemas.append(f"cm.{tabla} sin alias")
            continue
        if alias in alias_vistos:
            problemas.append(f"alias repetido: {alias}")
        alias_vistos.add(alias)
        filtro = rf"\b{re.escape(alias)}\.tenant_id\s*=\s*core\.current_tenant\(\)"
        if not re.search(filtro, sql):
            problemas.append(f"cm.{tabla} {alias} sin filtro de tenant")
    return problemas


# ---------------------------------------------------------------------------
# Inspección del SQL generado
# ---------------------------------------------------------------------------
def test_son_las_ocho_consultas() -> None:
    assert sorted(CONSULTAS) == sorted(
        [
            "resolver_nodos",
            "semillas",
            "impacto",
            "dependencias",
            "subgrafo_nodos",
            "aristas_entre",
            "vecinos",
            "dependencias_ocultas",
        ]
    )


@pytest.mark.parametrize("nombre", sorted(CONSULTAS))
def test_toda_tabla_de_cm_filtra_por_tenant(nombre: str) -> None:
    sql = CONSULTAS[nombre]
    assert re.search(r"\bcm\.\w+", sql), "la consulta no lee el grafo"
    assert referencias_sin_tenant(sql) == []


@pytest.mark.parametrize(
    "nombre", ["impacto", "dependencias", "subgrafo_nodos", "dependencias_ocultas"]
)
def test_el_termino_recursivo_filtra_por_tenant_en_cada_nivel(nombre: str) -> None:
    sql = CONSULTAS[nombre]
    assert "WITH RECURSIVE" in sql
    recursivo = sql[sql.index("UNION ALL") : sql.index(") CYCLE")]
    aristas = re.findall(r"\bcm\.edges\s+(\w+)", recursivo)
    assert aristas, "el término recursivo no recorre cm.edges"
    for alias in aristas:
        assert re.search(rf"\b{alias}\.tenant_id\s*=\s*core\.current_tenant\(\)", recursivo)
    assert re.search(r"distancia\s*<\s*CAST\(:k AS integer\)", recursivo), "sin cota de saltos"
    assert re.search(r"\) CYCLE id SET es_ciclo USING ruta", sql)
    assert "NOT a.es_ciclo" in sql or "NOT r.es_ciclo" in sql


@pytest.mark.parametrize(
    ("sql", "esperado"),
    [
        ("SELECT 1 FROM cm.edges e WHERE e.src_id = 'x'", ["cm.edges e sin filtro de tenant"]),
        ("SELECT 1 FROM cm.edges WHERE tenant_id = core.current_tenant()", ["cm.edges sin alias"]),
        (
            "SELECT 1 FROM cm.edges e JOIN cm.nodes e ON e.id = e.src_id "
            "WHERE e.tenant_id = core.current_tenant()",
            ["alias repetido: e"],
        ),
        (
            "SELECT 1 FROM cm.nodes n, cm.edges e WHERE n.tenant_id = core.current_tenant()",
            ["2 menciones a cm.* y 1 FROM/JOIN"],
        ),
        ("SELECT 1 FROM cm.nodes n WHERE n.tenant_id = core.current_tenant()", []),
    ],
    ids=["sin-filtro", "sin-alias", "alias-repetido", "coma-no-reconocida", "correcta"],
)
def test_el_chequeo_detecta_las_omisiones(sql: str, esperado: list[str]) -> None:
    assert referencias_sin_tenant(sql) == esperado


@pytest.mark.parametrize("nombre", sorted(CONSULTAS))
def test_quitar_un_filtro_de_tenant_se_detecta(nombre: str) -> None:
    """El chequeo no pasa por casualidad: sin cualquiera de los filtros, falla."""
    sql = CONSULTAS[nombre]
    filtros = list(re.finditer(r"\w+\.tenant_id = core\.current_tenant\(\)", sql))
    assert filtros
    for f in filtros:
        mutada = sql[: f.start()] + "TRUE" + sql[f.end() :]
        assert referencias_sin_tenant(mutada), f"{nombre}: quitar {f.group()} no se detecta"


def test_sin_parametros_huerfanos() -> None:
    esperados = {
        "resolver_nodos": {"nodos"},
        "semillas": {"nodos", "ficheros"},
        "impacto": {"nodos", "ficheros", "k", "tipos", "limite"},
        "dependencias": {"nodos", "ficheros", "k", "tipos", "limite"},
        "subgrafo_nodos": {"nodos", "ficheros", "k", "tipos", "limite"},
        "aristas_entre": {"ids", "tipos", "limite"},
        "vecinos": {"nodo", "entrantes", "salientes", "tipos", "limite"},
        "dependencias_ocultas": {"nodos", "ficheros", "k", "limite"},
    }
    for nombre, sql in CONSULTAS.items():
        assert set(re.findall(r"(?<![:\w]):(\w+)", sql)) == esperados[nombre], nombre
    consulta = ConsultaGrafoIn(nodos=["a"])
    assert set(cm_impacto.parametros_recorrido(consulta, "entrante")) == esperados["impacto"]
    assert set(cm_impacto.parametros_vecinos("a", "ambas")) == esperados["vecinos"]


def test_los_recorridos_siguen_el_sentido_pedido() -> None:
    def ramas(sql: str) -> set[str]:
        recursivo = sql[sql.index("UNION ALL") : sql.index(") CYCLE")]
        return set(re.findall(r"AND (e\w)\.(src_id|dst_id) = r\.id", recursivo))

    assert ramas(cm_impacto.SQL_IMPACTO) == {("ei", "dst_id")}
    assert ramas(cm_impacto.SQL_DEPENDENCIAS) == {("eo", "src_id")}
    assert ramas(cm_impacto.SQL_SUBGRAFO_NODOS) == {("ei", "dst_id"), ("eo", "src_id")}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def test_la_consulta_exige_semillas() -> None:
    with pytest.raises(ValidationError, match="al menos un nodo o un fichero"):
        ConsultaGrafoIn()
    assert ConsultaGrafoIn(ficheros=["src/a.py"]).k == cm_impacto.K_DEFECTO


@pytest.mark.parametrize("k", [0, cm_impacto.K_MAX + 1])
def test_k_acotado(k: int) -> None:
    with pytest.raises(ValidationError):
        ConsultaGrafoIn(nodos=["a"], k=k)


def test_tipos_invalidos_o_vacios_se_rechazan() -> None:
    with pytest.raises(ValidationError):
        ConsultaGrafoIn(nodos=["a"], tipos=["IMPORTS"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        ConsultaGrafoIn(nodos=["a"], tipos=[])
    with pytest.raises(ValidationError):
        ConsultaGrafoIn(nodos=["a"], extra=1)  # type: ignore[call-arg]


def test_tipos_efectivos_por_defecto_y_pedidos() -> None:
    base = ConsultaGrafoIn(nodos=["a"])
    assert cm_impacto.tipos_efectivos(base, "entrante") == sorted(cm_impacto.TIPOS_DEPENDENCIA)
    assert cm_impacto.tipos_efectivos(base, "ambas") == sorted(TipoArista)
    pedida = ConsultaGrafoIn(nodos=["a"], tipos=["USES_TABLE", "CALLS", "CALLS"])  # type: ignore[list-item]
    assert cm_impacto.tipos_efectivos(pedida, "ambas") == [TipoArista.CALLS, TipoArista.USES_TABLE]
    params = cm_impacto.parametros_recorrido(pedida, "saliente")
    assert params["tipos"] == ["CALLS", "USES_TABLE"]
    assert params["limite"] == MAX_NODOS + 1


@pytest.mark.parametrize(
    ("direccion", "entrantes", "salientes"),
    [("entrante", True, False), ("saliente", False, True), ("ambas", True, True)],
)
def test_parametros_vecinos(direccion: Any, entrantes: bool, salientes: bool) -> None:
    p = cm_impacto.parametros_vecinos("n1", direccion, ["CALLS"])
    assert (p["entrantes"], p["salientes"], p["tipos"]) == (entrantes, salientes, ["CALLS"])
    assert cm_impacto.parametros_vecinos("n1", direccion)["tipos"] == sorted(TipoArista)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
def _fila(i: int, distancia: int, via: str | None = "CALLS") -> dict[str, Any]:
    return {"id": f"n{i}", "kind": "symbol", "distancia": distancia, "via_tipo": via}


def test_construir_recorrido_marca_semillas_y_truncado() -> None:
    consulta = ConsultaGrafoIn(nodos=["n0"], k=2)
    filas = [_fila(0, 0, None), _fila(1, 1), _fila(2, 2, "USES_TABLE")]
    r = cm_impacto.construir_recorrido(consulta, "entrante", filas)
    assert r.version == "cm-impacto-v1"
    assert r.semillas == ["n0"]
    assert [(n.id, n.distancia, n.via_tipo) for n in r.nodos] == [
        ("n0", 0, None),
        ("n1", 1, TipoArista.CALLS),
        ("n2", 2, TipoArista.USES_TABLE),
    ]
    assert not r.truncado

    muchas = [_fila(0, 0, None)] + [_fila(i, 1) for i in range(1, MAX_NODOS + 1)]
    t = cm_impacto.construir_recorrido(consulta, "entrante", muchas)
    assert t.truncado and len(t.nodos) == MAX_NODOS


def test_construir_subgrafo_y_ocultas() -> None:
    consulta = ConsultaGrafoIn(nodos=["n0"], k=1)
    arista = {"tipo": "CALLS", "src_kind": "symbol", "src_id": "n0", "dst_kind": "symbol"}
    s = cm_impacto.construir_subgrafo(
        consulta, [_fila(0, 0, None), _fila(1, 1)], [{**arista, "dst_id": "n1"}]
    )
    assert [a.dst_id for a in s.aristas] == ["n1"] and not s.truncado
    assert s.tipos == sorted(TipoArista)
    s2 = cm_impacto.construir_subgrafo(
        consulta,
        [_fila(0, 0, None)],
        [{**arista, "dst_id": f"x{i}"} for i in range(MAX_ARISTAS + 1)],
    )
    assert s2.truncado and len(s2.aristas) == MAX_ARISTAS

    o = cm_impacto.construir_ocultas(
        consulta,
        ["n0"],
        [{"id": "h", "kind": "symbol", "tablas": ["t2", "t1"], "distancia": 1}],
    )
    assert o.semillas == ["n0"] and not o.truncado
    assert [(d.id, d.tablas, d.distancia) for d in o.dependencias] == [("h", ["t1", "t2"], 1)]
