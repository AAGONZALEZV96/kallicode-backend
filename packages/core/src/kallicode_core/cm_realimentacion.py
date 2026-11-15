"""Contrato cm-realimentacion-v1: realimentación del grafo desde los tickets entregados (KC-CM-10-4).

Cuando un ticket llega a producción, CodeMapping lee su expediente (título, descripción, criterios
de la spec, fallos de QA, hallazgos de seguridad confirmados y comentarios de los gates), pide al
LLM candidatos a nugget y a incidente y los deja en la cola de curación. Nada entra al grafo sin
una resolución humana:

- cada nugget se guarda en cm.nuggets con origen 'ticket', estado 'propuesto' y su revisión
  pendiente en cm.curation_queue (la base no deja otro estado inicial: 041 y 055);
- el incidente candidato queda en cm.curation_queue (tipo 'incidente', uno por ticket) y solo
  llega a cm.incidents, donde lo lee {{incidents}}, con cm.resolver_incidente() aprobado.

Idempotencia por ticket: cm.realimentacion_tickets guarda el resultado; un ticket 'completado'
no se vuelve a enviar al LLM. Los ids de los nuggets son deterministas y el candidato a incidente
es único por ticket, así que ni siquiera un reproceso forzado duplica.

Este módulo es la única definición del contrato: los modelos, las cotas y el SQL. El cliente
psycopg está en `kallicode_codemapping.realimentacion`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kallicode_core import cm_incidentes

VERSION_CONTRATO = "cm-realimentacion-v1"

# Stream de Redis por el que la API avisa de un ticket entregado: {tenant_id, ticket_id}.
STREAM = "kc:realimentacion"
ORIGEN_CONSUMO = "codemapping_feedback"  # core.usage_tokens.origen (055)
ETAPA_CONSUMO = "realimentacion"  # core.usage_tokens.etapa
TIPO_CURACION_INCIDENTE = "incidente"  # cm.curation_queue.tipo (055)

# Cotas del expediente que se envía al LLM (presupuesto de entrada del paso).
MAX_ELEMENTOS = 60
ELEMENTO_MAX_CHARS = 1_500
DESCRIPCION_MAX_CHARS = 3_000
MAX_HALLAZGOS = 30
MAX_COMENTARIOS_GATE = 20

EstadoRealimentacion = Literal["en_curso", "completado", "fallido", "sin_presupuesto"]
TipoElemento = Literal["ticket", "spec", "qa", "seguridad", "gate"]

# Los incidentes solo pueden apoyarse en evidencia de un fallo: QA, seguridad o un gate que
# devolvió el trabajo. Un título o un criterio de la spec no prueban que algo fallara.
TIPOS_EVIDENCIA_INCIDENTE: frozenset[str] = frozenset({"qa", "seguridad", "gate"})


class ElementoExpediente(BaseModel):
    """Una pieza citable del expediente: el LLM debe citar su `id` y un fragmento literal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80, description="p. ej. 'qa:AC-2', 'sf:sf_01H...'")
    tipo: TipoElemento
    texto: str = Field(min_length=1, max_length=ELEMENTO_MAX_CHARS)


