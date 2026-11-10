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
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from kallicode_core import cm_busqueda, cm_consulta
from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut, CapaBusqueda
from kallicode_core.cm_consulta import (
    ConsultaNuggetsIn,
    ConsultaNuggetsOut,
    VariablePlantilla,
    construir_respuesta,
    parametros_sql,
)

# La consulta de kallicode_core.cm_consulta.SQL_NUGGETS_APLICABLES con `%(nombre)s` en lugar de
# `:nombre` (sintaxis de psycopg). Se deriva al importar para que ambas no puedan desviarse;
# test/test_cm_queries_unit.py comprueba la traducción.
_PARAMETRO = re.compile(r"(?<![:\w]):(\w+)")
SQL_NUGGETS_APLICABLES = _PARAMETRO.sub(r"%(\1)s", cm_consulta.SQL_NUGGETS_APLICABLES)
# Igual para la llamada a cm.buscar_hibrido (kallicode_core.cm_busqueda.SQL_BUSQUEDA_HIBRIDA).
SQL_BUSQUEDA_HIBRIDA = _PARAMETRO.sub(r"%(\1)s", cm_busqueda.SQL_BUSQUEDA_HIBRIDA)


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
