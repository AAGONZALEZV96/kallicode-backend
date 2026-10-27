"""Pruebas dirigidas a mutantes sobrevivientes (KC-CM-06-6).

La primera corrida de mutmut sobre budget, tenant_thresholds, checkpoints y
embeddings dejó 114 de 336 mutantes vivos: los tests existentes ejecutaban el
código pero no fijaban sus valores exactos (constantes, límites, claves de los
diccionarios devueltos, argumentos pasados a las dependencias). Cada prueba de
este archivo fija uno de esos comportamientos.

Quedan sin cubrir, a propósito, los mutantes equivalentes: cambiar
`encoding="utf-8"` por `"UTF-8"` o por el valor por defecto no altera nada en
contenido ASCII (json.dumps escapa lo demás), y `[:1000]` -> `[:1001]` cae en el
mismo tramo de `ceil`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kallicode_codemapping import checkpoints, embeddings
from kallicode_codemapping.budget import (
    BudgetExceededError,
    estimar_tokens_ingesta,
    estimar_tokens_texto,
    verificar_presupuesto_tenant,
)
from kallicode_codemapping.config import build_settings
from kallicode_codemapping.tenant_thresholds import (
    UMBRAL_CONFIANZA_DEFAULT,
    obtener_umbral_confianza_tenant,
)

# --------------------------------------------------------------------------
# budget.estimar_tokens_texto
# --------------------------------------------------------------------------


def test_texto_vacio_cuesta_cero_tokens() -> None:
    assert estimar_tokens_texto("") == 0


def test_texto_corto_cuesta_al_menos_un_token() -> None:
    assert estimar_tokens_texto("a") == 1


@pytest.mark.parametrize(("largo", "esperado"), [(37, 10), (38, 11), (74, 20), (370, 100)])
def test_texto_usa_3_7_caracteres_por_token(largo: int, esperado: int) -> None:
    assert estimar_tokens_texto("x" * largo) == esperado


# --------------------------------------------------------------------------
# budget.estimar_tokens_ingesta
# --------------------------------------------------------------------------


def test_ingesta_sin_simbolos_es_cero() -> None:
    assert estimar_tokens_ingesta([]) == (0, 0)


def test_ingesta_un_simbolo_valores_exactos() -> None:
    # prompt = 120 + 10 (firma 37c) + 20 (snippet 74c) = 150; +15 % = 173; completion fija 150
    simbolo = {"signature": "x" * 37, "snippet": "y" * 74}
    assert estimar_tokens_ingesta([simbolo]) == (173, 150)


def test_ingesta_acumula_por_simbolo_y_aplica_overhead_al_total() -> None:
    simbolo = {"signature": "x" * 37, "snippet": "y" * 74}
    # prompt 300 -> overhead ceil(45.0) = 45 -> 345; completion 300
    assert estimar_tokens_ingesta([simbolo, simbolo]) == (345, 300)


def test_ingesta_simbolo_sin_firma_ni_snippet_solo_paga_la_plantilla() -> None:
    # 120 de plantilla + ceil(18.0) de overhead
    assert estimar_tokens_ingesta([{}]) == (138, 150)


def test_ingesta_acota_el_snippet_a_1000_caracteres() -> None:
    largo = estimar_tokens_ingesta([{"snippet": "y" * 1500}])
    acotado = estimar_tokens_ingesta([{"snippet": "y" * 1000}])
    assert largo == acotado
    assert estimar_tokens_ingesta([{"snippet": "y" * 1000}]) != estimar_tokens_ingesta([{}])


def test_ingesta_firma_y_snippet_se_suman_no_se_restan() -> None:
    solo_firma = estimar_tokens_ingesta([{"signature": "x" * 370}])[0]
    solo_snippet = estimar_tokens_ingesta([{"snippet": "y" * 370}])[0]
    ambos = estimar_tokens_ingesta([{"signature": "x" * 370, "snippet": "y" * 370}])[0]
    base = estimar_tokens_ingesta([{}])[0]
    assert solo_firma > base
    assert solo_snippet > base
    assert ambos > solo_firma
    assert ambos > solo_snippet


# --------------------------------------------------------------------------
# budget.verificar_presupuesto_tenant
# --------------------------------------------------------------------------

TRES_SIMBOLOS: list[dict[str, Any]] = [{}, {}, {}]
# prompt 360 + 54 de overhead = 414; completion 450; total 864 tokens
TOTAL_TRES = 864


def _conn(fila: tuple[int, int] | None) -> MagicMock:
    conn = MagicMock(name="conn")
    cur = MagicMock(name="cur")
    cur.fetchone.return_value = fila
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    conn.cur = cur
    return conn


def test_presupuesto_aprobado_devuelve_la_estimacion_completa() -> None:
    est = verificar_presupuesto_tenant(_conn((10_000, 1_000)), "t1", TRES_SIMBOLOS)

    assert est.total_tokens_estimados == TOTAL_TRES
    assert est.prompt_tokens_estimados == 414
    assert est.completion_tokens_estimados == 450
    assert est.cuota_disponible == 9_000
    assert est.presupuesto_suficiente is True


def test_costo_usa_precio_por_defecto_de_050_y_redondea_a_4_decimales() -> None:
    est = verificar_presupuesto_tenant(_conn((10_000, 0)), "t1", TRES_SIMBOLOS)
    # 864 / 1e6 * 0.50 = 0.000432 -> 0.0004
    assert est.costo_usd_estimado == 0.0004


def test_costo_respeta_el_precio_indicado_y_es_proporcional_a_los_tokens() -> None:
    grande: list[dict[str, Any]] = [{}] * 1000  # 288 000 tokens
    est = verificar_presupuesto_tenant(
        _conn((10_000_000, 0)), "t1", grande, precio_por_millon_usd=2.0
    )
    assert est.total_tokens_estimados == 288_000
    assert est.costo_usd_estimado == 0.576


def test_consulta_la_cuota_del_tenant_indicado() -> None:
    conn = _conn((10_000, 0))
    verificar_presupuesto_tenant(conn, "org_x", TRES_SIMBOLOS)

    sql, params = conn.cur.execute.call_args.args
    assert "core.subscriptions" in sql
    assert "core.usage_tokens" in sql
    assert params == ("org_x",)


def test_sin_suscripcion_aplica_el_plan_por_defecto_de_500k() -> None:
    est = verificar_presupuesto_tenant(_conn(None), "t1", TRES_SIMBOLOS)
    assert est.cuota_disponible == 500_000
    assert est.presupuesto_suficiente is True


def test_sin_suscripcion_y_consumo_cero_rechaza_lo_que_excede_500k() -> None:
    enorme: list[dict[str, Any]] = [{}] * 2000  # 576 000 tokens > 500 000
    with pytest.raises(BudgetExceededError, match="Disponible en ciclo: 500000 tokens"):
        verificar_presupuesto_tenant(_conn(None), "t1", enorme)


def test_cuota_justa_alcanza() -> None:
    est = verificar_presupuesto_tenant(_conn((TOTAL_TRES, 0)), "t1", TRES_SIMBOLOS)
    assert est.presupuesto_suficiente is True
    assert est.cuota_disponible == TOTAL_TRES


def test_un_token_menos_de_cuota_no_alcanza() -> None:
    with pytest.raises(BudgetExceededError) as exc:
        verificar_presupuesto_tenant(_conn((TOTAL_TRES - 1, 0)), "t1", TRES_SIMBOLOS)
    mensaje = str(exc.value)
    assert "KC-CM-05-5" in mensaje
    assert "t1" in mensaje
    assert f"Requerido: {TOTAL_TRES} tokens" in mensaje
    assert f"Disponible en ciclo: {TOTAL_TRES - 1} tokens" in mensaje


def test_consumo_superior_a_la_cuota_deja_cero_disponible_nunca_negativo() -> None:
    with pytest.raises(BudgetExceededError, match="Disponible en ciclo: 0 tokens"):
        verificar_presupuesto_tenant(_conn((100, 500)), "t1", TRES_SIMBOLOS)


# --------------------------------------------------------------------------
# tenant_thresholds
# --------------------------------------------------------------------------


@pytest.mark.parametrize("limite", [0.0, 1.0])
def test_override_acepta_los_limites_del_rango(limite: float) -> None:
    assert obtener_umbral_confianza_tenant(None, "t", override_umbral=limite) == limite


@pytest.mark.parametrize("invalido", [-0.01, 1.01])
def test_override_fuera_de_rango_se_rechaza(invalido: float) -> None:
    with pytest.raises(ValueError, match="rango"):
        obtener_umbral_confianza_tenant(None, "t", override_umbral=invalido)


def _conn_umbral(fila: tuple[Any, ...] | None, cerrada: bool = False) -> MagicMock:
    conn = MagicMock(name="conn")
    cur = MagicMock(name="cur")
    cur.fetchone.return_value = fila
    conn.cursor.return_value.__enter__.return_value = cur
    conn.closed = cerrada
    conn.cur = cur
    return conn


def test_umbral_consulta_la_organizacion_con_default_y_tenant() -> None:
    conn = _conn_umbral((0.6,))
    assert obtener_umbral_confianza_tenant(conn, "org_a") == 0.6

    sql, params = conn.cur.execute.call_args.args
    assert "link_confidence_threshold" in sql
    assert "core.organizations" in sql
    assert params == (UMBRAL_CONFIANZA_DEFAULT, "org_a")


@pytest.mark.parametrize(
    ("guardado", "esperado"), [(1.7, 1.0), (-0.4, 0.0), (0.0, 0.0), (1.0, 1.0)]
)
def test_umbral_de_bd_se_acota_a_0_1(guardado: float, esperado: float) -> None:
    assert obtener_umbral_confianza_tenant(_conn_umbral((guardado,)), "t") == esperado


def test_conexion_cerrada_usa_el_default_sin_consultar() -> None:
    conn = _conn_umbral((0.2,), cerrada=True)
    assert obtener_umbral_confianza_tenant(conn, "t") == UMBRAL_CONFIANZA_DEFAULT
    conn.cur.execute.assert_not_called()


def test_error_de_consulta_degrada_al_default() -> None:
    conn = _conn_umbral((0.2,))
    conn.cur.execute.side_effect = RuntimeError("columna inexistente")
    assert obtener_umbral_confianza_tenant(conn, "t") == UMBRAL_CONFIANZA_DEFAULT


def test_organizacion_sin_fila_usa_el_default() -> None:
    assert obtener_umbral_confianza_tenant(_conn_umbral(None), "t") == UMBRAL_CONFIANZA_DEFAULT


# --------------------------------------------------------------------------
# checkpoints
# --------------------------------------------------------------------------


def test_guardar_dos_veces_en_el_mismo_snapshot_sobrescribe_y_conserva_otras_fases(
    tmp_path: Path,
) -> None:
    checkpoints.guardar(str(tmp_path), "t", "s", "ingesta", {"v": 1})
    checkpoints.guardar(str(tmp_path), "t", "s", "semantica", {"v": 9})
    checkpoints.guardar(str(tmp_path), "t", "s", "ingesta", {"v": 2})

    assert checkpoints.cargar(str(tmp_path), "t", "s", "ingesta") == {"v": 2}
    assert checkpoints.cargar(str(tmp_path), "t", "s", "semantica") == {"v": 9}


def test_guardar_crea_la_ruta_tenant_checkpoints_snapshot(tmp_path: Path) -> None:
    checkpoints.guardar(str(tmp_path), "org", "snap", "ingesta", {"a": 1})
    assert (tmp_path / "org" / "checkpoints" / "snap" / "ingesta.json").is_file()


# --------------------------------------------------------------------------
# embeddings
# --------------------------------------------------------------------------


def test_cliente_tei_quita_solo_barras_finales_y_usa_30s_de_timeout() -> None:
    cliente = embeddings.TEIEmbeddingClient("http://hostX//")
    assert cliente.base_url == "http://hostX"
    assert cliente.timeout == 30.0
    assert embeddings.TEIEmbeddingClient("http://h", timeout=5.0).timeout == 5.0


def test_cliente_sync_pasa_su_timeout_a_httpx() -> None:
    with patch("kallicode_codemapping.embeddings.httpx.Client") as http:
        http.return_value.__enter__.return_value.post.return_value.json.return_value = [[0.0]]
        embeddings.TEIEmbeddingClient("http://h", timeout=7.5).embed_texts_sync(["a"])
    http.assert_called_once_with(timeout=7.5)


@pytest.mark.asyncio
async def test_cliente_async_pasa_su_timeout_a_httpx() -> None:
    with patch("kallicode_codemapping.embeddings.httpx.AsyncClient") as http:
        cliente_http = http.return_value.__aenter__.return_value
        respuesta = MagicMock()
        respuesta.json.return_value = [[0.0]]
        cliente_http.post = MagicMock(side_effect=_awaitable(respuesta))
        await embeddings.TEIEmbeddingClient("http://h", timeout=7.5).embed_texts(["a"])
    http.assert_called_once_with(timeout=7.5)


def _awaitable(valor: Any) -> Any:
    async def _f(*_a: Any, **_k: Any) -> Any:
        return valor

    return _f


def test_preparar_items_devuelve_el_registro_completo() -> None:
    res = embeddings.preparar_items_vectoriales(
        "tenant_1", "docs", [{"node_ref": "n1", "text": "hola", "payload": {"k": "v"}}]
    )
    assert res == [
        {
            "id": str(embeddings.generar_uuid5_vector("tenant_1", "docs", "n1")),
            "tenant_id": "tenant_1",
            "layer": "docs",
            "node_ref": "n1",
            "text": "hola",
            "contenido_hash": embeddings.calcular_contenido_hash("hola"),
            "payload": {"k": "v"},
        }
    ]


def test_preparar_items_convierte_el_texto_a_cadena() -> None:
    res = embeddings.preparar_items_vectoriales("t", "code", [{"node_ref": "n", "text": 42}])
    assert res[0]["text"] == "42"
    assert res[0]["contenido_hash"] == embeddings.calcular_contenido_hash("42")


def _embed_y_upsert(items: list[dict[str, Any]], vectores: list[list[float]]) -> Any:
    cfg = build_settings(tenant_id="tenant_1", repo_url="x", tei_endpoint_url="http://tei:1234")
    with patch("kallicode_codemapping.embeddings.TEIEmbeddingClient") as tei:
        tei.return_value.embed_texts_sync.return_value = vectores
        res = embeddings.embed_and_upsert(cfg, "definitions", items)
    return res, tei


def test_embed_and_upsert_llama_a_tei_con_su_endpoint_y_los_textos() -> None:
    _, tei = _embed_y_upsert(
        [{"node_ref": "a", "text": "uno"}, {"node_ref": "b", "text": "dos"}], [[1.0], [2.0]]
    )
    tei.assert_called_once_with(base_url="http://tei:1234")
    tei.return_value.embed_texts_sync.assert_called_once_with(["uno", "dos"])


def test_embed_and_upsert_devuelve_el_registro_completo() -> None:
    res, _ = _embed_y_upsert([{"node_ref": "a", "text": "uno", "payload": {"k": 1}}], [[1.0]])
    assert res == [
        {
            "id": str(embeddings.generar_uuid5_vector("tenant_1", "definitions", "a")),
            "tenant_id": "tenant_1",
            "layer": "definitions",
            "node_ref": "a",
            "text": "uno",
            "vector": [1.0],
            "contenido_hash": embeddings.calcular_contenido_hash("uno"),
            "payload": {"k": 1},
        }
    ]


def test_embed_and_upsert_prefiere_node_ref_y_cae_en_id() -> None:
    res, _ = _embed_y_upsert(
        [
            {"node_ref": "ref", "id": "otro", "text": "a"},
            {"id": "solo-id", "text": "b"},
        ],
        [[1.0], [2.0]],
    )
    assert [r["node_ref"] for r in res] == ["ref", "solo-id"]
    assert res[0]["id"] == str(embeddings.generar_uuid5_vector("tenant_1", "definitions", "ref"))
    assert res[1]["id"] == str(
        embeddings.generar_uuid5_vector("tenant_1", "definitions", "solo-id")
    )


def test_embed_and_upsert_payload_por_defecto_es_diccionario_vacio() -> None:
    res, _ = _embed_y_upsert([{"node_ref": "a", "text": "uno"}], [[1.0]])
    assert res[0]["payload"] == {}


def test_embed_and_upsert_rechaza_cantidad_de_vectores_distinta() -> None:
    with pytest.raises(ValueError):
        _embed_y_upsert(
            [{"node_ref": "a", "text": "uno"}, {"node_ref": "b", "text": "dos"}], [[1.0]]
        )
