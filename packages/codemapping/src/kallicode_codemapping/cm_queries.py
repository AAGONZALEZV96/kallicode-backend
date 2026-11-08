"""Consultas del grafo para las plantillas del pipeline: resolución de {{nuggets}} (KC-CM-08-3).

Cliente síncrono (psycopg) del contrato cm-consulta-v1, cuya única definición (modelos, parámetros,
orden y recorte) está en `kallicode_core.cm_consulta`. Aquí solo vive la misma consulta traducida
a la sintaxis de parámetros de psycopg.

Uso desde el pipeline:

    with GraphLoader(settings=settings) as loader:
        variables = contexto_plantilla(loader, ficheros=["src/billing/invoice.py"],
                                       modulo="src/billing")
        variables["nuggets"]  # "- Una factura pagada no puede anularse.\\n- ..."

Sin reglas aplicables cada variable es "" (nunca falla por falta de nuggets).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from kallicode_core import cm_consulta
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
SQL_NUGGETS_APLICABLES = re.sub(r"(?<![:\w]):(\w+)", r"%(\1)s", cm_consulta.SQL_NUGGETS_APLICABLES)


class CursorFabrica(Protocol):
    """Lo que necesita el resolutor: GraphLoader.fase() (transacción con app.tenant_id fijado)."""

    def fase(self, nombre: str) -> Any: ...


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