class ExpedienteTicket(BaseModel):
    """Lo que se sabe de un ticket entregado para proponer candidatos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    numero: str | None
    tipo: str
    etapa: str
    pr_url: str | None = None
    alcance: list[str] = Field(default_factory=list, description="scope_paths de la spec.")
    elementos: list[ElementoExpediente] = Field(max_length=MAX_ELEMENTOS)

    @property
    def referencia(self) -> str:
        """Cómo se cita el ticket: su número visible o, si no lo tiene, su id."""
        return self.numero or self.ticket_id

    def con_fallos(self) -> bool:
        """El expediente tiene evidencia de un fallo (requisito para proponer un incidente)."""
        return any(e.tipo in TIPOS_EVIDENCIA_INCIDENTE for e in self.elementos)


class CandidatoIncidente(BaseModel):
    """Payload del candidato en cm.curation_queue (tipo 'incidente').

    Los campos de registro son los de `cm_incidentes.RegistroIncidenteIn`; `evidencias` son los ids
    de los elementos del expediente que lo respaldan y `citas` sus fragmentos literales.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ticket_id: str
    resumen: str = Field(min_length=1, max_length=cm_incidentes.RESUMEN_MAX_CHARS)
    causa: str | None = Field(default=None, max_length=cm_incidentes.CAUSA_MAX_CHARS)
    correccion: str | None = Field(default=None, max_length=cm_incidentes.CORRECCION_MAX_CHARS)
    evidencia_ref: str = Field(max_length=cm_incidentes.EVIDENCIA_MAX_CHARS)
    nodos: list[str] = Field(default_factory=list, max_length=cm_incidentes.MAX_NODOS_POR_INCIDENTE)
    evidencias: list[str] = Field(min_length=1)
    citas: list[str] = Field(min_length=1)
    origen: Literal["realimentacion"] = "realimentacion"


class ResultadoRealimentacion(BaseModel):
    """Lo que dejó (o ya había dejado) la realimentación de un ticket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-realimentacion-v1"] = VERSION_CONTRATO
    ticket_id: str
    estado: EstadoRealimentacion
    omitido: bool = Field(
        description="true si el ticket ya estaba realimentado (o en curso) y no se hizo nada."
    )
    nuggets_propuestos: int = Field(ge=0, description="Nuggets nuevos en cm.nuggets.")
    incidente_propuesto: bool
    descartados: int = Field(ge=0, description="Propuestas del LLM descartadas por validación.")
    motivos_descarte: dict[str, int] = Field(default_factory=dict)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    motivo: str | None = None


# ---------------------------------------------------------------------------
# SQL (parámetros :nombre; el cliente psycopg los traduce a %(nombre)s)
# ---------------------------------------------------------------------------
# Reclama el ticket. Devuelve fila solo si este proceso debe trabajar: alta nueva, un intento
# anterior que falló o se quedó sin presupuesto, o uno 'en_curso' abandonado (caducado).
SQL_RECLAMAR = """
INSERT INTO cm.realimentacion_tickets AS r (tenant_id, ticket_id)
VALUES (core.current_tenant(), :ticket_id)
ON CONFLICT (tenant_id, ticket_id) DO UPDATE
   SET estado = 'en_curso',
       intentos = r.intentos + 1,
       motivo = NULL,
       iniciado_en = now(),
       actualizado_en = now()
 WHERE r.estado IN ('fallido', 'sin_presupuesto')
    OR (r.estado = 'en_curso'
        AND r.actualizado_en < now() - make_interval(secs => CAST(:caducidad_s AS integer)))
RETURNING r.intentos
"""

SQL_ESTADO = """
SELECT r.estado, r.nuggets_propuestos, r.incidente_propuesto, r.descartados, r.tokens_in,
       r.tokens_out, r.motivo
  FROM cm.realimentacion_tickets r
 WHERE r.tenant_id = core.current_tenant()
   AND r.ticket_id = :ticket_id
"""

SQL_CERRAR = """
UPDATE cm.realimentacion_tickets
   SET estado = :estado,
       nuggets_propuestos = :nuggets_propuestos,
       incidente_propuesto = :incidente_propuesto,
       descartados = :descartados,
       tokens_in = :tokens_in,
       tokens_out = :tokens_out,
       motivo = :motivo,
       actualizado_en = now(),
       completado_en = CASE WHEN :estado = 'completado' THEN now() END
 WHERE tenant_id = core.current_tenant()
   AND ticket_id = :ticket_id
"""

SQL_TICKET = """
SELECT t.id, t.numero, t.tipo, t.titulo, t.descripcion, t.etapa, t.impacto, t.pr_url
  FROM core.tickets t
 WHERE t.tenant_id = core.current_tenant()
   AND t.id = :ticket_id
