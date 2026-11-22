"""El expediente entrega el texto de cada diff para el visor del portal (KC-PT-08-1)."""

from __future__ import annotations

import hashlib

import pytest
from kallicode_api.servicios import expediente

pytestmark = pytest.mark.asyncio

PASO = {
    "salida_ref": "trazas-llm/org/KC-1/st_1/salida.json",
    "step_id": "st_1",
    "paso": "build.diff.1",
}
DIFF = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-viejo\n+nuevo\n"


def _blob(monkeypatch, salida: dict) -> None:
    async def leer(ref: str) -> dict:
        assert ref == PASO["salida_ref"]
        return salida

    monkeypatch.setattr(expediente, "leer_json_servidor", leer)


async def test_el_cambio_trae_el_diff_completo_y_su_hash(monkeypatch) -> None:
    _blob(monkeypatch, {"diff": DIFF, "rationale_en": "Por qué"})

    cambio = await expediente._cambio_desde_traza(PASO, "https://github.com/o/r/pull/1")

    assert cambio == {
        "archivo": "app.py",
        "pr_url": "https://github.com/o/r/pull/1",
        "racional": "Por qué",
        "referencia": PASO["salida_ref"],
        "hash_sha256": hashlib.sha256(DIFF.encode("utf-8")).hexdigest(),
        "diff": DIFF,
        "diff_truncado": False,
    }


async def test_un_diff_enorme_se_corta_pero_el_hash_es_el_del_completo(monkeypatch) -> None:
    enorme = DIFF + "+" + "x" * expediente.MAX_CARACTERES_DIFF
    _blob(monkeypatch, {"diff": enorme})

    cambio = await expediente._cambio_desde_traza(PASO, None)

    assert cambio is not None
    assert cambio["diff_truncado"] is True
    assert cambio["diff"] == enorme[: expediente.MAX_CARACTERES_DIFF]
    assert cambio["hash_sha256"] == hashlib.sha256(enorme.encode("utf-8")).hexdigest()


async def test_un_diff_justo_en_el_tope_no_se_marca_cortado(monkeypatch) -> None:
    justo = (DIFF * expediente.MAX_CARACTERES_DIFF)[: expediente.MAX_CARACTERES_DIFF]
    _blob(monkeypatch, {"diff": justo})

    cambio = await expediente._cambio_desde_traza(PASO, None)

    assert cambio is not None
    assert (cambio["diff"], cambio["diff_truncado"]) == (justo, False)
