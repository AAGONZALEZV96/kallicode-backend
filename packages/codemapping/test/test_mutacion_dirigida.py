"""Pruebas unitarias dirigidas para matar mutantes en codemapping (> 90%)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    estimar_tokens_ingesta,
    estimar_tokens_texto,
    verificar_presupuesto_tenant,
)
from kallicode_codemapping.curation import fabricar_curation_items
from kallicode_codemapping.ingest_docs import (
    clasificar_tipo_documento,
    trocear_documento_markdown,
)
from kallicode_codemapping.ontology_mapreduce import (
    agrupar_definiciones_por_modulo,
    inducir_ontologia_map_reduce,
)
from kallicode_codemapping.tenant_thresholds import (
    UMBRAL_CONFIANZA_DEFAULT,
    obtener_umbral_confianza_tenant,
)


# ----------------------------------------------------------------------
# 1. Mutantes en tenant_thresholds.py
# ----------------------------------------------------------------------
def test_mutantes_tenant_thresholds_cobertura_total() -> None:
    # Mata mutaciones en UMBRAL_CONFIANZA_DEFAULT
    assert UMBRAL_CONFIANZA_DEFAULT == 0.75

    # Mata mutaciones en validación de rangos 0.0 y 1.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=0.0) == 0.0
    assert obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.0) == 1.0

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=-0.001)

    with pytest.raises(ValueError, match="El umbral de confianza debe estar en el rango"):
        obtener_umbral_confianza_tenant(None, "t1", override_umbral=1.001)

    # Conexión cerrada
    conn_closed = MagicMock()
    conn_closed.closed = True
    assert obtener_umbral_confianza_tenant(conn_closed, "t1") == 0.75

    # Conexión lanza excepción
    conn_err = MagicMock()
    conn_err.closed = False
    conn_err.cursor.side_effect = RuntimeError("DB down")
    assert obtener_umbral_confianza_tenant(conn_err, "t1") == 0.75

    # Resultado DB nulo o vacío
    conn_ok = MagicMock()
    conn_ok.closed = False
    cur = MagicMock()
    conn_ok.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = (None,)
    assert obtener_umbral_confianza_tenant(conn_ok, "t1") == 0.75


# ----------------------------------------------------------------------
# 2. Mutantes en budget.py
# ----------------------------------------------------------------------
def test_mutantes_budget_estimaciones_y_excepcion() -> None:
    assert estimar_tokens_texto("") == 0
    assert estimar_tokens_texto("a") == 1
    assert estimar_tokens_texto("abcd") == 2

    p, c = estimar_tokens_ingesta([])
    assert p == 0
    assert c == 0

    simbolos = [
        {"signature": None, "snippet": None},
        {"signature": "def foo(): pass", "snippet": "x" * 2000},
    ]
    prompt_tokens, comp_tokens = estimar_tokens_ingesta(simbolos)
    assert prompt_tokens > 0
    assert comp_tokens == 300

    # Fallo presupuestario con mock
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = (100, 100)  # Cuota consumida al 100%

    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(
            conn, "t-broke", [{"signature": "def test():", "snippet": "pass"}]
        )
    assert "Presupuesto insuficiente" in str(exc_info.value)


# ----------------------------------------------------------------------
# 3. Mutantes en ontology_mapreduce.py
# ----------------------------------------------------------------------
def test_mutantes_ontology_mapreduce_bucles_y_ramas() -> None:
    # 1. Definiciones vacías
    res_vacio = inducir_ontologia_map_reduce([], lambda *args: {})
    assert res_vacio == {"concepts": []}

    # 2. Agrupación por módulo con claves ausentes
    defs = [
        {"id": "1", "module": ""},
        {"id": "2", "module": None},
        {"id": "3", "module": "auth"},
    ]
    grupos = agrupar_definiciones_por_modulo(defs)
    assert "root" in grupos
    assert len(grupos["root"]) == 2

    # 3. Respuesta map vacía
    def llm_map_vacio(paso: str, prompt: str, vars_: dict[str, Any]) -> dict[str, Any]:
        return {"concepts": []}

    res_sin_conceptos = inducir_ontologia_map_reduce(defs, llm_map_vacio)
    assert res_sin_conceptos == {"concepts": []}

    # 4. Forzar iteración del while en Reduce (lote_maximo_conceptos pequeño)
    llamadas_reduce = 0

    def llm_multi_reduce(paso: str, prompt: str, vars_: dict[str, Any]) -> dict[str, Any]:
        nonlocal llamadas_reduce
        if paso == "codemapping.ontology_map":
            return {"concepts": [{"name_en": f"C_{i}"} for i in range(10)]}
        elif paso == "codemapping.ontology_reduce":
            llamadas_reduce += 1
            return {"concepts": [{"name_en": "Merged"}]}
        elif paso == "codemapping.ontology_global":
            return {"concepts": [{"name_en": "FinalCanonical"}]}
        return {}

    res_reduce = inducir_ontologia_map_reduce(
        definitions=[{"id": "1", "module": "mod"}],
        ejecutar_llm=llm_multi_reduce,
        lote_maximo_defs=10,
        lote_maximo_conceptos=3,  # Obliga a iterar el while
    )
    assert llamadas_reduce >= 1
    assert res_reduce["concepts"][0]["name_en"] == "FinalCanonical"


# ----------------------------------------------------------------------
# 4. Mutantes en curation.py
# ----------------------------------------------------------------------
def test_mutantes_curation_items_estructuras() -> None:
    # Casos con valores None/faltantes para probar los `or` defensivos
    raw = [
        {
            "definition_id": None,
            "symbol_id": "sym_fallback",
            "concept_name_en": None,
            "concept_id": "cid_fallback",
            "confidence": None,
            "reasoning": None,
        }
    ]
    items = fabricar_curation_items(raw, "tenant-x", "snap-y")
    assert len(items) == 1
    item = items[0]
    assert item.origen_id == "sym_fallback"
    assert item.destino_id == "cid_fallback"
    assert item.confianza == 0.0
    assert item.evidencia["motivo"] == "Confianza inferior al umbral del tenant"

    tup = item.to_tuple()
    assert len(tup) == 10
    assert tup[0] == item.id
    assert tup[1] == "tenant-x"
    assert tup[8] == "pendiente"


# ----------------------------------------------------------------------
# 5. Mutantes en ingest_docs.py
# ----------------------------------------------------------------------
def test_mutantes_ingest_docs_clasificacion_y_chunking() -> None:
    # Pruebas de todas las ramas if/elif de clasificar_tipo_documento
    assert clasificar_tipo_documento("adr/decision.md", "") == "adr"
    assert clasificar_tipo_documento("doc.txt", "architecture decision record 01") == "adr"
    assert clasificar_tipo_documento("specs/api.md", "") == "spec"
    assert clasificar_tipo_documento("readme.md", "these are the system requirements") == "spec"
    assert clasificar_tipo_documento("manual_usuario.md", "") == "manual"
    assert clasificar_tipo_documento("guia.pdf", "") == "manual"
    assert clasificar_tipo_documento("politicas_negocio.md", "") == "negocio"
    assert clasificar_tipo_documento("business_rules.txt", "") == "negocio"
    assert clasificar_tipo_documento("random.log", "contenido comun") == "general"

    # Troceado con saltos de línea sin párrafos dobles
    texto_sin_parrafos = "## Seccion Unica\n" + ("linea de texto larga sin saltos dobles\n" * 50)
    chunks = trocear_documento_markdown(
        "doc-flat",
        texto_sin_parrafos,
        ruta="docs/flat.md",
        tamano_max_caracteres=100,
        solape_caracteres=20,
    )
    assert len(chunks) > 1
    assert all(c.tipo_doc == "general" for c in chunks)
