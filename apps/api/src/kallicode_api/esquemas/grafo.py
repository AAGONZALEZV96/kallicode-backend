"""Esquemas Pydantic v2 para el contrato interno con CodeMapping (KC-BE-09-6)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from kallicode_core.cm_escalacion import EscalacionIngestaIn, EscalacionIngestaOut
from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION_CONTRATO_INGESTA_GRAFO = "1.0.0"

# Límites máximos declarados por lote en el contrato interno
MAX_NODOS_POR_LOTE = 500
MAX_ARISTAS_POR_LOTE = 1_000
MAX_EMBEDDINGS_POR_LOTE = 250
DIMENSION_EMBEDDING = 1024

KindNodo = Literal["file", "symbol", "db_table", "db_column", "definition", "concept"]
TipoArista = Literal[
    "DEFINES",
    "CALLS",
    "HAS_COLUMN",
    "REFERENCES",
    "USES_TABLE",
    "REALIZED_BY",
    "RELATES",
    "EXPRESSES",
]
KindSimbolo = Literal[
    "function",
    "method",
    "class",
    "interface",
    "enum",
    "type",
    "view",
    "procedure",
]
RolSimbolo = Literal["domain", "infra", "glue"]


class _NodoBaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    tenant_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Opcional; si se informa debe coincidir con el tenant del token.",
    )


class NodoFileIn(_NodoBaseIn):
    kind: Literal["file"] = "file"
    path: str = Field(min_length=1, max_length=1024)
    language: str = Field(min_length=1, max_length=64)
    sha256: str = Field(min_length=8, max_length=128)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


class NodoSymbolIn(_NodoBaseIn):
    kind: Literal["symbol"] = "symbol"
    file_id: str = Field(min_length=1, max_length=256)
    symbol_kind: KindSimbolo = Field(
        default="function",
        description="Subtipo de símbolo AST (function, method, class, interface, enum, type, view, procedure).",
    )
    name: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    signature: str = Field(min_length=1, max_length=4096)
    descripcion: str | None = Field(default=None, max_length=4096)
    role: RolSimbolo | None = None

    @model_validator(mode="after")
    def _validar_lineas(self) -> NodoSymbolIn:
        if self.end_line < self.start_line:
            raise ValueError("end_line debe ser mayor o igual que start_line.")
        return self


class NodoDbTableIn(_NodoBaseIn):
    kind: Literal["db_table"] = "db_table"
    esquema: str = Field(min_length=1, max_length=128)
    nombre: str = Field(min_length=1, max_length=256)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


class NodoDbColumnIn(_NodoBaseIn):
    kind: Literal["db_column"] = "db_column"
    table_id: str = Field(min_length=1, max_length=256)
    nombre: str = Field(min_length=1, max_length=256)
    tipo: str = Field(min_length=1, max_length=128)
    nullable: bool


class NodoDefinitionIn(_NodoBaseIn):
    kind: Literal["definition"] = "definition"
    modulo: str = Field(min_length=1, max_length=256)
    nombre: str = Field(min_length=1, max_length=256)
    definition_kind: str = Field(
        default="definition",
        min_length=1,
        max_length=64,
        description="Clasificación semántica de la definición.",
    )
    descripcion: str = Field(min_length=1, max_length=4096)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


class NodoConceptIn(_NodoBaseIn):
    kind: Literal["concept"] = "concept"
    name_en: str = Field(min_length=1, max_length=256)
    name_local: str = Field(min_length=1, max_length=256)
    descripcion: str = Field(min_length=1, max_length=4096)
    confianza: float | None = Field(default=None, ge=0.0, le=1.0)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


GrafoNodoIn = Annotated[
    NodoFileIn | NodoSymbolIn | NodoDbTableIn | NodoDbColumnIn | NodoDefinitionIn | NodoConceptIn,
    Field(discriminator="kind"),
]


class GrafoAristaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    tipo: TipoArista
    src_kind: KindNodo
    src_id: str = Field(min_length=1, max_length=256)
    dst_kind: KindNodo
    dst_id: str = Field(min_length=1, max_length=256)
    props: dict[str, Any] = Field(default_factory=dict)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)


class GrafoEmbeddingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(default=None, min_length=1, max_length=128)
    nodo_ref: str = Field(min_length=1, max_length=256)
    capa: KindNodo
    nombre: str = Field(min_length=1, max_length=512)
    descripcion: str = Field(min_length=1, max_length=4096)
    modulo: str | None = Field(default=None, max_length=256)
    embedding: list[float] = Field(
        min_length=DIMENSION_EMBEDDING,
        max_length=DIMENSION_EMBEDDING,
        description="Vector denso de 1024 dimensiones (bge-m3).",
    )
    texto_hash: str = Field(min_length=8, max_length=128)


class GrafoLoteIn(BaseModel):
    """Escritura convergente por lotes de nodos, aristas y embeddings del grafo."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Debe coincidir con el tenant del token si se especifica.",
    )
    snapshot_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Snapshot por defecto aplicado a nodos y aristas que no indiquen uno propio.",
    )
    nodos: list[GrafoNodoIn] = Field(default_factory=list, max_length=MAX_NODOS_POR_LOTE)
    aristas: list[GrafoAristaIn] = Field(default_factory=list, max_length=MAX_ARISTAS_POR_LOTE)
    embeddings: list[GrafoEmbeddingIn] = Field(
        default_factory=list,
        max_length=MAX_EMBEDDINGS_POR_LOTE,
    )

    @model_validator(mode="after")
    def _validar_lote_no_vacio(self) -> GrafoLoteIn:
        if not self.nodos and not self.aristas and not self.embeddings:
            raise ValueError("El lote debe incluir al menos un nodo, arista o embedding.")
        return self


