"""Propuesta de cm.nuggets desde la documentación procesada (KC-CM-08-2).

Un nugget es una regla corta (de negocio o restricción técnica) que las plantillas del pipeline
consumen vía `{{nuggets}}`. Este módulo las PROPONE; nunca las aprueba:

1. Lee los chunks de los documentos ya procesados (vec.doc_chunks, KC-CM-08-1).
2. Los envía en lotes al paso LLM `codemapping.extract_nuggets`, delimitados como contenido no
   confiable. El cliente canónico valida la respuesta contra su JSON Schema estricto.
3. Descarta automáticamente lo que no se sostiene: nugget sin evidencia, evidencia que cita un chunk
   ajeno al lote (inventada) o una cita que no aparece en el chunk, regla vacía o con una frase de
   inyección, y duplicados. El límite de longitud lo impone el esquema (y la base).
4. Los supervivientes se guardan en cm.nuggets en estado 'propuesto' con su evidencia (id del chunk,
   sección de origen y cita literal). La base abre su revisión pendiente en cm.curation_queue y no
   deja que ninguno llegue a 'aprobado' sin una resolución humana registrada
   (db/migrations/041_nuggets_curacion.sql).
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import jsonschema
from kallicode_core import cm_consulta
from kallicode_core.llm.cliente import LLMEscaladoHumano
from kallicode_core.llm.plantillas import registrar_plantilla
from kallicode_core.llm.tiers import Tier

from .llm_enrich import _ejecutar_paso_sync
from .security import (
    INSTRUCCION_DOCS_NO_CONFIABLES,
    MAX_EXTRACTO_DOC,
    contiene_inyeccion,
    encapsular_contenido_no_confiable,
    generar_delimitador,
    sanear_texto,
)

logger = logging.getLogger(__name__)

PASO_EXTRACT_NUGGETS = "codemapping.extract_nuggets"

# Límite declarado de una regla (contrato cm-consulta-v1, KC-CM-08-3). Debe coincidir con
# cm.nugget_regla_max_chars() (041): la base rechaza lo que lo supere y un test compara ambos.
REGLA_MAX_CHARS = cm_consulta.REGLA_MAX_CHARS
CITA_MAX_CHARS = 300
SECCION_MAX_CHARS = 300
MAX_SCOPE_REFS = 10
SCOPE_REF_MAX_CHARS = 120

# Presupuesto de contexto de {{nuggets}} en las plantillas del pipeline: como mucho
# MAX_NUGGETS_EN_CONTEXTO reglas de REGLA_MAX_CHARS, más el "- " y el salto de línea de cada una.
MAX_NUGGETS_EN_CONTEXTO = cm_consulta.MAX_NUGGETS_EN_CONTEXTO
PRESUPUESTO_CONTEXTO_NUGGETS = cm_consulta.PRESUPUESTO_DEFECTO

# Tope de nuggets por respuesta del modelo (la salida del paso también tiene presupuesto).
MAX_NUGGETS_POR_LOTE = 20

TIPOS_NUGGET = ("regla_negocio", "restriccion_tecnica")

# Motivos de descarte: códigos estables para logs y métricas (no se muestran al usuario).
SIN_EVIDENCIA = "SIN_EVIDENCIA"
EVIDENCIA_NO_RESOLUBLE = "EVIDENCIA_NO_RESOLUBLE"
CITA_NO_ENCONTRADA = "CITA_NO_ENCONTRADA"
REGLA_VACIA = "REGLA_VACIA"
POSIBLE_INYECCION = "POSIBLE_INYECCION"
DUPLICADO = "DUPLICADO"
ESQUEMA_INVALIDO = "ESQUEMA_INVALIDO"
LLM_ESCALADO = "LLM_ESCALADO"

ESQUEMA_SALIDA_NUGGETS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nuggets"],
    "properties": {
        "nuggets": {
            "type": "array",
            "maxItems": MAX_NUGGETS_POR_LOTE,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule", "kind", "evidence", "scope_refs"],
                "properties": {
                    "rule": {"type": "string", "minLength": 1, "maxLength": REGLA_MAX_CHARS},
                    "kind": {"type": "string", "enum": list(TIPOS_NUGGET)},
                    # null está permitido a propósito: un nugget sin evidencia es una respuesta
                    # válida del modelo que el paso descarta, no un error de esquema que reintentar.
                    "evidence": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["chunk_id", "quote"],
                                "properties": {
                                    "chunk_id": {"type": "string", "minLength": 1},
                                    "quote": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": CITA_MAX_CHARS,
                                    },
                                },
                            },
                        ]
                    },
                    "scope_refs": {
                        "type": "array",
                        "maxItems": MAX_SCOPE_REFS,
                        "items": {"type": "string", "maxLength": SCOPE_REF_MAX_CHARS},
                    },
                },
            },
        }
    },
}

EXTRACT_NUGGETS_SYSTEM = (
    "You extract business rules and technical constraints from excerpts of a customer's "
    "documentation (manuals, specifications, architecture decision records, business documents). "
    "A rule is a short, self-contained, verifiable statement that a developer changing this system "
    "must respect (e.g. 'An invoice cannot be cancelled after it is paid', 'Amounts are stored in "
    "cents as integers'). Write each rule in the language of the excerpt, at most "
    f"{REGLA_MAX_CHARS} characters. Classify it as 'regla_negocio' (business rule) or "
    "'restriccion_tecnica' (technical constraint). Every rule MUST cite its evidence: the chunk_id "
    "of the excerpt it comes from and a literal quote copied verbatim from that excerpt (at most "
    f"{CITA_MAX_CHARS} characters). If you cannot point to such evidence, set evidence to null. "
    "Do not invent rules, do not merge rules from different excerpts, and return an empty list when "
    "the excerpts state no rule. scope_refs lists code identifiers or tables the rule names "
    f"explicitly (may be empty). Return at most {MAX_NUGGETS_POR_LOTE} rules. Return ONLY JSON: "
    '{"nuggets": [{"rule", "kind", "evidence": {"chunk_id", "quote"} | null, "scope_refs": []}]}'
    + INSTRUCCION_DOCS_NO_CONFIABLES
)

registrar_plantilla(
    PASO_EXTRACT_NUGGETS,
    tier=Tier.FLASH,
    escalable=True,
    descripcion="Propone reglas de negocio y restricciones técnicas citando el chunk de "
    "documentación que las respalda. Las propuestas quedan pendientes de curación humana.",
    entrada={
        "delimiter": "nonce del lote",
        "max_rule_chars": REGLA_MAX_CHARS,
        "chunks": "list[{chunk_id, section, excerpt}] con section/excerpt delimitados",
    },
    salida=ESQUEMA_SALIDA_NUGGETS,
    system=EXTRACT_NUGGETS_SYSTEM,
)


@dataclass(frozen=True)
class ChunkFuente:
    """Chunk de documentación procesada del que se pueden extraer nuggets."""

    chunk_id: str
    documento_id: str
    seccion: str
    contenido: str


@dataclass(frozen=True)
class NuggetPropuesto:
    """Nugget que superó los filtros: se guarda en cm.nuggets en estado 'propuesto'."""

    id: str
    tenant_id: str
    regla: str
    tipo: str
    evidencia_ref: str
    documento_id: str
    seccion: str
    cita: str
    scope_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class NuggetDescartado:
    """Propuesta del modelo que el paso descartó automáticamente, con su motivo."""

    motivo: str
    regla: str = ""
    chunk_id: str = ""


@dataclass
class ResultadoExtraccion:
    propuestos: list[NuggetPropuesto] = field(default_factory=list)
    descartados: list[NuggetDescartado] = field(default_factory=list)

    def extender(self, otro: ResultadoExtraccion) -> None:
        self.propuestos.extend(otro.propuestos)
        self.descartados.extend(otro.descartados)


class RepositorioNuggets(Protocol):
    """Lo que el extractor necesita de la persistencia (lo implementa GraphLoader)."""

    def obtener_chunks_para_nuggets(
        self, documento_ids: Sequence[str] | None = None
    ) -> list[ChunkFuente]: ...

    def guardar_nuggets_propuestos(self, nuggets: Sequence[NuggetPropuesto]) -> int: ...


EjecutorLLM = Callable[[str, dict[str, Any]], dict[str, Any]]

_ESPACIOS = re.compile(r"\s+")


def normalizar_para_comparar(texto: str) -> str:
    """Forma canónica para comparar textos: NFKC, minúsculas y espacios colapsados."""
    return _ESPACIOS.sub(" ", unicodedata.normalize("NFKC", texto)).strip().casefold()


def id_nugget(tenant_id: str, chunk_id: str, regla: str) -> str:
    """Id determinista: la misma regla del mismo chunk es el mismo nugget (reprocesar no duplica)."""
    clave = f"{tenant_id}:{chunk_id}:{normalizar_para_comparar(regla)}"
    return f"ng_{hashlib.sha256(clave.encode('utf-8')).hexdigest()[:24]}"


def construir_payload_lote(chunks: Sequence[ChunkFuente], delimitador: str) -> dict[str, Any]:
    """Payload del paso: cada sección y extracto, saneado y encerrado con el nonce del lote."""
    return {
        "delimiter": delimitador,
        "max_rule_chars": REGLA_MAX_CHARS,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "section": encapsular_contenido_no_confiable(
                    sanear_texto(c.seccion, SECCION_MAX_CHARS), delimitador
                )[0],
                "excerpt": encapsular_contenido_no_confiable(
                    sanear_texto(c.contenido, MAX_EXTRACTO_DOC), delimitador
                )[0],
            }
            for c in chunks
        ],
    }


def validar_salida_nuggets(
    respuesta: object,
    chunks_lote: Sequence[ChunkFuente],
    tenant_id: str,
) -> ResultadoExtraccion:
    """Valida la salida del paso contra su esquema estricto y filtra lo que no tiene evidencia.

    El cliente canónico ya validó el esquema; se vuelve a validar aquí porque esta función es la
    frontera de confianza del módulo (también se usa con ejecutores inyectados).
    """
    resultado = ResultadoExtraccion()
    try:
        jsonschema.validate(respuesta, ESQUEMA_SALIDA_NUGGETS)
    except jsonschema.ValidationError as e:
        resultado.descartados.append(NuggetDescartado(ESQUEMA_INVALIDO, regla=str(e.message)[:80]))
        return resultado
    salida = cast(dict[str, Any], respuesta)

    por_id = {c.chunk_id: c for c in chunks_lote}
    vistos: set[str] = set()
    for crudo in salida["nuggets"]:
        regla = sanear_texto(crudo["rule"], REGLA_MAX_CHARS)
        evidencia = crudo["evidence"]
        etiqueta = regla[:80]
        if evidencia is None:
            resultado.descartados.append(NuggetDescartado(SIN_EVIDENCIA, regla=etiqueta))
            continue
        chunk = por_id.get(evidencia["chunk_id"])
        if chunk is None:
            resultado.descartados.append(
                NuggetDescartado(EVIDENCIA_NO_RESOLUBLE, etiqueta, str(evidencia["chunk_id"])[:80])
            )
            continue
        cita = sanear_texto(evidencia["quote"], CITA_MAX_CHARS)
        motivo = _motivo_rechazo(regla, cita, chunk)
        if motivo is not None:
            resultado.descartados.append(NuggetDescartado(motivo, etiqueta, chunk.chunk_id))
            continue
        nugget_id = id_nugget(tenant_id, chunk.chunk_id, regla)
        if nugget_id in vistos:
            resultado.descartados.append(NuggetDescartado(DUPLICADO, etiqueta, chunk.chunk_id))
            continue
        vistos.add(nugget_id)
        resultado.propuestos.append(
            NuggetPropuesto(
                id=nugget_id,
                tenant_id=tenant_id,
                regla=regla,
                tipo=crudo["kind"],
                evidencia_ref=chunk.chunk_id,
                documento_id=chunk.documento_id,
                seccion=sanear_texto(chunk.seccion, SECCION_MAX_CHARS),
                cita=cita,
                scope_refs=_scope_refs(crudo["scope_refs"]),
            )
        )
    return resultado


def _motivo_rechazo(regla: str, cita: str, chunk: ChunkFuente) -> str | None:
    # La longitud (<= REGLA_MAX_CHARS) ya la garantiza el esquema, y sanear solo acorta.
    if not regla:
        return REGLA_VACIA
    if contiene_inyeccion(regla) or contiene_inyeccion(cita):
        return POSIBLE_INYECCION
    # La cita debe estar literalmente en el chunk que dice respaldarla: si no, la evidencia no
    # resuelve a nada que un curador pueda comprobar.
    if not cita or normalizar_para_comparar(cita) not in normalizar_para_comparar(chunk.contenido):
        return CITA_NO_ENCONTRADA
    return None


def _scope_refs(crudos: Sequence[str]) -> tuple[str, ...]:
    refs = (sanear_texto(r, SCOPE_REF_MAX_CHARS) for r in crudos)
    return tuple(dict.fromkeys(r for r in refs if r))


def agrupar_en_lotes(
    chunks: Sequence[ChunkFuente], max_chunks: int, max_caracteres: int
) -> list[list[ChunkFuente]]:
    """Lotes de como mucho `max_chunks` chunks y `max_caracteres` de contenido (al menos 1 chunk)."""
    lotes: list[list[ChunkFuente]] = []
    actual: list[ChunkFuente] = []
    caracteres = 0
    for chunk in chunks:
        tamano = min(len(chunk.contenido), MAX_EXTRACTO_DOC)
        if actual and (len(actual) >= max_chunks or caracteres + tamano > max_caracteres):
            lotes.append(actual)
            actual, caracteres = [], 0
        actual.append(chunk)
        caracteres += tamano
    if actual:
        lotes.append(actual)
    return lotes


def _ejecutor_canonico(tenant_id: str) -> EjecutorLLM:
    def ejecutar(paso: str, variables: dict[str, Any]) -> dict[str, Any]:
        return _ejecutar_paso_sync(
            nombre_paso=paso, tier_inicial=Tier.FLASH, variables=variables, tenant_id=tenant_id
        )

    return ejecutar


def extraer_nuggets(
    chunks: Sequence[ChunkFuente],
    tenant_id: str,
    ejecutar_llm: EjecutorLLM | None = None,
    max_chunks_por_lote: int = 8,
    max_caracteres_por_lote: int = 12000,
) -> ResultadoExtraccion:
    """Extrae nuggets de los chunks, lote a lote. Un lote que el LLM no resuelve no frena al resto."""
    ejecutar = ejecutar_llm or _ejecutor_canonico(tenant_id)
    resultado = ResultadoExtraccion()
    vistos: set[str] = set()
    for lote in agrupar_en_lotes(chunks, max_chunks_por_lote, max_caracteres_por_lote):
        payload = construir_payload_lote(lote, generar_delimitador())
        try:
            respuesta = ejecutar(PASO_EXTRACT_NUGGETS, payload)
        except LLMEscaladoHumano as e:
            logger.warning(
                "nuggets: lote de %d chunk(s) sin respuesta válida del LLM (%s)",
                len(lote),
                e.motivo,
            )
            resultado.descartados.append(NuggetDescartado(LLM_ESCALADO, chunk_id=lote[0].chunk_id))
            continue
        parcial = validar_salida_nuggets(respuesta, lote, tenant_id)
        for nugget in parcial.propuestos:
            if nugget.id in vistos:
                parcial.descartados.append(
                    NuggetDescartado(DUPLICADO, nugget.regla[:80], nugget.evidencia_ref)
                )
            else:
                vistos.add(nugget.id)
                resultado.propuestos.append(nugget)
        resultado.descartados.extend(parcial.descartados)
    return resultado


def proponer_nuggets(
    repo: RepositorioNuggets,
    tenant_id: str,
    documento_ids: Sequence[str] | None = None,
    ejecutar_llm: EjecutorLLM | None = None,
    max_chunks_por_lote: int = 8,
    max_caracteres_por_lote: int = 12000,
) -> tuple[ResultadoExtraccion, int]:
    """Extrae y persiste como 'propuesto'. Devuelve (resultado, nuggets nuevos guardados)."""
    chunks = repo.obtener_chunks_para_nuggets(documento_ids)
    resultado = extraer_nuggets(
        chunks,
        tenant_id,
        ejecutar_llm=ejecutar_llm,
        max_chunks_por_lote=max_chunks_por_lote,
        max_caracteres_por_lote=max_caracteres_por_lote,
    )
    guardados = repo.guardar_nuggets_propuestos(resultado.propuestos) if resultado.propuestos else 0
    motivos: dict[str, int] = {}
    for d in resultado.descartados:
        motivos[d.motivo] = motivos.get(d.motivo, 0) + 1
    logger.info(
        "nuggets: %d propuestos (%d nuevos), %d descartados",
        len(resultado.propuestos),
        guardados,
        len(resultado.descartados),
        extra={"tenant_id": tenant_id, "nuggets_descartados_por_motivo": motivos},
    )
    return resultado, guardados


def renderizar_nuggets_para_contexto(
    reglas: Sequence[str], presupuesto: int = PRESUPUESTO_CONTEXTO_NUGGETS
) -> str:
    """Bloque `{{nuggets}}` de las reglas, dentro del presupuesto de contexto.

    Delegado en el recorte del contrato cm-consulta-v1 (kallicode_core.cm_consulta): una línea
    "- <regla>" por regla, sin partir ninguna y deteniéndose en la primera que no cabe.
    """
    elegidas = cm_consulta.seleccionar_para_presupuesto(reglas, presupuesto)
    return cm_consulta.renderizar([reglas[i] for i in elegidas])
