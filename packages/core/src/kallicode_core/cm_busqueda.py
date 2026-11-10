"""Contrato cm-busqueda-v1: búsqueda semántica híbrida sobre el grafo (KC-CM-08-4).

"¿Dónde se implementa la facturación por convenio corporativo?": dado el embedding BGE-M3 de la
pregunta, devuelve los k elementos más parecidos de las capas de definiciones
(vec.definition_embeddings), documentación (vec.doc_chunks) y nuggets (cm.nuggets), filtrados por
tenant, capa y módulo. La consulta vive en SQL, en `cm.buscar_hibrido` (migración
043_busqueda_hibrida.sql); este módulo es la única definición de su entrada y su salida. Lo usan
Backend (`buscar_hibrido`, SQLAlchemy async) y CodeMapping (`kallicode_codemapping.cm_queries`,
psycopg síncrono).

Garantías (las da la función SQL):
- Solo filas del tenant de la sesión (`core.current_tenant()` explícito en cada recorrido además
  de RLS; sin tenant no devuelve nada).
- Sin `incluir_sin_curar` nunca devuelve conceptos ni nuggets sin curar; los nuggets rechazados no
  se devuelven nunca.
- `hnsw.iterative_scan` con los parámetros calibrados de 'diseno' (KC-DB-08-1): el filtro no hunde
  el recall.
- Orden determinista: similitud descendente y, a igualdad, capa e id (collation "C").
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VERSION_CONTRATO = "cm-busqueda-v1"

DIMENSIONES = 1024  # BGE-M3, igual que vec.* (vector(1024)) y cm.validar_busqueda_hibrida()
K_DEFECTO = 10
K_MAXIMO = 100  # cota de cm.validar_busqueda_hibrida()
MODULO_MAX_CHARS = 500


class CapaBusqueda(StrEnum):
    """Capas de cm.capas_busqueda(): kinds de vec.definition_embeddings, doc y nugget."""

    FILE = "file"
    SYMBOL = "symbol"
    DB_TABLE = "db_table"
    DB_COLUMN = "db_column"
    DEFINITION = "definition"
    CONCEPT = "concept"
    DOC = "doc"
    NUGGET = "nugget"


EstadoCuracion = Literal["curado", "sin_curar"]


class BusquedaHibridaIn(BaseModel):
    """Qué se busca y dónde. El tenant es el de la sesión."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    embedding: list[float] = Field(
        min_length=DIMENSIONES,
        max_length=DIMENSIONES,
        description="Embedding BGE-M3 de la consulta (1024 dimensiones).",
    )
    capas: list[CapaBusqueda] | None = Field(
        default=None,
        min_length=1,
        description="Capas en las que buscar; None = todas.",
    )
    modulo: str | None = Field(
        default=None,
        min_length=1,
        max_length=MODULO_MAX_CHARS,
        description="Prefijo de ruta con semántica de carpeta ('src/billing' no casa 'src/billingx').",
    )
    k: int = Field(default=K_DEFECTO, ge=1, le=K_MAXIMO)
    incluir_sin_curar: bool = Field(
        default=False,
        description="Incluye conceptos y nuggets aún sin curar (petición explícita).",
    )

    @field_validator("modulo")
    @classmethod
    def _modulo_sin_barra_final(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        limpio = valor.strip().rstrip("/")
        if not limpio:
            raise ValueError("modulo no puede ser vacío")
        return limpio


class ResultadoBusqueda(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capa: CapaBusqueda
    id: str
    referencia: str = Field(
        description="nodo_ref (definiciones), document_id (doc) o evidencia_ref (nugget)."
    )
    modulo: str | None
    titulo: str
    contenido: str
    estado_curacion: EstadoCuracion | None = Field(
        description="Solo conceptos y nuggets; None en el resto de capas."
    )
    similitud: float = Field(ge=0, le=1)


class BusquedaHibridaOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-busqueda-v1"] = VERSION_CONTRATO
    resultados: list[ResultadoBusqueda]
    incluye_sin_curar: bool


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
# Copia con parámetros psycopg en kallicode_codemapping.cm_queries.SQL_BUSQUEDA_HIBRIDA.
COLUMNAS = (
    "capa",
    "id",
    "referencia",
    "modulo",
    "titulo",
    "contenido",
    "estado_curacion",
    "similitud",
)
SQL_BUSQUEDA_HIBRIDA = (
    "SELECT " + ", ".join(COLUMNAS) + " FROM cm.buscar_hibrido("
    "CAST(:embedding AS vector), CAST(:capas AS text[]), CAST(:modulo AS text), "
    "CAST(:k AS integer), CAST(:incluir_sin_curar AS boolean))"
)


def vector_literal(vector: Sequence[float]) -> str:
    """Literal de pgvector ('[x,y,...]'). 9 cifras significativas: ida y vuelta exacta en float4."""
    return "[" + ",".join(f"{float(x):.9g}" for x in vector) + "]"


def parametros_sql(consulta: BusquedaHibridaIn) -> dict[str, Any]:
    """Parámetros de SQL_BUSQUEDA_HIBRIDA (iguales en ambos clientes)."""
    return {
        "embedding": vector_literal(consulta.embedding),
        "capas": None if consulta.capas is None else [c.value for c in consulta.capas],
        "modulo": consulta.modulo,
        "k": consulta.k,
        "incluir_sin_curar": consulta.incluir_sin_curar,
    }


def construir_respuesta(
    consulta: BusquedaHibridaIn, filas: Iterable[Mapping[str, Any]]
) -> BusquedaHibridaOut:
    """Convierte las filas (ya ordenadas por la función SQL) en la respuesta del contrato."""
    resultados = [
        ResultadoBusqueda(
            capa=CapaBusqueda(str(f["capa"])),
            id=str(f["id"]),
            referencia=str(f["referencia"]),
            modulo=None if f["modulo"] is None else str(f["modulo"]),
            titulo=str(f["titulo"] or ""),
            contenido=str(f["contenido"] or ""),
            estado_curacion=f["estado_curacion"],
            similitud=round(min(max(float(f["similitud"]), 0.0), 1.0), 6),
        )
        for f in filas
    ]
    return BusquedaHibridaOut(resultados=resultados, incluye_sin_curar=consulta.incluir_sin_curar)


async def buscar_hibrido(db: AsyncSession, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut:
    """Búsqueda híbrida con una sesión confinada al tenant (`db.sesion_tenant`)."""
    resultado = await db.execute(text(SQL_BUSQUEDA_HIBRIDA), parametros_sql(consulta))
    return construir_respuesta(consulta, resultado.mappings().all())
