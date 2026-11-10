"""Contrato cm-consulta-v1: resolución de {{nuggets}} para las plantillas del pipeline (KC-CM-08-3).

Las plantillas de diseño, build y corrección rellenan tres variables con las reglas aprobadas que
aplican al área que se va a tocar:

    {{nuggets}}             reglas de los nodos/ficheros pedidos y, al final, las generales
                            (aprobadas sin ningún vínculo con el grafo).
    {{module_conventions}}  restricciones técnicas de los ficheros bajo el prefijo del módulo.
    {{nuggets_for_file}}    reglas del fichero y de sus símbolos.

Este módulo es la única definición del contrato (entrada, salida, orden y recorte). Lo usan
Backend (`resolver_nuggets`, SQLAlchemy async) y CodeMapping
(`kallicode_codemapping.cm_queries`, psycopg síncrono). El JSON Schema congelado está en
`contratos/cm-consulta-v1.schema.json`; se regenera con `python -m kallicode_core.cm_consulta`.

Garantías:
- Solo nuggets `aprobado` del tenant de la sesión (`core.current_tenant()` explícito además de RLS).
- Orden determinista: relevancia (directa < alcance < general), score descendente, instante de
  aprobación e id (collation "C").
- El recorte al presupuesto entra reglas enteras en orden y se detiene en la primera que no cabe:
  nunca parte una regla y una de menor prioridad nunca desplaza a una de mayor.
- Sin reglas aplicables la respuesta es válida: texto "" y lista vacía.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VERSION_CONTRATO = "cm-consulta-v1"

# Límite de una regla: igual que cm.nugget_regla_max_chars() (041) y el extractor de KC-CM-08-2.
REGLA_MAX_CHARS = 280
# Presupuesto por defecto de una variable: 20 reglas de 280 caracteres + "- " y salto de línea.
MAX_NUGGETS_EN_CONTEXTO = 20
PRESUPUESTO_DEFECTO = MAX_NUGGETS_EN_CONTEXTO * (REGLA_MAX_CHARS + 3)
PRESUPUESTO_MAXIMO = 20_000
# Candidatos que se leen de la base antes de recortar (cota de trabajo por consulta).
MAX_CANDIDATOS = 200
MAX_REFERENCIAS = 500

RUTA_CONTRATO = Path(__file__).resolve().parents[4] / "contratos" / "cm-consulta-v1.schema.json"


class VariablePlantilla(StrEnum):
    NUGGETS = "nuggets"
    MODULE_CONVENTIONS = "module_conventions"
    NUGGETS_FOR_FILE = "nuggets_for_file"


Relevancia = Literal["directa", "alcance", "general"]
_RELEVANCIA_POR_PRIORIDAD: dict[int, Relevancia] = {1: "directa", 2: "alcance", 3: "general"}


class ConsultaNuggetsIn(BaseModel):
    """Qué variable se resuelve y para qué área del grafo. El tenant es el de la sesión."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: VariablePlantilla
    nodos: list[str] = Field(
        default_factory=list,
        max_length=MAX_REFERENCIAS,
        description="Ids de nodos del grafo (cm.nodes) del área que se va a tocar.",
    )
    ficheros: list[str] = Field(
        default_factory=list,
        max_length=MAX_REFERENCIAS,
        description="Rutas de fichero (cm.files.path); incluyen sus símbolos.",
    )
    modulo: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="Prefijo de ruta del módulo (obligatorio en module_conventions).",
    )
    presupuesto_caracteres: int = Field(default=PRESUPUESTO_DEFECTO, ge=0, le=PRESUPUESTO_MAXIMO)

    @model_validator(mode="after")
    def _area_coherente(self) -> ConsultaNuggetsIn:
        if self.variable is VariablePlantilla.MODULE_CONVENTIONS and not self.modulo:
            raise ValueError("module_conventions exige 'modulo'")
        if self.variable is VariablePlantilla.NUGGETS_FOR_FILE and len(self.ficheros) != 1:
            raise ValueError("nuggets_for_file exige exactamente un fichero")
        return self


class NuggetAplicable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    regla: str = Field(min_length=1, max_length=REGLA_MAX_CHARS)
    tipo: Literal["regla_negocio", "restriccion_tecnica"]
    seccion: str
    evidencia_ref: str
    relevancia: Relevancia
    score: float = Field(ge=0, le=1)