class LimitesLoteGrafoOut(BaseModel):
    max_nodos: int = MAX_NODOS_POR_LOTE
    max_aristas: int = MAX_ARISTAS_POR_LOTE
    max_embeddings: int = MAX_EMBEDDINGS_POR_LOTE
    dimension_embedding: int = DIMENSION_EMBEDDING


class GrafoLoteOut(BaseModel):
    """Resultado idempotente de escritura por lotes del grafo CodeMapping."""

    version_contrato: str = VERSION_CONTRATO_INGESTA_GRAFO
    tenant_id: str
    snapshot_id: str | None = None
    nodos_procesados: int = Field(ge=0)
    aristas_procesadas: int = Field(ge=0)
    embeddings_procesados: int = Field(ge=0)
    embeddings_actualizados: int = Field(ge=0)
    limites_lote: LimitesLoteGrafoOut = Field(default_factory=LimitesLoteGrafoOut)


class FaseIngestaOut(BaseModel):
    numero: int = Field(ge=0, le=5)
    nombre: str
    checkpoint: str | None = None


class ProgresoIngestaOut(BaseModel):
    porcentaje: int = Field(ge=0, le=100)
    fases_totales: int = 5
    nodos: int = Field(ge=0)
    aristas: int = Field(ge=0)
    embeddings: int = Field(ge=0)


class CoberturaGrafoOut(BaseModel):
    nodos_totales: int = Field(ge=0)
    nodos_con_embedding: int = Field(ge=0)
    porcentaje_embeddings: float = Field(ge=0.0, le=100.0)
    por_kind: dict[str, int] = Field(default_factory=dict)


class CuracionPendienteOut(BaseModel):
    total: int = Field(ge=0)
    por_tipo: dict[str, int] = Field(default_factory=dict)


class IngestaGrafoStatusOut(BaseModel):
    """Estado consolidado de ingesta del grafo CodeMapping por tenant."""

    version_contrato: str = VERSION_CONTRATO_INGESTA_GRAFO
    tenant_id: str
    snapshot_id: str | None = None
    commit_sha: str | None = None
    rama: str | None = None
    estado: str
    fase: FaseIngestaOut
    progreso: ProgresoIngestaOut
    cobertura: CoberturaGrafoOut
    curacion_pendiente: CuracionPendienteOut
    ultimo_refresco: str | None = None
    escalacion_activa: dict[str, Any] | None = None
    iniciado_en: str | None = None
    finalizado_en: str | None = None


__all__ = [
    "DIMENSION_EMBEDDING",
    "MAX_ARISTAS_POR_LOTE",
    "MAX_EMBEDDINGS_POR_LOTE",
    "MAX_NODOS_POR_LOTE",
    "VERSION_CONTRATO_INGESTA_GRAFO",
    "CoberturaGrafoOut",
    "CuracionPendienteOut",
    "EscalacionIngestaIn",
    "EscalacionIngestaOut",
    "FaseIngestaOut",
    "GrafoAristaIn",
    "GrafoEmbeddingIn",
    "GrafoLoteIn",
    "GrafoLoteOut",
    "GrafoNodoIn",
    "IngestaGrafoStatusOut",
    "KindNodo",
    "KindSimbolo",
    "LimitesLoteGrafoOut",
    "NodoConceptIn",
    "NodoDbColumnIn",
    "NodoDbTableIn",
    "NodoDefinitionIn",
    "NodoFileIn",
    "NodoSymbolIn",
    "ProgresoIngestaOut",
    "RolSimbolo",
    "TipoArista",
]
