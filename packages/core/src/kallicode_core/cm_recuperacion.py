"""Contrato cm-recuperacion-v1: recuperación en dos fases para Diseño y duplicados (KC-CM-09-5).

Dos usos, un mismo contrato:

1. Contexto para la etapa Diseño. Fase 1: búsqueda vectorial AMPLIA en pgvector
   (`cm.buscar_hibrido`, contrato cm-busqueda-v1, con los parámetros calibrados de 'diseno').
   Fase 2: re-ranking TOPOLÓGICO: cada candidato que es un nodo del grafo se puntúa también por
   su cercanía (saltos) al área del ticket. El resultado se recorta al presupuesto de tokens de
   la etapa.
2. Duplicados semánticos: tickets del MISMO tenant cuya similitud de coseno con el ticket nuevo
   supera el umbral configurado (`vec.ticket_embeddings`, parámetros de 'dedup').

Este módulo es la única definición de las entradas, las salidas y la fórmula de puntuación; la
orquestación (consultas, embebedor, recorte) vive en `kallicode_codemapping.recuperacion`.

Garantías:
- Solo filas del tenant de la sesión (`core.current_tenant()` explícito además de RLS).
- La puntuación es determinista: mismo candidato, mismas semillas y mismo peso dan el mismo orden.
- `tokens_estimados <= presupuesto_tokens` siempre (lo comprueba el constructor de la salida).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION_CONTRATO = "cm-recuperacion-v1"

PRESUPUESTO_TOKENS_DISENO_DEFECTO = 6_000
PRESUPUESTO_TOKENS_MIN = 100
K_AMPLIO_DEFECTO = 50  # fase 1: se trae mucho más de lo que cabe, para que la fase 2 reordene
K_AMPLIO_MAX = 100  # cota de cm.validar_busqueda_hibrida()
SALTOS_DEFECTO = 2
SALTOS_MAX = 5  # igual que cm_impacto.K_MAX
PESO_TOPOLOGICO_DEFECTO = 0.35
SEMILLAS_DESDE_BUSQUEDA = 3  # sin área explícita, los 3 mejores nodos de la fase 1 son las semillas
CARACTERES_MAX_ELEMENTO = 1_200  # tope de contenido por elemento antes de recortar al presupuesto

UMBRAL_DUPLICADO_DEFECTO = 0.70  # igual que kallicode_core.config.Settings.dedup_umbral
K_DUPLICADOS_DEFECTO = 10
K_DUPLICADOS_MAX = 50

# Capas que son nodos del grafo (tienen distancia). doc y nugget no están en el grafo.
CAPAS_NODO: tuple[str, ...] = ("file", "symbol", "db_table", "db_column", "definition", "concept")
CAPAS_DISENO_DEFECTO: tuple[str, ...] = (
    "symbol",
    "file",
    "db_table",
    "db_column",
    "definition",
    "doc",
    "nugget",
)


# ---------------------------------------------------------------------------
# Puntuación (fase 2)
# ---------------------------------------------------------------------------
def proximidad(distancia: int | None) -> float:
    """1 en el área (0 saltos), 1/2 a un salto, 1/3 a dos...; 0 si no está en el vecindario."""
    if distancia is None or distancia < 0:
        return 0.0
    return 1.0 / (1.0 + distancia)


def puntuacion(similitud: float, distancia: int | None, peso_topologico: float) -> float:
    """(1 - w) * similitud + w * proximidad. Con w = 0 es solo la fase 1; con w = 1, solo el grafo."""
    if not 0.0 <= peso_topologico <= 1.0:
        raise ValueError("peso_topologico debe estar entre 0 y 1")
    return round((1.0 - peso_topologico) * similitud + peso_topologico * proximidad(distancia), 6)


# ---------------------------------------------------------------------------
# Modelos: contexto de Diseño
# ---------------------------------------------------------------------------
class ElementoContexto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capa: str
    id: str
    referencia: str
    modulo: str | None
    titulo: str
    contenido: str
    similitud: float = Field(ge=0, le=1, description="Coseno de la fase 1.")
    distancia: int | None = Field(
        description="Saltos al área del ticket; null si no es un nodo o está fuera del vecindario."
    )
    puntuacion: float = Field(ge=0, le=1, description="Puntuación combinada de la fase 2.")


class ContextoDisenoOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-recuperacion-v1"] = VERSION_CONTRATO
    texto: str = Field(
        description="Contexto listo para la plantilla de Diseño ('' si no hay nada)."
    )
    elementos: list[ElementoContexto] = Field(description="Los que entraron en `texto`, en orden.")
    candidatos: int = Field(ge=0, description="Resultados de la fase 1.")
    omitidos_por_presupuesto: int = Field(ge=0)
    presupuesto_tokens: int = Field(ge=PRESUPUESTO_TOKENS_MIN)
    tokens_estimados: int = Field(ge=0)
    reordenado: bool = Field(
        description="False si no hubo semillas y se conservó el orden de la fase 1."
    )
    semillas: list[str]
    saltos: int = Field(ge=1, le=SALTOS_MAX)
    peso_topologico: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _dentro_del_presupuesto(self) -> ContextoDisenoOut:
        if self.tokens_estimados > self.presupuesto_tokens:
            raise ValueError("el contexto supera el presupuesto de tokens de la etapa Diseño")
        return self


# ---------------------------------------------------------------------------
# Modelos y consulta: duplicados de tickets
# ---------------------------------------------------------------------------
class TicketSimilar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    numero: str | None
    titulo: str
    etapa: str
    similitud: float = Field(ge=0, le=1, description="Coseno entre el texto nuevo y el ticket.")


class DuplicadosOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-recuperacion-v1"] = VERSION_CONTRATO
    umbral: float = Field(ge=0, le=1)
    candidatos: list[TicketSimilar] = Field(description="Los k más parecidos, de mayor a menor.")
    duplicados: list[TicketSimilar] = Field(description="Los candidatos con similitud > umbral.")


# Copia con parámetros psycopg en kallicode_codemapping.recuperacion.SQL_TICKETS_SIMILARES.
# La búsqueda va en una CTE MATERIALIZED porque vec.configurar_busqueda('dedup') activa el
# escaneo iterativo en `relaxed_order` (los resultados pueden llegar algo desordenados): se
# reordena fuera, con `distancia + 0` para que el planificador no use el índice para ordenar.
SQL_TICKETS_SIMILARES = """
WITH cand AS MATERIALIZED (
    SELECT e.ticket_id, e.embedding <=> CAST(:embedding AS vector) AS distancia
      FROM vec.ticket_embeddings e
     WHERE e.tenant_id = core.current_tenant()
       AND (CAST(:excluir AS text) IS NULL OR e.ticket_id <> CAST(:excluir AS text))
     ORDER BY e.embedding <=> CAST(:embedding AS vector)
     LIMIT CAST(:k AS integer)
)
SELECT t.id AS ticket_id, t.numero, t.titulo, t.etapa, 1 - c.distancia AS similitud
  FROM cand c
  JOIN core.tickets t ON t.tenant_id = core.current_tenant() AND t.id = c.ticket_id
 ORDER BY c.distancia + 0, t.id COLLATE "C"
"""


def construir_duplicados(umbral: float, filas: Sequence[Mapping[str, Any]]) -> DuplicadosOut:
    """Convierte las filas de SQL_TICKETS_SIMILARES. Duplicado = similitud ESTRICTAMENTE > umbral."""
    if not 0.0 <= umbral <= 1.0:
        raise ValueError("el umbral de duplicados debe estar entre 0 y 1")
    candidatos = [
        TicketSimilar(
            ticket_id=str(f["ticket_id"]),
            numero=None if f["numero"] is None else str(f["numero"]),
            titulo=str(f["titulo"]),
            etapa=str(f["etapa"]),
            similitud=round(min(max(float(f["similitud"]), 0.0), 1.0), 6),
        )
        for f in filas
    ]
    return DuplicadosOut(
        umbral=umbral,
        candidatos=candidatos,
        duplicados=[c for c in candidatos if c.similitud > umbral],
    )
