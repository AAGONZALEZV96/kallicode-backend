"""Vigencia de los embeddings: medición y reparación tras el refresco diario (KC-CM-11-4).

Un embedding está VIGENTE cuando su `texto_hash` es el hash del texto canónico de su nodo calculado ahora
desde cm.* (embeddings.texto_embedding_simbolo / texto_embedding_definicion) y es del modelo vigente. El
refresco incremental solo reembebe los símbolos de los ficheros que cambiaron; una descripción curada,
una definición reconsolidada o un cambio de modelo dejan embeddings que ya no corresponden a su nodo.
Este paso, al final de cada refresco, los busca en la base (vec.embeddings_a_reparar, migración 060), los
reembebe hasta `vigencia_max_reembebidos` por corrida y registra la vigencia resultante.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .embeddings import MODELO_EMBEDDING, TAMANO_LOTE_TEI, Embebedor, embeber_en_lotes
from .logging_setup import get_logger
from .metricas import UMBRAL_VIGENCIA, registrar_reparacion_vigencia

logger = get_logger()


class _Loader(Protocol):
    tenant_id: str

    def embeddings_a_reparar(self, limite: int, modelo: str = ...) -> list[dict[str, Any]]: ...

    def upsert_vectors(self, items: list[dict[str, Any]], modelo: str = ...) -> None: ...

    def vigencia_embeddings(self, modelo: str = ...) -> list[dict[str, Any]]: ...


@dataclass
class ResultadoVigencia:
    reembebidos: int = 0
    pendientes: bool = False
    motivos: dict[str, int] = field(default_factory=dict)
    capas: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        """Vigencia del tenant sobre todas las capas (1.0 si no hay nodos)."""
        nodos = sum(int(c["nodos"]) for c in self.capas)
        vigentes = sum(int(c["vigentes"]) for c in self.capas)
        return 1.0 if nodos == 0 else vigentes / nodos

    @property
    def cumple(self) -> bool:
        return self.ratio >= UMBRAL_VIGENCIA


def reparar_vigencia(
    loader: _Loader,
    embebedor: Embebedor,
    limite: int,
    *,
    modelo: str = MODELO_EMBEDDING,
    tamano_lote: int = TAMANO_LOTE_TEI,
) -> ResultadoVigencia:
    """Reembebe hasta `limite` nodos obsoletos o sin embedding y devuelve la vigencia resultante."""
    resultado = ResultadoVigencia()
    if limite > 0:
        candidatos = list(loader.embeddings_a_reparar(limite + 1, modelo))
        resultado.pendientes = len(candidatos) > limite
        lote = candidatos[:limite]
        if lote:
            vectores = embeber_en_lotes(embebedor, [it["text"] for it in lote], tamano_lote)
            for item, vector in zip(lote, vectores, strict=True):
                item["vector"] = vector
                resultado.motivos[item["motivo"]] = resultado.motivos.get(item["motivo"], 0) + 1
            loader.upsert_vectors(lote, modelo)
            resultado.reembebidos = len(lote)

    resultado.capas = list(loader.vigencia_embeddings(modelo))
    registrar_reparacion_vigencia(resultado.motivos, resultado.ratio)
    nivel = logger.info if resultado.cumple else logger.warning
    nivel(
        f"vigencia de embeddings: {resultado.ratio:.2%} tras reembeber {resultado.reembebidos}",
        extra={
            "tenant_id": loader.tenant_id,
            "vigencia": round(resultado.ratio, 4),
            "reembebidos": resultado.reembebidos,
            "motivos": resultado.motivos,
            "pendientes": resultado.pendientes,
            "umbral": UMBRAL_VIGENCIA,
        },
    )
    return resultado
