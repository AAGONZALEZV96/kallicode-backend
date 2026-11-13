"""Consultas del grafo: resolución de {{nuggets}} (KC-CM-08-3) y búsqueda híbrida (KC-CM-08-4).

Cliente síncrono (psycopg) del contrato cm-consulta-v1, cuya única definición (modelos, parámetros,
orden y recorte) está en `kallicode_core.cm_consulta`. Aquí solo vive la misma consulta traducida
a la sintaxis de parámetros de psycopg.

Uso desde el pipeline:

    with GraphLoader(settings=settings) as loader:
        variables = contexto_plantilla(loader, ficheros=["src/billing/invoice.py"],
                                       modulo="src/billing")
        variables["nuggets"]  # "- Una factura pagada no puede anularse.\\n- ..."

Sin reglas aplicables cada variable es "" (nunca falla por falta de nuggets).

Búsqueda semántica (contrato cm-busqueda-v1, `kallicode_core.cm_busqueda`; la consulta es la
función SQL cm.buscar_hibrido de la migración 043):

    with GraphLoader(settings=settings) as loader:
        r = buscar_por_texto(loader, TEIEmbeddingClient(settings.tei_endpoint_url),
                             "donde se implementa la facturación por convenio corporativo",
                             capas=["symbol", "definition", "doc"], modulo="src/facturacion")
        [x.id for x in r.resultados]

Recorrido del grafo (KC-CM-09-1; consultas y modelos en `kallicode_core.cm_impacto`):

    with GraphLoader(settings=settings) as loader:
        r = impacto(loader, ConsultaGrafoIn(ficheros=["src/billing/invoice.py"], k=3))
        [(n.id, n.distancia) for n in r.nodos]   # quién se ve afectado y a cuántos saltos
        dependencias_ocultas(loader, ConsultaGrafoIn(nodos=["sym_x"])).dependencias

Incidentes del área para {{incidents}} (KC-CM-09-2; contrato en `kallicode_core.cm_incidentes`):

    with GraphLoader(settings=settings) as loader:
        contexto_incidentes(loader, ficheros=["src/billing/invoice.py"],
                            texto="La factura duplica el IVA", embebedor=tei)
        # {"incidents": "- [KC-0042] IVA duplicado al reemitir — cause: ... — fix: ..."}
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from kallicode_core import cm_busqueda, cm_consulta, cm_impacto, cm_incidentes
from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut, CapaBusqueda
from kallicode_core.cm_consulta import (
    ConsultaNuggetsIn,
    ConsultaNuggetsOut,
    VariablePlantilla,
    construir_respuesta,
    parametros_sql,
)
from kallicode_core.cm_impacto import (
    Arista,
    ConsultaGrafoIn,
    DependenciasOcultasOut,
    Direccion,
    NodoGrafo,
    RecorridoOut,
    SubgrafoOut,
    TipoArista,
)
from kallicode_core.cm_incidentes import ConsultaIncidentesIn, ConsultaIncidentesOut

# La consulta de kallicode_core.cm_consulta.SQL_NUGGETS_APLICABLES con `%(nombre)s` en lugar de
# `:nombre` (sintaxis de psycopg). Se deriva al importar para que ambas no puedan desviarse;
# test/test_cm_queries_unit.py comprueba la traducción.
_PARAMETRO = re.compile(r"(?<![:\w]):(\w+)")
SQL_NUGGETS_APLICABLES = _PARAMETRO.sub(r"%(\1)s", cm_consulta.SQL_NUGGETS_APLICABLES)
# Igual para la llamada a cm.buscar_hibrido (kallicode_core.cm_busqueda.SQL_BUSQUEDA_HIBRIDA).
SQL_BUSQUEDA_HIBRIDA = _PARAMETRO.sub(r"%(\1)s", cm_busqueda.SQL_BUSQUEDA_HIBRIDA)
# Y para las ocho consultas de recorrido del grafo (kallicode_core.cm_impacto.CONSULTAS).
CONSULTAS_IMPACTO: dict[str, str] = {
    nombre: _PARAMETRO.sub(r"%(\1)s", sql) for nombre, sql in cm_impacto.CONSULTAS.items()
}
# Y para la capa de incidentes (kallicode_core.cm_incidentes.CONSULTAS).
CONSULTAS_INCIDENTES: dict[str, str] = {
    nombre: _PARAMETRO.sub(r"%(\1)s", sql) for nombre, sql in cm_incidentes.CONSULTAS.items()
}


class CursorFabrica(Protocol):
    """Lo que necesita el resolutor: GraphLoader.fase() (transacción con app.tenant_id fijado)."""

    def fase(self, nombre: str) -> Any: ...


class Embebedor(Protocol):
    """Lo que necesita buscar_por_texto: TEIEmbeddingClient.embed_texts_sync."""

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]: ...


def resolver_nuggets(loader: CursorFabrica, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut:
    """Resuelve una variable en una transacción de lectura confinada al tenant del loader."""
    with loader.fase("lectura") as cur:
        cur.execute(SQL_NUGGETS_APLICABLES, parametros_sql(consulta))
        columnas = [c.name for c in cur.description]
        filas = [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]
    return construir_respuesta(consulta, filas)


def contexto_plantilla(
    loader: CursorFabrica,
    *,
    nodos: Sequence[str] = (),
    ficheros: Sequence[str] = (),
    modulo: str | None = None,
    fichero: str | None = None,
    presupuesto_caracteres: int = cm_consulta.PRESUPUESTO_DEFECTO,
) -> dict[str, str]:
    """Valores de {{nuggets}}, {{module_conventions}} y {{nuggets_for_file}} para un área.

    `module_conventions` se resuelve si hay `modulo`, y `nuggets_for_file` si hay `fichero`; sin
    ellos la variable queda en "".
    """
    valores = {v.value: "" for v in VariablePlantilla}
    consultas = [
        ConsultaNuggetsIn(
            variable=VariablePlantilla.NUGGETS,
            nodos=list(nodos),
            ficheros=list(ficheros),
            presupuesto_caracteres=presupuesto_caracteres,
        )
    ]
    if modulo:
        consultas.append(
            ConsultaNuggetsIn(
                variable=VariablePlantilla.MODULE_CONVENTIONS,
                modulo=modulo,
                presupuesto_caracteres=presupuesto_caracteres,
            )
        )
    if fichero:
        consultas.append(
            ConsultaNuggetsIn(
                variable=VariablePlantilla.NUGGETS_FOR_FILE,
                ficheros=[fichero],
                presupuesto_caracteres=presupuesto_caracteres,
            )
        )
    for consulta in consultas:
        valores[consulta.variable.value] = resolver_nuggets(loader, consulta).texto
    return valores


# ---------------------------------------------------------------------------
# Búsqueda semántica híbrida (KC-CM-08-4)
# ---------------------------------------------------------------------------
def buscar_hibrido(loader: CursorFabrica, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut:
    """cm.buscar_hibrido en una transacción de lectura confinada al tenant del loader."""
    with loader.fase("busqueda") as cur:
        cur.execute(SQL_BUSQUEDA_HIBRIDA, cm_busqueda.parametros_sql(consulta))
        columnas = [c.name for c in cur.description]
        filas = [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]
    return cm_busqueda.construir_respuesta(consulta, filas)


def buscar_por_texto(
    loader: CursorFabrica,
    embebedor: Embebedor,
    texto: str,
    *,
    capas: Sequence[CapaBusqueda | str] | None = None,
    modulo: str | None = None,
    k: int = cm_busqueda.K_DEFECTO,
    incluir_sin_curar: bool = False,
) -> BusquedaHibridaOut:
    """Embebe `texto` (BGE-M3 vía TEI) y busca. El texto vacío es un error, no una búsqueda."""
    if not texto.strip():
        raise ValueError("la consulta de búsqueda no puede estar vacía")
    vectores = embebedor.embed_texts_sync([texto])
    if len(vectores) != 1:
        raise ValueError(f"el embebedor devolvio {len(vectores)} vectores para 1 texto")
    consulta = BusquedaHibridaIn(
        embedding=vectores[0],
        capas=None if capas is None else [CapaBusqueda(c) for c in capas],
        modulo=modulo,
        k=k,
        incluir_sin_curar=incluir_sin_curar,
    )
    return buscar_hibrido(loader, consulta)


def precision_en_k(devueltos: Sequence[str], relevantes: Iterable[str], k: int = 5) -> float:
    """P@k: fracción de los k primeros devueltos que son relevantes (denominador k, fijo).

    Si se devuelven menos de k, los huecos cuentan como fallos: una búsqueda que devuelve poco
    no puede puntuar más que una que devuelve k aciertos.
    """
    if k < 1:
        raise ValueError("k debe ser >= 1")
    relevantes_set = set(relevantes)
    return sum(1 for d in devueltos[:k] if d in relevantes_set) / k


# ---------------------------------------------------------------------------
# Recorrido del grafo: impacto a k saltos y dependencias ocultas (KC-CM-09-1)
# ---------------------------------------------------------------------------
def _filas(cur: Any, consulta: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cur.execute(CONSULTAS_IMPACTO[consulta], params)
    columnas = [c.name for c in cur.description]
    return [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]


def _limitar_tiempo(cur: Any) -> None:
    """statement_timeout local a la transacción: una consulta degenerada falla, no bloquea."""
    cur.execute(
        "SELECT set_config('statement_timeout', %s, true)", (str(cm_impacto.TIEMPO_MAXIMO_MS),)
    )


def resolver_nodos(loader: CursorFabrica, ids: Sequence[str]) -> list[NodoGrafo]:
    """Los ids que existen en el tenant activo, con su kind (orden por id)."""
    with loader.fase("lectura") as cur:
        filas = _filas(cur, "resolver_nodos", {"nodos": list(ids)})
    return [NodoGrafo(id=str(f["id"]), kind=str(f["kind"])) for f in filas]


def semillas(loader: CursorFabrica, consulta: ConsultaGrafoIn) -> list[NodoGrafo]:
    """Nodos de partida de una consulta: los nodos pedidos y cada fichero con sus símbolos."""
    with loader.fase("lectura") as cur:
        filas = _filas(cur, "semillas", {"nodos": consulta.nodos, "ficheros": consulta.ficheros})
    return [NodoGrafo(id=str(f["id"]), kind=str(f["kind"])) for f in filas]


def _recorrer(
    loader: CursorFabrica, consulta: ConsultaGrafoIn, nombre: str, direccion: Direccion
) -> RecorridoOut:
    with loader.fase("lectura") as cur:
        _limitar_tiempo(cur)
        filas = _filas(cur, nombre, cm_impacto.parametros_recorrido(consulta, direccion))
    return cm_impacto.construir_recorrido(consulta, direccion, filas)


def impacto(loader: CursorFabrica, consulta: ConsultaGrafoIn) -> RecorridoOut:
    """Nodos que dependen del área hasta k saltos (aristas recorridas en sentido inverso)."""
    return _recorrer(loader, consulta, "impacto", "entrante")


def dependencias(loader: CursorFabrica, consulta: ConsultaGrafoIn) -> RecorridoOut:
    """Nodos de los que depende el área hasta k saltos (aristas recorridas hacia delante)."""
    return _recorrer(loader, consulta, "dependencias", "saliente")


def subgrafo(loader: CursorFabrica, consulta: ConsultaGrafoIn) -> SubgrafoOut:
    """Vecindad del área en ambos sentidos hasta k saltos y las aristas entre sus nodos."""
    params = cm_impacto.parametros_recorrido(consulta, "ambas")
    with loader.fase("lectura") as cur:
        _limitar_tiempo(cur)
        filas_nodos = _filas(cur, "subgrafo_nodos", params)
        ids = [str(f["id"]) for f in filas_nodos[: cm_impacto.MAX_NODOS]]
        filas_aristas = _filas(
            cur,
            "aristas_entre",
            {"ids": ids, "tipos": params["tipos"], "limite": cm_impacto.MAX_ARISTAS + 1},
        )
    return cm_impacto.construir_subgrafo(consulta, filas_nodos, filas_aristas)


def vecinos(
    loader: CursorFabrica,
    nodo: str,
    direccion: Direccion = "ambas",
    tipos: Sequence[TipoArista | str] | None = None,
) -> list[Arista]:
    """Aristas a un salto de `nodo`, filtradas por sentido y tipo."""
    with loader.fase("lectura") as cur:
        filas = _filas(cur, "vecinos", cm_impacto.parametros_vecinos(nodo, direccion, tipos))
    return cm_impacto.aristas(filas)


def dependencias_ocultas(
    loader: CursorFabrica, consulta: ConsultaGrafoIn
) -> DependenciasOcultasOut:
    """Nodos que comparten tabla (USES_TABLE) con lo que el área alcanza por CALLS en k saltos,
    pero que no son alcanzables por CALLS: el acoplamiento que no se ve en el grafo de llamadas."""
    params = cm_impacto.parametros_recorrido(consulta, "saliente")
    with loader.fase("lectura") as cur:
        _limitar_tiempo(cur)
        ids_semillas = [
            str(f["id"])
            for f in _filas(
                cur, "semillas", {"nodos": params["nodos"], "ficheros": params["ficheros"]}
            )
        ]
        filas = _filas(cur, "dependencias_ocultas", params)
    return cm_impacto.construir_ocultas(consulta, ids_semillas, filas)


# ---------------------------------------------------------------------------
# Capa de incidentes: {{incidents}} (KC-CM-09-2)
# ---------------------------------------------------------------------------
def resolver_incidentes(
    loader: CursorFabrica, consulta: ConsultaIncidentesIn
) -> ConsultaIncidentesOut:
    """Incidentes del tenant activo enlazados a nodos a <= `saltos` del área, recortados.

    Sin área no consulta la base: devuelve la respuesta vacía (texto "").
    """
    if not consulta.con_area():
        return cm_incidentes.respuesta_vacia(consulta)
    with loader.fase("lectura") as cur:
        _limitar_tiempo(cur)
        cur.execute(CONSULTAS_INCIDENTES["incidentes_area"], cm_incidentes.parametros_sql(consulta))
        columnas = [c.name for c in cur.description]
        filas = [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]
    return cm_incidentes.construir_respuesta(consulta, filas)


def contexto_incidentes(
    loader: CursorFabrica,
    *,
    nodos: Sequence[str] = (),
    ficheros: Sequence[str] = (),
    texto: str | None = None,
    embebedor: Embebedor | None = None,
    saltos: int = cm_incidentes.SALTOS_DEFECTO,
    presupuesto_caracteres: int = cm_incidentes.PRESUPUESTO_DEFECTO,
) -> dict[str, str]:
    """Valor de {{incidents}} para un área ("" si no hay incidentes; nunca falla por ello).

    Con `texto` y `embebedor` ordena también por similitud semántica con el texto del ticket.
    """
    embedding: list[float] | None = None
    if texto and texto.strip() and embebedor is not None and (nodos or ficheros):
        vectores = embebedor.embed_texts_sync([texto])
        if len(vectores) != 1:
            raise ValueError(f"el embebedor devolvio {len(vectores)} vectores para 1 texto")
        embedding = vectores[0]
    consulta = ConsultaIncidentesIn(
        nodos=list(nodos),
        ficheros=list(ficheros),
        saltos=saltos,
        embedding=embedding,
        presupuesto_caracteres=presupuesto_caracteres,
    )
    return {"incidents": resolver_incidentes(loader, consulta).texto}
