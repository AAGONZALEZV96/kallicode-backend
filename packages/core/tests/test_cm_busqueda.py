"""Contrato cm-busqueda-v1 (KC-CM-08-4): cliente async de Backend sin base de datos.

La semántica de cm.buscar_hibrido se prueba contra PostgreSQL en
packages/codemapping/tests/test_busqueda.py.
"""

from __future__ import annotations

from typing import Any

from kallicode_core import cm_busqueda
from kallicode_core.cm_busqueda import BusquedaHibridaIn, CapaBusqueda


class _Resultado:
    def __init__(self, filas: list[dict[str, Any]]) -> None:
        self._filas = filas

    def mappings(self) -> _Resultado:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._filas


class _Sesion:
    def __init__(self, filas: list[dict[str, Any]]) -> None:
        self.filas = filas
        self.ejecutadas: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, sentencia: Any, params: dict[str, Any]) -> _Resultado:
        self.ejecutadas.append((str(sentencia), params))
        return _Resultado(self.filas)


async def test_buscar_hibrido_llama_a_la_funcion_sql_con_los_parametros_del_contrato() -> None:
    fila = {
        "capa": "nugget",
        "id": "ng_1",
        "referencia": "chunk_1",
        "modulo": None,
        "titulo": "Sección",
        "contenido": "Una factura pagada no se anula.",
        "estado_curacion": "curado",
        "similitud": 0.75,
    }
    sesion = _Sesion([fila])
    consulta = BusquedaHibridaIn(embedding=[0.5] * 1024, capas=["nugget"], k=3)

    r = await cm_busqueda.buscar_hibrido(sesion, consulta)  # type: ignore[arg-type]

    sql, params = sesion.ejecutadas[0]
    assert "cm.buscar_hibrido(" in sql
    assert params == cm_busqueda.parametros_sql(consulta)
    assert [(x.capa, x.id, x.estado_curacion) for x in r.resultados] == [
        (CapaBusqueda.NUGGET, "ng_1", "curado")
    ]
    assert not r.incluye_sin_curar


def test_las_columnas_de_la_consulta_son_los_campos_del_resultado() -> None:
    assert set(cm_busqueda.COLUMNAS) == set(cm_busqueda.ResultadoBusqueda.model_fields)
