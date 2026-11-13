"""Contrato cm-incidentes-v1: memoria de fallos y resolución de {{incidents}} (KC-CM-09-2).

La capa de incidentes guarda, por tenant, los fallos que ya ocurrieron (causa y corrección) y los
nodos del grafo que tocaron, para que el pipeline no repita el mismo error. Las plantillas de
triage, diseño y QA rellenan {{incidents}} con los incidentes del área que se va a tocar:

    {{incidents}}   una línea por incidente: "- [KC-0012] <resumen> — cause: <causa> —
                    fix: <corrección>" (las partes ausentes se omiten).

Este módulo es la única definición del contrato: el registro (`RegistroIncidenteIn`), la consulta
(`ConsultaIncidentesIn` / `ConsultaIncidentesOut`), su SQL, el orden y el recorte. El cliente
psycopg está en `kallicode_codemapping.cm_queries` / `kallicode_codemapping.incidentes`; Backend
usa `resolver_incidentes` (SQLAlchemy async). El JSON Schema congelado está en
`contratos/cm-incidentes-v1.schema.json`; se regenera con `python -m kallicode_core.cm_incidentes`.

Garantías:
- Solo incidentes del tenant de la sesión: `core.current_tenant()` explícito en cada tabla
  (además de RLS forzada en cm.incidents, cm.incident_nodes y vec.incident_embeddings).
- Candidatos: incidentes enlazados (cm.incident_nodes) a un nodo a ≤ `saltos` (máx. 2) del área,
  recorriendo en ambos sentidos las aristas de dependencia (kallicode_core.cm_impacto).
- Orden determinista en cascada: cercanía topológica (distancia), relevancia semántica
  (similitud coseno con el embedding de la consulta, si lo hay; sin embedding no cuenta),
  recencia (creado_en descendente) e id (collation "C").
- Recorte al presupuesto por incidentes enteros, en orden, deteniéndose en el primero que no
  cabe: nunca parte un registro y uno menos relevante nunca desplaza a otro más relevante.
- Sin incidentes (tenant vacío, área vacía o sin enlaces): texto "" y lista vacía, sin error.
- El texto de un incidente es contenido no confiable para el LLM: se normaliza a una sola línea
  (sin saltos que puedan simular secciones o instrucciones) antes de entrar en la plantilla.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_core import cm_busqueda, cm_impacto

VERSION_CONTRATO = "cm-incidentes-v1"
RUTA_CONTRATO = Path(__file__).resolve().parents[4] / "contratos" / "cm-incidentes-v1.schema.json"

# Cotas: iguales que los CHECK de db/migrations/047_capa_incidentes.sql.
RESUMEN_MAX_CHARS = 500
CAUSA_MAX_CHARS = 1000
CORRECCION_MAX_CHARS = 1000
EVIDENCIA_MAX_CHARS = 500
MAX_NODOS_POR_INCIDENTE = 500
DIMENSION_EMBEDDING = 1024

SALTOS_DEFECTO = 2
SALTOS_MAX = 2
MAX_INCIDENTES_EN_CONTEXTO = 10
PRESUPUESTO_DEFECTO = 4_000
PRESUPUESTO_MAXIMO = 20_000
MAX_CANDIDATOS = 100
MAX_REFERENCIAS = 500

OrigenIncidente = Literal["ticket", "postmortem", "manual"]


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
_ESPACIOS = re.compile(r"\s+")


def normalizar(texto: str | None) -> str:
    """Una sola línea con espacios simples (el texto viene de tickets y QA: no confiable)."""
    return _ESPACIOS.sub(" ", texto or "").strip()


class RegistroIncidenteIn(BaseModel):
    """Alta (o nueva alta idempotente por ticket) de un incidente. El tenant es el de la sesión."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origen: OrigenIncidente
    ticket_id: str | None = Field(
        default=None, description="core.tickets.id; obligatorio con origen 'ticket'."
    )
    resumen: str = Field(min_length=1, max_length=RESUMEN_MAX_CHARS)
    causa: str | None = Field(default=None, max_length=CAUSA_MAX_CHARS)
    correccion: str | None = Field(default=None, max_length=CORRECCION_MAX_CHARS)
    evidencia_ref: str | None = Field(default=None, max_length=EVIDENCIA_MAX_CHARS)
    nodos: list[str] = Field(
        default_factory=list,
        max_length=MAX_NODOS_POR_INCIDENTE,
        description="Ids de nodos afectados (cm.nodes).",
    )
    ficheros: list[str] = Field(
        default_factory=list,
        max_length=MAX_NODOS_POR_INCIDENTE,
        description="Rutas afectadas (cm.files.path); enlazan el fichero y sus símbolos.",
    )

    @field_validator("resumen", "causa", "correccion", "evidencia_ref", mode="before")
    @classmethod
    def _una_linea(cls, v: Any) -> Any:
        if v is None:
            return None
        limpio = normalizar(str(v))
        return limpio or None

    @model_validator(mode="after")
    def _ticket_coherente(self) -> RegistroIncidenteIn:
        if self.origen == "ticket" and not self.ticket_id:
            raise ValueError("origen 'ticket' exige ticket_id")
        return self

    def texto_embedding(self) -> str:
        """Texto que se embebe para la relevancia semántica."""
        return " ".join(p for p in (self.resumen, self.causa, self.correccion) if p)


class RegistroIncidenteOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-incidentes-v1"] = VERSION_CONTRATO
    id: str
    creado: bool = Field(description="false si el ticket ya tenía incidente y se actualizó.")
    nodos_enlazados: list[str] = Field(description="Nodos existentes enlazados al incidente.")
    embebido: bool = Field(description="Se calculó (o se renovó) el embedding en este registro.")


def hash_texto(texto: str, modelo: str) -> str:
    """Huella del texto embebido: solo se re-embebe si cambia el texto o el modelo."""
    return hashlib.sha256(f"{modelo}\n{texto}".encode()).hexdigest()


class ConsultaIncidentesIn(BaseModel):
    """Área que se va a tocar y, opcionalmente, el embedding del ticket para ordenar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodos: list[str] = Field(
        default_factory=list,
        max_length=MAX_REFERENCIAS,
        description="Ids de nodos del grafo (cm.nodes) del área.",
    )
    ficheros: list[str] = Field(
        default_factory=list,
        max_length=MAX_REFERENCIAS,
        description="Rutas (cm.files.path); incluyen sus símbolos.",
    )
    saltos: int = Field(default=SALTOS_DEFECTO, ge=0, le=SALTOS_MAX)
    embedding: list[float] | None = Field(
        default=None,
        min_length=DIMENSION_EMBEDDING,
        max_length=DIMENSION_EMBEDDING,
        description="Embedding BGE-M3 del ticket; sin él no se ordena por similitud.",
    )
    presupuesto_caracteres: int = Field(default=PRESUPUESTO_DEFECTO, ge=0, le=PRESUPUESTO_MAXIMO)

    def con_area(self) -> bool:
        return bool(self.nodos or self.ficheros)


class IncidenteAplicable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    ticket: str | None = Field(description="Número del ticket (KC-####) si lo hay.")
    origen: OrigenIncidente
    resumen: str
    causa: str | None
    correccion: str | None
    distancia: int = Field(ge=0, le=SALTOS_MAX, description="Saltos desde el área.")
    similitud: float | None = Field(
        ge=-1, le=1, description="Coseno con el embedding de la consulta; null si no aplica."
    )
    creado_en: datetime


class ConsultaIncidentesOut(BaseModel):
    """Bloque listo para la plantilla y los incidentes que lo forman, en orden de entrega."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-incidentes-v1"] = VERSION_CONTRATO
    texto: str = Field(description='Una línea "- ..." por incidente; "" si no hay.')
    incidentes: list[IncidenteAplicable]
    omitidos: int = Field(ge=0, description="Candidatos que no cupieron en el presupuesto.")
    presupuesto_caracteres: int = Field(ge=0)
    caracteres_usados: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Renderizado y recorte
# ---------------------------------------------------------------------------
def renderizar_linea(incidente: IncidenteAplicable) -> str:
    partes = [normalizar(incidente.resumen)]
    if incidente.causa:
        partes.append(f"cause: {normalizar(incidente.causa)}")
    if incidente.correccion:
        partes.append(f"fix: {normalizar(incidente.correccion)}")
    prefijo = f"[{incidente.ticket}] " if incidente.ticket else ""
    return f"- {prefijo}" + " — ".join(partes)