"""

# La spec que se firmó (aprobada) o, si no hay, la última versión.
SQL_SPEC = """
SELECT s.id, s.scope_paths
  FROM core.specs s
 WHERE s.tenant_id = core.current_tenant()
   AND s.ticket_id = :ticket_id
 ORDER BY (s.estado = 'aprobada') DESC, s.version DESC
 LIMIT 1
"""

SQL_CRITERIOS = """
SELECT c.criterio_id, c.given_txt, c.when_txt, c.then_txt
  FROM core.spec_criteria c
 WHERE c.tenant_id = core.current_tenant()
   AND c.spec_id = :spec_id
 ORDER BY length(c.criterio_id), c.criterio_id COLLATE "C"
"""

SQL_FALLOS_QA = cm_incidentes.SQL_FALLOS_QA

SQL_HALLAZGOS = """
SELECT f.id, f.origen, f.regla, f.archivo, f.severidad, f.titulo, f.estado
  FROM core.security_findings f
 WHERE f.tenant_id = core.current_tenant()
   AND f.ticket_id = :ticket_id
   AND f.veredicto = 'confirmado'
 ORDER BY f.creado_en, f.id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""

SQL_COMENTARIOS_GATE = """
SELECT g.id, g.gate, g.accion, g.comentario
  FROM core.gate_signatures g
 WHERE g.tenant_id = core.current_tenant()
   AND g.ticket_id = :ticket_id
   AND g.comentario IS NOT NULL
   AND btrim(g.comentario) <> ''
 ORDER BY g.creado_en, g.id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""

# Tokens LLM consumidos por el tenant en el mes en curso (todas las procedencias): la misma
# ventana que la cuota mensual LLM de kallicode_core.llm.seguridad.
SQL_CONSUMO_MES = """
SELECT COALESCE(sum(u.tokens_in + u.tokens_out), 0)::bigint AS consumido
  FROM core.usage_tokens u
 WHERE u.tenant_id = core.current_tenant()
   AND u.creado_en >= date_trunc('month', now())
"""

SQL_INSERTAR_CANDIDATO_INCIDENTE = """
INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
VALUES (:id, core.current_tenant(), 'incidente', CAST(:payload AS jsonb))
ON CONFLICT (tenant_id, (payload ->> 'ticket_id')) WHERE tipo = 'incidente' DO NOTHING
RETURNING id
"""

SQL_REGISTRAR_CONSUMO = """
INSERT INTO core.usage_tokens
       (tenant_id, ticket_id, etapa, modelo, tier, invocaciones, tokens_in, tokens_out,
        duracion_ms, origen)
VALUES (core.current_tenant(), :ticket_id, 'realimentacion', :modelo, :tier, :invocaciones,
        :tokens_in, :tokens_out, :duracion_ms, 'codemapping_feedback')
"""

SQL_RESOLVER_INCIDENTE = """
SELECT cm.resolver_incidente(:revision_id, :usuario_id, :decision, :motivo, :resumen, :causa,
                             :correccion) AS incidente_id
"""

# Texto del incidente aprobado, para calcular su embedding después de la resolución.
SQL_TEXTO_INCIDENTE = """
SELECT i.resumen, i.causa, i.correccion
  FROM cm.incidents i
 WHERE i.tenant_id = core.current_tenant()
   AND i.id = :id
"""

CONSULTAS: dict[str, str] = {
    "texto_incidente": SQL_TEXTO_INCIDENTE,
    "reclamar": SQL_RECLAMAR,
    "estado": SQL_ESTADO,
    "cerrar": SQL_CERRAR,
    "ticket": SQL_TICKET,
    "spec": SQL_SPEC,
    "criterios": SQL_CRITERIOS,
    "fallos_qa": SQL_FALLOS_QA,
    "hallazgos": SQL_HALLAZGOS,
    "comentarios_gate": SQL_COMENTARIOS_GATE,
    "consumo_mes": SQL_CONSUMO_MES,
    "insertar_candidato_incidente": SQL_INSERTAR_CANDIDATO_INCIDENTE,
    "registrar_consumo": SQL_REGISTRAR_CONSUMO,
    "resolver_incidente": SQL_RESOLVER_INCIDENTE,
}
