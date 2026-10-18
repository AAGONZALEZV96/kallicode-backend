"""Pruebas unitarias para inducción ontológica Map-Reduce jerárquica (KC-CM-07-1)."""

from typing import Any

from kallicode_codemapping.ontology_mapreduce import (
    agrupar_definiciones_por_modulo,
    inducir_ontologia_map_reduce,
)


def test_agrupar_definiciones_por_modulo() -> None:
    defs = [
        {"id": "1", "name_en": "AuthToken", "module": "auth"},
        {"id": "2", "name_en": "LoginHandler", "module": "auth"},
        {"id": "3", "name_en": "InvoiceService", "module": "billing"},
        {"id": "4", "name_en": "Utils"},  # sin module -> root
    ]
    grupos = agrupar_definiciones_por_modulo(defs)
    assert len(grupos["auth"]) == 2
    assert len(grupos["billing"]) == 1
    assert len(grupos["root"]) == 1


def test_map_reduce_procesa_todas_las_definiciones_sin_truncamiento() -> None:
    # Generar 400 definiciones simuladas repartidas en 4 módulos
    defs: list[dict[str, Any]] = []
    for mod in ["orders", "users", "shipping", "catalog"]:
        for i in range(100):
            defs.append(
                {
                    "id": f"{mod}_{i}",
                    "name_en": f"Definition_{mod}_{i}",
                    "description_en": f"Description for {mod} #{i}",
                    "module": mod,
                    "role": "domain",
                }
            )

    llamadas_map: list[dict[str, Any]] = []
    llamadas_reduce: list[dict[str, Any]] = []

    def mock_ejecutar_llm(paso: str, prompt: str, vars_: dict[str, Any]) -> dict[str, Any]:
        if paso == "codemapping.ontology_map":
            llamadas_map.append(vars_)
            mod = vars_["module"]
            return {"concepts": [{"name_en": f"Concept_{mod}", "category": "core"}]}
        elif "reduce" in paso or "global" in paso:
            llamadas_reduce.append(vars_)
            return {
                "concepts": [
                    {"name_en": "OrderManagement", "category": "sales"},
                    {"name_en": "UserIdentity", "category": "security"},
                ]
            }
        return {"concepts": []}

    res = inducir_ontologia_map_reduce(
        definitions=defs,
        ejecutar_llm=mock_ejecutar_llm,
        lote_maximo_defs=50,  # Fuerza 2 lotes por módulo (4 módulos * 2 = 8 llamadas Map)
        lote_maximo_conceptos=4,
    )

    # 1. Verificar que NO hubo truncamiento (todas las 400 defs fueron procesadas)
    total_defs_procesadas = sum(len(call["definitions"]) for call in llamadas_map)
    assert total_defs_procesadas == 400

    # 2. Hubo fases de Map y al menos un Reduce
    assert len(llamadas_map) == 8
    assert len(llamadas_reduce) >= 1

    # 3. Estructura de salida válida
    assert "concepts" in res
    assert len(res["concepts"]) == 2
    assert res["concepts"][0]["name_en"] == "OrderManagement"


def test_map_reduce_con_lista_vacia() -> None:
    res = inducir_ontologia_map_reduce(
        definitions=[],
        ejecutar_llm=lambda *args: {"concepts": []},
    )
    assert res == {"concepts": []}