def seleccionar_para_presupuesto(
    lineas: Sequence[str],
    presupuesto: int = PRESUPUESTO_DEFECTO,
    max_incidentes: int = MAX_INCIDENTES_EN_CONTEXTO,
) -> list[int]:
    """Índices de las líneas que entran enteras, en orden, en `presupuesto` caracteres.

    Cada línea ocupa su longitud más el salto que la separa de la anterior. Se detiene en la
    primera que no cabe para que un incidente menos relevante nunca desplace a uno más relevante.
    """
    elegidas: list[int] = []
    usado = 0
    for i, linea in enumerate(lineas):
        coste = len(linea) + (1 if elegidas else 0)
        if len(elegidas) >= max_incidentes or usado + coste > presupuesto:
            break
        elegidas.append(i)
        usado += coste
    return elegidas


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
# El recorrido (semillas + CTE recursiva con CYCLE + alcanzados) es el de cm_impacto en ambos
# sentidos; aquí solo se añaden los candidatos y el orden. Copia psycopg derivada en
# kallicode_codemapping.cm_queries.CONSULTAS_INCIDENTES.
SQL_INCIDENTES_AREA = (
    cm_impacto.ctes_recorrido("ambas")
    + """,
candidatos AS (
    SELECT inn.incident_id, min(a.distancia) AS distancia
      FROM alcanzados a
      JOIN cm.incident_nodes inn
        ON inn.tenant_id = core.current_tenant()
       AND inn.nodo_ref = a.id
     GROUP BY inn.incident_id
)
SELECT i.id, t.numero AS ticket, i.origen, i.resumen, i.causa, i.correccion, i.creado_en,
       c.distancia,
       CASE WHEN CAST(:embedding AS vector) IS NULL OR ie.embedding IS NULL THEN NULL
            ELSE 1 - (ie.embedding <=> CAST(:embedding AS vector))
       END AS similitud
  FROM candidatos c
  JOIN cm.incidents i
    ON i.tenant_id = core.current_tenant()
   AND i.id = c.incident_id
  LEFT JOIN vec.incident_embeddings ie
    ON ie.tenant_id = core.current_tenant()
   AND ie.incident_id = i.id
  LEFT JOIN core.tickets t
    ON t.tenant_id = core.current_tenant()
   AND t.id = i.ticket_id
 ORDER BY c.distancia, similitud DESC NULLS LAST, i.creado_en DESC, i.id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""
)


# --- Registro (alta idempotente por ticket) ---------------------------------------------------
# Un ticket deja un solo incidente (incidents_ticket_uq): registrarlo de nuevo actualiza el texto,
# la foto de nodos y los enlaces. Sin ticket, cada registro es un incidente nuevo.
SQL_UPSERT_INCIDENTE = """
INSERT INTO cm.incidents AS i
       (id, tenant_id, ticket_id, origen, resumen, causa, correccion, evidencia_ref,
        nodos_afectados)
VALUES (:id, core.current_tenant(), :ticket_id, :origen, :resumen, :causa, :correccion,
        :evidencia_ref, CAST(:nodos_afectados AS jsonb))
ON CONFLICT (tenant_id, ticket_id) WHERE ticket_id IS NOT NULL DO UPDATE
   SET origen = EXCLUDED.origen,
       resumen = EXCLUDED.resumen,
       causa = EXCLUDED.causa,
       correccion = EXCLUDED.correccion,
       evidencia_ref = EXCLUDED.evidencia_ref,
       nodos_afectados = EXCLUDED.nodos_afectados,
       actualizado_en = now()
RETURNING i.id, (i.xmax = 0) AS creado
"""

# Nodos afectados: los ids pedidos que existen y cada fichero con sus símbolos (como las semillas
# del recorrido: el recorrido no sigue DEFINES, así que un enlace solo al fichero no casaría con un
# área de símbolos).
SQL_NODOS_AFECTADOS = cm_impacto.SQL_SEMILLAS

SQL_BORRAR_ENLACES = """
DELETE FROM cm.incident_nodes d
 WHERE d.tenant_id = core.current_tenant()
   AND d.incident_id = :id
"""

SQL_INSERTAR_ENLACES = """
INSERT INTO cm.incident_nodes (tenant_id, incident_id, nodo_ref)
SELECT core.current_tenant(), :id, n.nodo
  FROM unnest(CAST(:nodos AS text[])) AS n(nodo)
ON CONFLICT DO NOTHING
"""

SQL_HASH_EMBEDDING = """
SELECT e.texto_hash
  FROM vec.incident_embeddings e
 WHERE e.tenant_id = core.current_tenant()
   AND e.incident_id = :id
"""

SQL_UPSERT_EMBEDDING = """
INSERT INTO vec.incident_embeddings (tenant_id, incident_id, embedding, modelo_embedding,
                                     texto_hash)
VALUES (core.current_tenant(), :id, CAST(:embedding AS vector), :modelo, :texto_hash)
ON CONFLICT (tenant_id, incident_id) DO UPDATE
   SET embedding = EXCLUDED.embedding,
       modelo_embedding = EXCLUDED.modelo_embedding,
       texto_hash = EXCLUDED.texto_hash,
       creado_en = now()
"""

# --- Expediente del ticket (fuente del incidente) ----------------------------------------------
SQL_TICKET_EXPEDIENTE = """
SELECT t.id, t.numero, t.tipo, t.titulo, t.impacto, t.pr_url
  FROM core.tickets t
 WHERE t.tenant_id = core.current_tenant()
   AND t.id = :ticket_id
"""

# Último resultado en falla de cada criterio en las corridas de QA del ticket.
SQL_FALLOS_QA = """
SELECT DISTINCT ON (qr.criterio_id) qr.criterio_id, qr.extracto_fallo
  FROM core.qa_results qr
  JOIN core.qa_runs qn
    ON qn.tenant_id = core.current_tenant()
   AND qn.id = qr.qa_run_id
 WHERE qr.tenant_id = core.current_tenant()
   AND qn.ticket_id = :ticket_id
   AND qr.resultado = 'falla'
 ORDER BY qr.criterio_id COLLATE "C", qn.corrida DESC
"""

# Módulos de tickets.impacto -> rutas de fichero (prefijo con semántica de carpeta; los patrones
# LIKE llegan escapados con barra invertida) y tablas -> ids de cm.db_tables (nombre o
# esquema.nombre, sin distinguir mayúsculas).
SQL_FICHEROS_DE_MODULOS = """
SELECT fm.path
  FROM cm.files fm
 WHERE fm.tenant_id = core.current_tenant()
   AND (fm.path = ANY(CAST(:modulos AS text[]))
        OR fm.path LIKE ANY(CAST(:patrones AS text[])))
 ORDER BY fm.path COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""

SQL_TABLAS_POR_NOMBRE = """
SELECT tb.id
  FROM cm.db_tables tb
 WHERE tb.tenant_id = core.current_tenant()
   AND (lower(tb.nombre) = ANY(CAST(:tablas AS text[]))
        OR lower(tb.esquema || '.' || tb.nombre) = ANY(CAST(:tablas AS text[])))
 ORDER BY tb.id COLLATE "C"
"""

CONSULTAS: dict[str, str] = {
    "incidentes_area": SQL_INCIDENTES_AREA,
    "upsert_incidente": SQL_UPSERT_INCIDENTE,
    "nodos_afectados": SQL_NODOS_AFECTADOS,
    "borrar_enlaces": SQL_BORRAR_ENLACES,
    "insertar_enlaces": SQL_INSERTAR_ENLACES,
    "hash_embedding": SQL_HASH_EMBEDDING,
    "upsert_embedding": SQL_UPSERT_EMBEDDING,
    "ticket_expediente": SQL_TICKET_EXPEDIENTE,
    "fallos_qa": SQL_FALLOS_QA,
    "ficheros_de_modulos": SQL_FICHEROS_DE_MODULOS,
    "tablas_por_nombre": SQL_TABLAS_POR_NOMBRE,
}


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parametros_modulos(modulos: Iterable[str]) -> dict[str, Any]:
    """Módulos ('src/billing') -> la ruta exacta y el patrón de carpeta ('src/billing/%')."""
    limpios = sorted({m.strip().rstrip("/") for m in modulos if m and m.strip().rstrip("/")})
    return {
        "modulos": limpios,
        "patrones": [_escapar_like(m) + "/%" for m in limpios],
        "limite": MAX_NODOS_POR_INCIDENTE,
    }


def vector_literal(embedding: Sequence[float] | None) -> str | None:
    """Literal de pgvector (el de cm_busqueda) o None; CAST(... AS vector) en ambos clientes."""
    return None if embedding is None else cm_busqueda.vector_literal(embedding)


def parametros_sql(consulta: ConsultaIncidentesIn) -> dict[str, Any]:
    """Parámetros de SQL_INCIDENTES_AREA (iguales en ambos clientes)."""
    return {
        "nodos": list(consulta.nodos),
        "ficheros": list(consulta.ficheros),
        "k": consulta.saltos,
        "tipos": [t.value for t in sorted(cm_impacto.TIPOS_DEPENDENCIA)],
        "embedding": vector_literal(consulta.embedding),
        "limite": MAX_CANDIDATOS,
    }


def construir_respuesta(
    consulta: ConsultaIncidentesIn, filas: Iterable[Mapping[str, Any]]
) -> ConsultaIncidentesOut:
    """Convierte las filas ordenadas en la respuesta recortada al presupuesto."""
    candidatos = [
        IncidenteAplicable(
            id=str(f["id"]),
            ticket=None if f["ticket"] is None else str(f["ticket"]),
            origen=f["origen"],
            resumen=normalizar(str(f["resumen"])),
            causa=normalizar(f["causa"]) or None,
            correccion=normalizar(f["correccion"]) or None,
            distancia=int(f["distancia"]),
            similitud=(
                None
                if f["similitud"] is None
                else round(min(max(float(f["similitud"]), -1.0), 1.0), 6)
            ),
            creado_en=f["creado_en"],
        )
        for f in filas
    ]
    lineas = [renderizar_linea(c) for c in candidatos]
    indices = seleccionar_para_presupuesto(lineas, consulta.presupuesto_caracteres)
    texto = "\n".join(lineas[i] for i in indices)
    return ConsultaIncidentesOut(
        texto=texto,
        incidentes=[candidatos[i] for i in indices],
        omitidos=len(candidatos) - len(indices),
        presupuesto_caracteres=consulta.presupuesto_caracteres,
        caracteres_usados=len(texto),
    )


def respuesta_vacia(consulta: ConsultaIncidentesIn) -> ConsultaIncidentesOut:
    return construir_respuesta(consulta, [])


async def resolver_incidentes(
    db: AsyncSession, consulta: ConsultaIncidentesIn
) -> ConsultaIncidentesOut:
    """Resuelve {{incidents}} con una sesión confinada al tenant (`db.sesion_tenant`)."""
    if not consulta.con_area():
        return respuesta_vacia(consulta)
    resultado = await db.execute(text(SQL_INCIDENTES_AREA), parametros_sql(consulta))
    return construir_respuesta(consulta, resultado.mappings().all())


# ---------------------------------------------------------------------------
# Contrato congelado
# ---------------------------------------------------------------------------
def generar_contrato() -> dict[str, Any]:
    """JSON Schema del contrato cm-incidentes-v1 a partir de los modelos (fuente única)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:kallicode:contrato:cm-incidentes:v1",
        "title": "Memoria de incidentes y resolución de {{incidents}} para las plantillas",
        "description": (
            "Contrato congelado v1.0.0 entre CodeMapping y Backend (KC-CM-09-2). `registro` es el "
            "alta idempotente por ticket; `entrada` la consulta por área y `salida` la respuesta. "
            "Solo incidentes del tenant de la sesión enlazados a nodos a <= 2 saltos del área; "
            "orden determinista (distancia, similitud desc, recencia, id); recorte por "
            "incidentes enteros. Cambios compatibles suben la versión menor; quitar o renombrar "
            "un campo exige v2."
        ),
        "x-version": "1.0.0",
        "x-orden": ["distancia", "similitud desc (nulls last)", "creado_en desc", "id (C)"],
        "x-presupuesto": {
            "max_incidentes": MAX_INCIDENTES_EN_CONTEXTO,
            "presupuesto_por_defecto": PRESUPUESTO_DEFECTO,
            "formato_linea": "- [KC-####] <resumen> — cause: <causa> — fix: <correccion>",
        },
        "registro": RegistroIncidenteIn.model_json_schema(),
        "registro_salida": RegistroIncidenteOut.model_json_schema(),
        "entrada": ConsultaIncidentesIn.model_json_schema(),
        "salida": ConsultaIncidentesOut.model_json_schema(),
    }


def serializar_contrato() -> str:
    return json.dumps(generar_contrato(), ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":  # pragma: no cover - regeneración manual del contrato
    RUTA_CONTRATO.write_text(serializar_contrato(), encoding="utf-8", newline="\n")
    sys.stdout.write(f"contrato escrito en {RUTA_CONTRATO}\n")