class ConsultaNuggetsOut(BaseModel):
    """Bloque listo para la plantilla y las reglas que lo forman, en orden de entrega."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-consulta-v1"] = VERSION_CONTRATO
    variable: VariablePlantilla
    texto: str = Field(description='Una línea "- <regla>" por nugget; "" si no hay reglas.')
    nuggets: list[NuggetAplicable] = Field(description="Los nuggets incluidos en `texto`.")
    omitidos: int = Field(ge=0, description="Candidatos que no cupieron en el presupuesto.")
    presupuesto_caracteres: int = Field(ge=0)
    caracteres_usados: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Recorte al presupuesto
# ---------------------------------------------------------------------------
_ESPACIOS = re.compile(r"\s+")


def normalizar_regla(regla: str) -> str:
    return _ESPACIOS.sub(" ", regla).strip()


def seleccionar_para_presupuesto(
    reglas: Sequence[str],
    presupuesto: int = PRESUPUESTO_DEFECTO,
    max_reglas: int = MAX_NUGGETS_EN_CONTEXTO,
) -> list[int]:
    """Índices de las reglas que entran enteras, en orden, en un bloque de `presupuesto` caracteres.

    Cada regla ocupa "- <regla>" más el salto de línea que la separa de la anterior. Las vacías o
    de más de REGLA_MAX_CHARS se saltan (la base ya no las admite); en la primera que no cabe se
    detiene, para que una regla de menor prioridad nunca desplace a una de mayor.
    """
    elegidas: list[int] = []
    usado = 0
    for i, regla in enumerate(reglas):
        limpia = normalizar_regla(regla)
        if not limpia or len(limpia) > REGLA_MAX_CHARS:
            continue
        coste = len(limpia) + 2 + (1 if elegidas else 0)
        if len(elegidas) >= max_reglas or usado + coste > presupuesto:
            break
        elegidas.append(i)
        usado += coste
    return elegidas


def renderizar(reglas: Sequence[str]) -> str:
    return "\n".join(f"- {normalizar_regla(r)}" for r in reglas)


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
# Copia con parámetros psycopg en kallicode_codemapping.cm_queries.SQL_NUGGETS_APLICABLES: un test
# comprueba que ambas son la misma consulta. `core.current_tenant()` se filtra explícitamente en
# cada tabla, además de RLS: sin tenant en la sesión no devuelve nada.
SQL_NUGGETS_APLICABLES = """
WITH ficheros_objetivo AS (
    SELECT f.id
      FROM cm.files f
     WHERE f.tenant_id = core.current_tenant()
       AND (f.path = ANY(CAST(:ficheros AS text[]))
            OR (CAST(:prefijo AS text) IS NOT NULL
                AND f.path LIKE CAST(:prefijo AS text) ESCAPE '!'))
),
objetivo AS (
    SELECT n.id
      FROM cm.nodes n
     WHERE n.tenant_id = core.current_tenant() AND n.id = ANY(CAST(:nodos AS text[]))
    UNION
    SELECT fo.id FROM ficheros_objetivo fo
    UNION
    SELECT s.id
      FROM cm.symbols s
      JOIN ficheros_objetivo fo ON fo.id = s.file_id
     WHERE s.tenant_id = core.current_tenant()
),
nombres AS (
    SELECT lower(f.path) AS nombre
      FROM cm.files f JOIN objetivo o ON o.id = f.id
     WHERE f.tenant_id = core.current_tenant()
    UNION
    SELECT lower(s.name)
      FROM cm.symbols s JOIN objetivo o ON o.id = s.id
     WHERE s.tenant_id = core.current_tenant()
    UNION
    SELECT lower(t.nombre)
      FROM cm.db_tables t JOIN objetivo o ON o.id = t.id
     WHERE t.tenant_id = core.current_tenant()
    UNION
    SELECT lower(t.esquema || '.' || t.nombre)
      FROM cm.db_tables t JOIN objetivo o ON o.id = t.id
     WHERE t.tenant_id = core.current_tenant()
),
directos AS (
    SELECT l.chunk_id, max(l.score) AS score
      FROM cm.doc_chunk_links l
      JOIN objetivo o ON o.id = l.nodo_ref
     WHERE l.tenant_id = core.current_tenant()
     GROUP BY l.chunk_id
),
candidatos AS (
    SELECT n.id, n.regla, n.tipo, n.seccion, n.evidencia_ref, n.resuelto_en,
           CASE
               WHEN d.chunk_id IS NOT NULL THEN 1
               WHEN jsonb_typeof(n.scope_refs) = 'array' AND EXISTS (
                        SELECT 1
                          FROM jsonb_array_elements_text(n.scope_refs) AS r(ref)
                          JOIN nombres nm ON nm.nombre = lower(r.ref)) THEN 2
               -- General: sin ningún vínculo con el grafo (ni enlaces de su evidencia ni
               -- scope_refs). Un nugget vinculado a OTRA área no aplica a esta (4).
               WHEN COALESCE(jsonb_array_length(
                        CASE WHEN jsonb_typeof(n.scope_refs) = 'array' THEN n.scope_refs END),
                        0) = 0
                    AND NOT EXISTS (
                        SELECT 1
                          FROM cm.doc_chunk_links l2
                         WHERE l2.tenant_id = core.current_tenant()
                           AND l2.chunk_id = n.evidencia_ref) THEN 3
               ELSE 4
           END AS prioridad,
           COALESCE(d.score, 0) AS score
      FROM cm.nuggets n
      LEFT JOIN directos d ON d.chunk_id = n.evidencia_ref
     WHERE n.tenant_id = core.current_tenant()
       AND n.estado = 'aprobado'
       AND (CAST(:tipos AS text[]) IS NULL OR n.tipo = ANY(CAST(:tipos AS text[])))
)
SELECT id, regla, tipo, seccion, evidencia_ref, prioridad, score
  FROM candidatos
 WHERE prioridad < 3 OR (prioridad = 3 AND CAST(:incluir_generales AS boolean))
 ORDER BY prioridad, score DESC, resuelto_en, id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""


