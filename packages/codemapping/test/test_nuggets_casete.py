"""Paso codemapping.extract_nuggets ejercitado con casete (KC-CM-08-2).

A diferencia de test_nuggets.py, aquí NO se sustituye el cliente LLM: la respuesta grabada en
tests/fixtures/casetes/extract_nuggets.json se sirve por HTTP (respx) y recorre el camino real
extraer_nuggets -> _ejecutar_paso_sync -> kallicode_core.llm.ejecutar_paso, que valida la salida
contra el JSON Schema estricto de la plantilla registrada (reintento con feedback si no cumple).

El casete trae 5 nuggets: 2 con evidencia resoluble, 1 sin evidencia, 1 que cita un chunk que no
estaba en el lote y 1 cuya cita no aparece en el chunk. Solo los 2 primeros sobreviven.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from kallicode_codemapping import nuggets as ng
from kallicode_codemapping.nuggets import ChunkFuente, extraer_nuggets
from kallicode_core.llm import cliente
from kallicode_core.llm.tiers import Tier, config_tier

CASETE = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "casetes" / "extract_nuggets.json"
)
TENANT = "org_test_nuggets_casete"

CHUNKS = [
    ChunkFuente(
        chunk_id="a1f0c9d2-5b1e-5c3a-9d7e-000000000001",
        documento_id="doc_manual_facturacion",
        seccion="Manual de facturación > Anulaciones",
        contenido=(
            "Una factura en estado pagada no puede anularse; para corregirla se emite una nota "
            "de crédito. El descuento comercial se aplica antes de impuestos."
        ),
    ),
    ChunkFuente(
        chunk_id="a1f0c9d2-5b1e-5c3a-9d7e-000000000002",
        documento_id="doc_adr_021",
        seccion="ADR-021 Conciliación > Decisión",
        contenido=(
            "Para evitar errores de redondeo, los importes se almacenan en céntimos como enteros "
            "(bigint) en todas las tablas de cobros."
        ),
    ),
]


def _casete() -> dict[str, Any]:
    datos = json.loads(CASETE.read_text(encoding="utf-8"))
    assert isinstance(datos, dict)
    return datos


def _con_contenido(casete: dict[str, Any], contenido: dict[str, Any]) -> dict[str, Any]:
    otro = copy.deepcopy(casete)
    otro["choices"][0]["message"]["content"] = json.dumps(contenido, ensure_ascii=False)
    return otro


@pytest.fixture
def llm_http() -> Iterator[respx.MockRouter]:
    """Intercepta el endpoint OpenAI-compatible del tier FLASH; cualquier otra salida falla."""
    # El cliente HTTP y los circuitos son globales del módulo: se aíslan por prueba.
    cliente._cliente_http = None
    cliente._circuitos.clear()
    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        yield router
    cliente._cliente_http = None
    cliente._circuitos.clear()


def _ruta(router: respx.MockRouter) -> respx.Route:
    return router.post(f"{config_tier(Tier.FLASH).base_url}/chat/completions")


def test_casete_valida_contra_el_esquema_y_descarta_lo_que_no_tiene_evidencia(
    llm_http: respx.MockRouter,
) -> None:
    ruta = _ruta(llm_http).mock(return_value=httpx.Response(200, json=_casete()))

    resultado = extraer_nuggets(CHUNKS, TENANT)

    assert ruta.call_count == 1
    assert [(n.evidencia_ref, n.tipo) for n in resultado.propuestos] == [
        ("a1f0c9d2-5b1e-5c3a-9d7e-000000000001", "regla_negocio"),
        ("a1f0c9d2-5b1e-5c3a-9d7e-000000000002", "restriccion_tecnica"),
    ]
    assert [n.seccion for n in resultado.propuestos] == [c.seccion for c in CHUNKS]
    assert all(len(n.regla) <= ng.REGLA_MAX_CHARS for n in resultado.propuestos)
    assert [d.motivo for d in resultado.descartados] == [
        ng.SIN_EVIDENCIA,
        ng.EVIDENCIA_NO_RESOLUBLE,
        ng.CITA_NO_ENCONTRADA,
    ]

    # Lo que viajó al modelo: prompt de la plantilla registrada y chunks delimitados.
    cuerpo = json.loads(ruta.calls[0].request.content)
    assert cuerpo["messages"][0]["content"].startswith(ng.EXTRACT_NUGGETS_SYSTEM)
    usuario = cuerpo["messages"][1]["content"]
    assert "<untrusted_payload>" in usuario
    payload = json.loads(usuario.split("<untrusted_payload>\n", 1)[1].rsplit("\n</untrusted", 1)[0])
    delim = payload["delimiter"]
    assert [c["chunk_id"] for c in payload["chunks"]] == [c.chunk_id for c in CHUNKS]
    assert all(c["excerpt"].startswith(f"<{delim}>") for c in payload["chunks"])


@pytest.mark.parametrize(
    "mutar",
    [
        lambda c: c["nuggets"][0].update(confidence=0.97),
        lambda c: c["nuggets"][0].update(rule="r" * (ng.REGLA_MAX_CHARS + 1)),
        lambda c: c["nuggets"][1].update(kind="opinion"),
    ],
    ids=["campo-extra", "regla-sobre-el-limite", "tipo-desconocido"],
)
def test_respuesta_fuera_de_esquema_se_reintenta_con_feedback(
    llm_http: respx.MockRouter, mutar: Any
) -> None:
    casete = _casete()
    invalido = json.loads(casete["choices"][0]["message"]["content"])
    mutar(invalido)
    ruta = _ruta(llm_http).mock(
        side_effect=[
            httpx.Response(200, json=_con_contenido(casete, invalido)),
            httpx.Response(200, json=casete),
        ]
    )

    resultado = extraer_nuggets(CHUNKS, TENANT)

    assert ruta.call_count == 2
    segundo = json.loads(ruta.calls[1].request.content)["messages"][1]["content"]
    assert "PREVIOUS ATTEMPT WAS INVALID" in segundo
    # Lo inválido nunca llega al extractor: sale lo mismo que con el casete correcto.
    assert len(resultado.propuestos) == 2


def test_esquema_incumplido_en_todos_los_tiers_no_propone_nada(
    llm_http: respx.MockRouter,
) -> None:
    """Si ningún tier cumple el esquema, el lote se descarta entero (LLMEscaladoHumano)."""
    casete = _casete()
    invalido = _con_contenido(casete, {"nuggets": [{"rule": "x"}]})
    llm_http.post(url__regex=r".*/(chat/completions|messages)$").mock(
        return_value=httpx.Response(
            200, json={**invalido, "content": [{"type": "text", "text": '{"nuggets": 1}'}]}
        )
    )

    resultado = extraer_nuggets(CHUNKS, TENANT)

    assert resultado.propuestos == []
    assert [d.motivo for d in resultado.descartados] == [ng.LLM_ESCALADO]