def _escapar_like(texto: str) -> str:
    return texto.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def parametros_sql(consulta: ConsultaNuggetsIn) -> dict[str, Any]:
    """Parámetros de SQL_NUGGETS_APLICABLES para una consulta (iguales en ambos clientes)."""
    variable = consulta.variable
    prefijo: str | None = None
    if variable is VariablePlantilla.MODULE_CONVENTIONS and consulta.modulo:
        prefijo = _escapar_like(consulta.modulo.rstrip("/") + "/") + "%"
    return {
        "nodos": [] if variable is VariablePlantilla.MODULE_CONVENTIONS else list(consulta.nodos),
        "ficheros": (
            [] if variable is VariablePlantilla.MODULE_CONVENTIONS else list(consulta.ficheros)
        ),
        "prefijo": prefijo,
        "tipos": (
            ["restriccion_tecnica"] if variable is VariablePlantilla.MODULE_CONVENTIONS else None
        ),
        "incluir_generales": variable is VariablePlantilla.NUGGETS,
        "limite": MAX_CANDIDATOS,
    }


def construir_respuesta(
    consulta: ConsultaNuggetsIn, filas: Iterable[Mapping[str, Any]]
) -> ConsultaNuggetsOut:
    """Convierte las filas ordenadas de la consulta en la respuesta recortada al presupuesto."""
    candidatos = [
        NuggetAplicable(
            id=str(f["id"]),
            regla=normalizar_regla(str(f["regla"])),
            tipo=f["tipo"],
            seccion=str(f["seccion"]),
            evidencia_ref=str(f["evidencia_ref"]),
            relevancia=_RELEVANCIA_POR_PRIORIDAD[int(f["prioridad"])],
            score=round(min(max(float(f["score"]), 0.0), 1.0), 6),
        )
        for f in filas
    ]
    indices = seleccionar_para_presupuesto(
        [c.regla for c in candidatos], consulta.presupuesto_caracteres
    )
    incluidos = [candidatos[i] for i in indices]
    texto = renderizar([n.regla for n in incluidos])
    return ConsultaNuggetsOut(
        variable=consulta.variable,
        texto=texto,
        nuggets=incluidos,
        omitidos=len(candidatos) - len(incluidos),
        presupuesto_caracteres=consulta.presupuesto_caracteres,
        caracteres_usados=len(texto),
    )


async def resolver_nuggets(db: AsyncSession, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut:
    """Resuelve una variable con una sesión confinada al tenant (`db.sesion_tenant`)."""
    resultado = await db.execute(text(SQL_NUGGETS_APLICABLES), parametros_sql(consulta))
    return construir_respuesta(consulta, resultado.mappings().all())


# ---------------------------------------------------------------------------
# Contrato congelado
# ---------------------------------------------------------------------------
def generar_contrato() -> dict[str, Any]:
    """JSON Schema del contrato cm-consulta-v1 a partir de los modelos (fuente única)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:kallicode:contrato:cm-consulta:v1",
        "title": "Resolución de {{nuggets}} para las plantillas del pipeline",
        "description": (
            "Contrato congelado v1.0.0 entre CodeMapping y Backend (KC-CM-08-3). `entrada` es la "
            "consulta y `salida` la respuesta. Solo nuggets aprobados del tenant de la sesión; "
            "orden determinista (relevancia directa < alcance < general, score descendente, "
            "instante de aprobación, id); recorte por reglas enteras. Cambios compatibles suben "
            "la versión menor; quitar o renombrar un campo exige v2."
        ),
        "x-version": "1.0.0",
        "x-orden": ["relevancia", "score desc", "resuelto_en", "id (collation C)"],
        "x-presupuesto": {
            "regla_max_caracteres": REGLA_MAX_CHARS,
            "max_nuggets": MAX_NUGGETS_EN_CONTEXTO,
            "presupuesto_por_defecto": PRESUPUESTO_DEFECTO,
            "formato_linea": "- <regla>",
        },
        "entrada": ConsultaNuggetsIn.model_json_schema(),
        "salida": ConsultaNuggetsOut.model_json_schema(),
    }


def serializar_contrato() -> str:
    return json.dumps(generar_contrato(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":  # pragma: no cover - regeneración manual del contrato
    RUTA_CONTRATO.write_text(serializar_contrato(), encoding="utf-8", newline="\n")
    sys.stdout.write(f"contrato escrito en {RUTA_CONTRATO}\n")
