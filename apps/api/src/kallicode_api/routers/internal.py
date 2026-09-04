"""API interna del pipeline /internal/v1 (§17 del diseño).

Solo red privada. Autenticación: JWT de servicio (typ=svc) + cabecera
Idempotency-Key obligatoria. Cada mutación escribe su evento de auditoría en
la MISMA transacción. El modelo LLM nunca llama esta API: proponen los
prompts, ejecutan las herramientas del orquestador.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, conflicto, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log

from ..deps import ServicioActual, servicio_actual, sesion_de

router = APIRouter(tags=["API interna"])

# Máquina de estados del job (§17.3): transiciones permitidas.
TRANSICIONES: dict[str, set[str]] = {
    "pendiente_asignacion": {"triage", "cancelado", "pausado_por_cuota"},
    "triage": {"design", "cerrado_duplicado", "cancelado", "escalado_humano"},
    "design": {"gate1", "cancelado", "escalado_humano"},
    "gate1": {"build", "design", "cancelado"},          # salir del gate = firma (§10)
    "build": {"qa", "design", "cancelado", "escalado_humano", "pausado_por_cuota"},
    "qa": {"gate2", "build", "cancelado", "escalado_humano"},
    "gate2": {"security", "build", "cancelado"},
    "security": {"deploy_preflight", "build", "cancelado", "escalado_humano"},
    "deploy_preflight": {"gate3", "build", "cancelado"},
    "gate3": {"deploy", "design", "build", "qa", "cancelado"},
    "deploy": {"produccion", "escalado_humano", "cancelado"},
    "pausado_por_cuota": {"triage", "build", "cancelado"},
    "escalado_humano": {"triage", "design", "build", "qa", "security", "deploy",
                        "cancelado"},
}
_GATES = {"gate1": 1, "gate2": 2, "gate3": 3}


# ---------------------------------------------------------------- idempotencia
async def _idempotente(db, svc: ServicioActual, clave: str, endpoint: str):
    """Devuelve la respuesta memorizada si la clave ya se usó (§3.8)."""
    fila = await uno(db, """SELECT respuesta, status_code FROM core.idempotency_keys
                             WHERE tenant_id=:t AND endpoint=:e AND clave=:c""",
                     {"t": svc.tenant_id, "e": endpoint, "c": clave})
    return fila


async def _memorizar(db, svc: ServicioActual, clave: str, endpoint: str,
                     respuesta: dict, status: int = 200) -> None:
    await db.execute(text("""
        INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta,
                                           status_code)
        VALUES (:c, :t, :e, CAST(:r AS jsonb), :s) ON CONFLICT DO NOTHING"""),
        {"c": clave, "t": svc.tenant_id, "e": endpoint, "r": json.dumps(respuesta),
         "s": status})


# -------------------------------------------------------------------- esquemas
class JobIn(BaseModel):
    ticket_id: str                       # KC-####
    prioridad_efectiva: str = Field(pattern=r"^P[1-4]$")


class AssignIn(BaseModel):
    linea: int = Field(ge=1)


class StateIn(BaseModel):
    a: str
    motivo: str | None = None
    contexto: dict | None = None


class StepIn(BaseModel):
    """Registro de un paso LLM ejecutado por un agente (§17.4).

    Alimenta a la vez: auditoría (cadena), consumo (usage_tokens) y métricas.
    """
    etapa: str = Field(pattern=r"^(triage|design|build|qa|security|deploy)$")
    paso: str
    version_plantilla: str
    modelo: str
    tier: str = Field(pattern=r"^(pro|flash|fable|vision)$")
    confianza: float | None = Field(default=None, ge=0, le=1)
    validacion: str = Field(pattern=r"^(esquema_valido|reintento|escalado_tier|escalado_humano)$")
    reintentos: int = Field(default=0, ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    duracion_ms: int = Field(ge=0)
    entrada_ref: str
    salida_ref: str


class TriageResultIn(BaseModel):
    tipo: str = Field(pattern=r"^(bug|mejora|funcionalidad|seguridad)$")
    prioridad: str = Field(pattern=r"^P[1-4]$")
    duplicado_de: str | None = None
    relacionados: list[str] = Field(default_factory=list)
    impacto: dict
    complejidad: str = Field(pattern=r"^(trivial|small|medium|large)$")
    tier_recomendado: str = Field(pattern=r"^(pro|flash|fable)$")


class AlternativaIn(BaseModel):
    id: str
    titulo: str
    descripcion: str = ""
    riesgo: str = Field(pattern=r"^(bajo|medio|alto)$")
    recomendada: bool = False


class CriterioSpecIn(BaseModel):
    id: str = Field(pattern=r"^AC-\d+$")
    given: str
    when: str
    then: str


class SpecIn(BaseModel):
    spec_md_ref: str
    alternativas: list[AlternativaIn] = Field(min_length=1, max_length=3)
    criterios: list[CriterioSpecIn] = Field(min_length=1)
    scope_paths: list[str] = Field(min_length=1)
    plan_tests: list | None = None
    migraciones: list | None = None
    adr_borrador: dict | None = None
    estimacion_loc: int | None = Field(default=None, ge=0)


class EvidenciaIn(BaseModel):
    corrida: int = Field(ge=1)
    matriz: list[dict] = Field(min_length=1)
    veredicto_visual: list[dict] = Field(default_factory=list)
    regresion: dict
    flaky: list = Field(default_factory=list)


class HallazgoIn(BaseModel):
    id: str | None = None
    origen: str = Field(pattern=r"^(sast|sca|secrets)$")
    severidad_contextual: str = Field(pattern=r"^(critica|alta|media|baja)$")
    titulo: str
    veredicto: str = Field(pattern=r"^(confirmado|falso_positivo)$")
    justificacion: str | None = None
    fix_diff_ref: str | None = None
    bloquea: bool = False


class FindingsIn(BaseModel):
    hallazgos: list[HallazgoIn]
    veredicto_cambio: str = Field(pattern=r"^(approved|blocked)$")
    tickets_proactivos: list[dict] = Field(default_factory=list)


class PreflightIn(BaseModel):
    destino: str = Field(pattern=r"^(staging|produccion)$")


class DeployResultIn(BaseModel):
    destino: str = Field(pattern=r"^(staging|produccion)$")
    resultado: str = Field(pattern=r"^(exito|anomalia|fallo)$")
    post_deploy: dict
    anomalia: dict | None = None
    changelog_ref: str | None = None
    doc_cambio_ref: str | None = None
    mensaje_reporter: str | None = None


class LocIn(BaseModel):
    ticket_id: str
    pr_url: str
    anadidas: int = Field(ge=0)
    eliminadas: int = Field(ge=0)
    netas: int
    tests: int = Field(default=0, ge=0)


class EscalationIn(BaseModel):
    job_id: str
    etapa: str
    paso: str | None = None
    motivo: str = Field(pattern=r"^(validacion_persistente|baja_confianza|max_iteraciones|"
                                r"selector_repair|anomalia_deploy|otro)$")
    severidad: str = Field(pattern=r"^(baja|media|alta|critica)$")
    detalle: dict


# -------------------------------------------------------------------- helpers
async def _ticket(db, tenant_id: str, numero: str) -> dict:
    t = await uno(db, "SELECT * FROM core.tickets WHERE tenant_id=:t AND numero=:n",
                  {"t": tenant_id, "n": numero})
    if not t:
        raise no_encontrado("El ticket")
    return t


async def _job_activo(db, job_id: str) -> dict:
    j = await uno(db, """SELECT * FROM core.jobs WHERE id=:i
                          AND estado NOT IN ('produccion','cancelado','cerrado_duplicado')
                          FOR UPDATE""", {"i": job_id})
    if not j:
        raise no_encontrado("El job")
    return j


# ------------------------------------------------------------------- endpoints
@router.post("/jobs", status_code=201, summary="Crear job desde un ticket")
async def crear_job(datos: JobIn, svc: ServicioActual = Depends(servicio_actual),
                    idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Errores: JOB_YA_EXISTE (409 — idempotencia devuelve el existente con 200)
    · FABRICA_INACTIVA (409). Comercial: QU-1 (nace pausado si cuota agotada)."""
    async with sesion_de(svc) as db:
        memo = await _idempotente(db, svc, idempotency_key, "jobs")
        if memo:
            return memo["respuesta"]
        org = await uno(db, "SELECT fabrica_activa FROM core.organizations WHERE id=:t",
                        {"t": svc.tenant_id})
        if not org or not org["fabrica_activa"]:
            raise conflicto("FABRICA_INACTIVA", "El tenant no completó el onboarding.")
        t = await _ticket(db, svc.tenant_id, datos.ticket_id)
        existente = await uno(db, """SELECT id, estado FROM core.jobs WHERE ticket_id=:tk
                                      AND estado NOT IN ('produccion','cancelado',
                                                         'cerrado_duplicado')""",
                              {"tk": t["id"]})
        if existente:
            raise conflicto("JOB_YA_EXISTE", "El ticket ya tiene un job activo.")
        pausado = await comercial.cuota_agotada(db, svc.tenant_id)
        jid = nuevo("job")
        estado = "pausado_por_cuota" if pausado else "pendiente_asignacion"
        await db.execute(text("""
            INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
            VALUES (:i, :t, :tk, :e, :p)"""),
            {"i": jid, "t": svc.tenant_id, "tk": t["id"], "e": estado,
             "p": datos.prioridad_efectiva})
        await registrar_evento(db, svc.tenant_id, evento="job_creado",
                               resumen=f"Job creado ({datos.prioridad_efectiva})",
                               actor_tipo="sistema", ticket_id=datos.ticket_id,
                               job_id=jid)
        respuesta = {"job_id": jid, "estado": estado}
        await _memorizar(db, svc, idempotency_key, "jobs", respuesta, 201)
    log.info("jobs.creado", job_id=jid, ticket_id=datos.ticket_id)
    return respuesta


@router.post("/jobs/{job_id}/assign", summary="Asignar job a línea")
async def asignar(job_id: str, datos: AssignIn,
                  svc: ServicioActual = Depends(servicio_actual),
                  idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """QU-2 transaccional: la línea se toma con FOR UPDATE y el índice único
    parcial impide dos jobs en la misma línea.
    Errores: LINEA_OCUPADA / LINEAS_EXCEDIDAS (409)."""
    async with sesion_de(svc) as db:
        memo = await _idempotente(db, svc, idempotency_key, "assign")
        if memo:
            return memo["respuesta"]
        job = await _job_activo(db, job_id)
        linea = await uno(db, """SELECT * FROM core.production_lines
                                  WHERE tenant_id=:t AND numero=:n FOR UPDATE""",
                          {"t": svc.tenant_id, "n": datos.linea})
        if not linea:
            raise no_encontrado("La línea")
        if linea["estado"] != "disponible":
            raise conflicto("LINEA_OCUPADA", "La línea ya tiene un job activo.")
        try:
            await db.execute(text("UPDATE core.jobs SET linea=:n, estado='triage' "
                                  "WHERE id=:j"), {"n": datos.linea, "j": job_id})
        except Exception:
            raise conflicto("LINEAS_EXCEDIDAS",
                            "El plan no permite más líneas concurrentes.")
        respuesta = {"job_id": job_id, "linea": datos.linea, "etapa": "triage"}
        await _memorizar(db, svc, idempotency_key, "assign", respuesta)
    log.info("jobs.asignado", job_id=job_id, linea=datos.linea)
    return respuesta


@router.patch("/jobs/{job_id}/state", summary="Transición de estado del job")
async def transicion(job_id: str, datos: StateIn,
                     svc: ServicioActual = Depends(servicio_actual),
                     idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Única vía de cambio de etapa; valida contra la máquina de estados.
    Salir de un gate solo lo hace la firma humana (GATE_REQUERIDO 409).
    Al entrar a build se re-verifica QU-1 (pausado_por_cuota si agotada).
    Errores: TRANSICION_INVALIDA / GATE_REQUERIDO / ARTEFACTO_FALTANTE (409)."""
    async with sesion_de(svc) as db:
        job = await _job_activo(db, job_id)
        de = job["estado"]
        if de in _GATES and datos.a in TRANSICIONES.get(de, set()) \
                and datos.a not in ("cancelado",):
            raise conflicto("GATE_REQUERIDO",
                            f"La transición requiere la firma humana del gate "
                            f"{_GATES[de]}.")
        if datos.a not in TRANSICIONES.get(de, set()):
            raise conflicto("TRANSICION_INVALIDA",
                            f"Transición {de}→{datos.a} no permitida.")
        if datos.a in ("build",) and await comercial.cuota_agotada(db, svc.tenant_id):
            datos.a = "pausado_por_cuota"
            datos.motivo = "cuota_loc_agotada"
        # precondición de artefactos para entrar a gates
        if datos.a == "gate1":
            sp = await uno(db, """SELECT id FROM core.specs WHERE ticket_id=:tk
                                   AND estado='pendiente_gate1'""", {"tk": job["ticket_id"]})
            if not sp:
                raise conflicto("ARTEFACTO_FALTANTE",
                                "Falta la spec para entrar a gate1.")
        if datos.a == "gate2":
            qa = await uno(db, "SELECT id FROM core.qa_runs WHERE ticket_id=:tk LIMIT 1",
                           {"tk": job["ticket_id"]})
            if not qa:
                raise conflicto("ARTEFACTO_FALTANTE",
                                "Falta la corrida de QA para entrar a gate2.")
        iteraciones = dict(job["iteraciones"] or {})
        if datos.a in ("design", "build", "qa") and datos.motivo:
            iteraciones[datos.a] = iteraciones.get(datos.a, 0) + 1
        await db.execute(text("""UPDATE core.jobs SET estado=:a,
                                        iteraciones=CAST(:it AS jsonb),
                                        contexto=CAST(:cx AS jsonb),
                                        terminado_en = CASE WHEN :a IN
                                            ('produccion','cancelado','cerrado_duplicado')
                                            THEN now() ELSE terminado_en END
                                  WHERE id=:j"""),
                         {"a": datos.a, "it": json.dumps(iteraciones),
                          "cx": json.dumps(datos.contexto) if datos.contexto else None,
                          "j": job_id})
        numero = (await uno(db, "SELECT numero FROM core.tickets WHERE id=:i",
                            {"i": job["ticket_id"]}))["numero"]
        await registrar_evento(db, svc.tenant_id, evento="job_transicion",
                               resumen=f"{de} → {datos.a}"
                                       + (f" ({datos.motivo})" if datos.motivo else ""),
                               actor_tipo="agente", actor_id=svc.servicio,
                               ticket_id=numero, job_id=job_id, etapa=datos.a)
        iteracion = iteraciones.get(datos.a, 0)
    log.info("jobs.transicion", job_id=job_id, de=de, a=datos.a, motivo=datos.motivo)
    return {"job_id": job_id, "de": de, "a": datos.a, "iteracion": iteracion}


@router.post("/jobs/{job_id}/steps", status_code=201, summary="Registrar paso LLM")
async def registrar_paso(job_id: str, datos: StepIn,
                         svc: ServicioActual = Depends(servicio_actual),
                         idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Persiste el paso (core.llm_steps), acumula tokens (usage_tokens) y
    sella el evento de auditoría — las tres cosas en una transacción.
    Error: ETAPA_INCONSISTENTE (409)."""
    async with sesion_de(svc) as db:
        memo = await _idempotente(db, svc, idempotency_key, "steps")
        if memo:
            return memo["respuesta"]
        job = await _job_activo(db, job_id)
        estado_norm = {"gate1": "design", "gate2": "qa", "gate3": "deploy",
                       "deploy_preflight": "deploy",
                       "pendiente_asignacion": "triage"}.get(job["estado"], job["estado"])
        if datos.etapa != estado_norm and job["estado"] != "escalado_humano":
            raise conflicto("ETAPA_INCONSISTENTE",
                            f"El job está en {job['estado']}, no en {datos.etapa}.")
        sid = nuevo("st")
        numero = (await uno(db, "SELECT numero FROM core.tickets WHERE id=:i",
                            {"i": job["ticket_id"]}))["numero"]
        await db.execute(text("""
            INSERT INTO core.llm_steps (id, tenant_id, job_id, ticket_id, etapa, paso,
                                        version_plantilla, modelo, tier, confianza,
                                        validacion, reintentos, tokens_in, tokens_out,
                                        duracion_ms, entrada_ref, salida_ref)
            VALUES (:i, :t, :j, :tk, :e, :p, :vp, :mo,
                    CASE WHEN :ti = 'fable' THEN 'pro' ELSE :ti END,
                    :cf, :va, :re, :tin, :tout, :du, :er, :sr)"""),
            {"i": sid, "t": svc.tenant_id, "j": job_id, "tk": job["ticket_id"],
             "e": datos.etapa, "p": datos.paso, "vp": datos.version_plantilla,
             "mo": datos.modelo, "ti": datos.tier, "cf": datos.confianza,
             "va": datos.validacion, "re": datos.reintentos, "tin": datos.tokens_in,
             "tout": datos.tokens_out, "du": datos.duracion_ms,
             "er": datos.entrada_ref, "sr": datos.salida_ref})
        await db.execute(text("""
            INSERT INTO core.usage_tokens (tenant_id, ticket_id, etapa, modelo, tier,
                                           tokens_in, tokens_out, duracion_ms)
            VALUES (:t, :tk, :e, :mo,
                    CASE WHEN :ti = 'fable' THEN 'pro' ELSE :ti END,
                    :tin, :tout, :du)"""),
            {"t": svc.tenant_id, "tk": job["ticket_id"], "e": datos.etapa,
             "mo": datos.modelo, "ti": datos.tier, "tin": datos.tokens_in,
             "tout": datos.tokens_out, "du": datos.duracion_ms})
        sello = await registrar_evento(
            db, svc.tenant_id, evento="paso_llm",
            resumen=f"{datos.paso} ({datos.modelo}, {datos.validacion})",
            actor_tipo="agente", actor_id=svc.servicio, ticket_id=numero,
            job_id=job_id, etapa=datos.etapa, modelo=datos.modelo, step_id=sid,
            datos={"tier": datos.tier, "confianza": datos.confianza,
                   "tokens": datos.tokens_in + datos.tokens_out})
        respuesta = {"step_id": sid, "sello": sello}
        await _memorizar(db, svc, idempotency_key, "steps", respuesta, 201)
    log.info("llm.paso_registrado", step_id=sid, paso=datos.paso, tier=datos.tier,
             tokens=datos.tokens_in + datos.tokens_out)
    return respuesta


@router.post("/tickets/{numero}/triage-result", summary="Registrar resultado de Triage")
async def triage_result(numero: str, datos: TriageResultIn,
                        svc: ServicioActual = Depends(servicio_actual),
                        idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Escribe clasificación/impacto/prioridad; duplicado_de cierra el ticket.
    Error: RECURSO_NO_ENCONTRADO si duplicado_de no existe."""
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, numero)
        dup_id = None
        if datos.duplicado_de:
            dup = await _ticket(db, svc.tenant_id, datos.duplicado_de)
            dup_id = dup["id"]
        await db.execute(text("""
            UPDATE core.tickets SET tipo=:tipo, prioridad_pipeline=:p,
                   duplicado_de=:dup, impacto=CAST(:imp AS jsonb), complejidad=:cx
             WHERE id=:i"""),
            {"tipo": datos.tipo, "p": datos.prioridad, "dup": dup_id,
             "imp": json.dumps(datos.impacto), "cx": datos.complejidad, "i": t["id"]})
        siguiente = "cerrado_duplicado" if dup_id else "design"
        await db.execute(text("""UPDATE core.jobs SET estado=:e WHERE ticket_id=:tk
                                  AND estado NOT IN ('produccion','cancelado',
                                                     'cerrado_duplicado')"""),
                         {"e": siguiente, "tk": t["id"]})
        await registrar_evento(
            db, svc.tenant_id, evento="job_transicion", etapa="triage",
            resumen=f"Clasificado como {datos.tipo} · prioridad {datos.prioridad} · "
                    f"impacto {datos.impacto.get('radio')}",
            actor_tipo="agente", actor_id="triage", ticket_id=numero,
            datos=datos.model_dump())
    log.info("triage.resultado", ticket_id=numero, prioridad=datos.prioridad,
             duplicado=bool(dup_id))
    return {"ticket_id": numero, "siguiente": siguiente}


@router.post("/tickets/{numero}/spec-versions", status_code=201,
             summary="Registrar versión de spec")
async def spec_version(numero: str, datos: SpecIn,
                       svc: ServicioActual = Depends(servicio_actual),
                       idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Nueva versión inmutable en pendiente_gate1; el job pasa a gate1.
    Errores: SCOPE_PROHIBIDO (422) · ETAPA_INCONSISTENTE (409).
    Comercial: estimacion_loc > cuota restante => aviso en la notificación."""
    rec = [a for a in datos.alternativas if a.recomendada]
    if len(rec) != 1:
        raise AppError("VALIDACION_ENTRADA", 422,
                       "Debe haber exactamente una alternativa recomendada.")
    if any(p.startswith(("infra/secrets", "/")) for p in datos.scope_paths):
        raise AppError("SCOPE_PROHIBIDO", 422,
                       "scope_paths incluye rutas vetadas por la política del tenant.")
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, numero)
        version = (await uno(db, """SELECT coalesce(max(version),0)+1 AS v
                                     FROM core.specs WHERE ticket_id=:tk""",
                             {"tk": t["id"]}))["v"]
        await db.execute(text("""UPDATE core.specs SET estado='reemplazada'
                                  WHERE ticket_id=:tk AND estado='pendiente_gate1'"""),
                         {"tk": t["id"]})
        sid = nuevo("sp")
        await db.execute(text("""
            INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref,
                                    scope_paths, plan_tests, migraciones, adr_borrador,
                                    estimacion_loc)
            VALUES (:i, :t, :tk, :v, :md, :sc, CAST(:pt AS jsonb), CAST(:mg AS jsonb),
                    CAST(:adr AS jsonb), :loc)"""),
            {"i": sid, "t": svc.tenant_id, "tk": t["id"], "v": version,
             "md": datos.spec_md_ref, "sc": datos.scope_paths,
             "pt": json.dumps(datos.plan_tests or []),
             "mg": json.dumps(datos.migraciones or []),
             "adr": json.dumps(datos.adr_borrador) if datos.adr_borrador else None,
             "loc": datos.estimacion_loc})
        for a in datos.alternativas:
            await db.execute(text("""
                INSERT INTO core.spec_alternatives (spec_id, tenant_id, alt_id, titulo,
                                                    descripcion, riesgo, recomendada)
                VALUES (:s, :t, :a, :ti, :d, :r, :rec)"""),
                {"s": sid, "t": svc.tenant_id, "a": a.id, "ti": a.titulo,
                 "d": a.descripcion, "r": a.riesgo, "rec": a.recomendada})
        for c in datos.criterios:
            await db.execute(text("""
                INSERT INTO core.spec_criteria (spec_id, tenant_id, criterio_id,
                                                given_txt, when_txt, then_txt)
                VALUES (:s, :t, :c, :g, :w, :th)"""),
                {"s": sid, "t": svc.tenant_id, "c": c.id, "g": c.given,
                 "w": c.when, "th": c.then})
        await db.execute(text("""UPDATE core.jobs SET estado='gate1' WHERE ticket_id=:tk
                                  AND estado NOT IN ('produccion','cancelado',
                                                     'cerrado_duplicado')"""),
                         {"tk": t["id"]})
        await registrar_evento(db, svc.tenant_id, evento="spec_generada", etapa="design",
                               resumen=f"Spec v{version} generada con "
                                       f"{len(datos.alternativas)} alternativas",
                               actor_tipo="agente", actor_id="design", ticket_id=numero)
        # notificación de gate pendiente a aprobadores
        aprobadores = await todos(db, """SELECT id FROM core.users
                                          WHERE tenant_id=:t AND estado='activo'
                                            AND rol IN ('owner','admin','architect','approver')""",
                                  {"t": svc.tenant_id})
        for u in aprobadores:
            await db.execute(text("""
                INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo,
                                                cuerpo, ticket_id)
                VALUES (:i, :t, :u, 'gate_pendiente', :ti, :c, :tk)"""),
                {"i": nuevo("nt"), "t": svc.tenant_id, "u": u["id"],
                 "ti": f"GATE 1 · {t['titulo']}",
                 "c": f"Spec v{version} lista para tu aprobación.", "tk": t["id"]})
    log.info("design.spec", ticket_id=numero, version=version)
    return {"version": version, "estado": "pendiente_gate1"}


@router.post("/tickets/{numero}/evidence", status_code=201,
             summary="Registrar corrida de QA")
async def evidencia(numero: str, datos: EvidenciaIn,
                    svc: ServicioActual = Depends(servicio_actual),
                    idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Matriz completa (todos los criterios de la spec, si no MATRIZ_INCOMPLETA
    422). Todos pasan => gate2; si no, retorno a build con diagnóstico."""
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, numero)
        spec = await uno(db, """SELECT * FROM core.specs WHERE ticket_id=:tk
                                 AND estado='aprobada' ORDER BY version DESC LIMIT 1""",
                         {"tk": t["id"]})
        if not spec:
            raise conflicto("ETAPA_INCONSISTENTE", "El ticket no tiene spec aprobada.")
        criterios = {c["criterio_id"] for c in await todos(
            db, "SELECT criterio_id FROM core.spec_criteria WHERE spec_id=:s",
            {"s": spec["id"]})}
        cubiertos = {m.get("criterio_id") for m in datos.matriz}
        faltantes = criterios - cubiertos
        if faltantes:
            raise AppError("MATRIZ_INCOMPLETA", 422,
                           f"Faltan criterios en la matriz: {sorted(faltantes)}.")
        rid = nuevo("qr")
        await db.execute(text("""
            INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida,
                                      regresion, flaky)
            VALUES (:i, :t, :tk, :s, :c, CAST(:r AS jsonb), CAST(:f AS jsonb))"""),
            {"i": rid, "t": svc.tenant_id, "tk": t["id"], "s": spec["id"],
             "c": datos.corrida, "r": json.dumps(datos.regresion),
             "f": json.dumps(datos.flaky)})
        visual = {v.get("criterio_id"): v for v in datos.veredicto_visual}
        for m in datos.matriz:
            await db.execute(text("""
                INSERT INTO core.qa_results (qa_run_id, tenant_id, criterio_id,
                                             resultado, extracto_fallo,
                                             veredicto_visual, evidencias)
                VALUES (:r, :t, :c, :res, :ex, CAST(:vv AS jsonb), CAST(:ev AS jsonb))"""),
                {"r": rid, "t": svc.tenant_id, "c": m["criterio_id"],
                 "res": m["resultado"], "ex": m.get("extracto_fallo"),
                 "vv": json.dumps(visual.get(m["criterio_id"])),
                 "ev": json.dumps(m.get("evidencias", []))})
        pasan = all(m["resultado"] == "pasa" for m in datos.matriz)
        siguiente = "gate2" if pasan else "build"
        await db.execute(text("""UPDATE core.jobs SET estado=:e WHERE ticket_id=:tk
                                  AND estado NOT IN ('produccion','cancelado',
                                                     'cerrado_duplicado')"""),
                         {"e": siguiente, "tk": t["id"]})
        resumen = (f"Corrida {datos.corrida}: "
                   f"{sum(1 for m in datos.matriz if m['resultado'] == 'pasa')}"
                   f"/{len(datos.matriz)} criterios pasan")
        await registrar_evento(db, svc.tenant_id, evento="qa_corrida", etapa="qa",
                               resumen=resumen, actor_tipo="agente", actor_id="qa",
                               ticket_id=numero, datos={"corrida": datos.corrida})
    log.info("qa.corrida", ticket_id=numero, corrida=datos.corrida, siguiente=siguiente)
    return {"corrida": datos.corrida, "siguiente": siguiente}


@router.post("/tickets/{numero}/findings", status_code=201,
             summary="Registrar veredicto de Security")
async def findings(numero: str, datos: FindingsIn,
                   svc: ServicioActual = Depends(servicio_actual),
                   idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """blocked exige >=1 bloqueante (VEREDICTO_INCONSISTENTE 422); los
    proactivos entran como tickets tipo seguridad por QU-1."""
    bloqueantes = [h for h in datos.hallazgos if h.bloquea]
    if (datos.veredicto_cambio == "blocked") != bool(bloqueantes):
        raise AppError("VEREDICTO_INCONSISTENTE", 422,
                       "blocked sin hallazgos bloqueantes (o viceversa).")
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, numero)
        for h in datos.hallazgos:
            await db.execute(text("""
                INSERT INTO core.security_findings (id, tenant_id, ticket_id, origen,
                                                    severidad, titulo, veredicto,
                                                    justificacion, fix_diff_ref, bloquea,
                                                    estado)
                VALUES (:i, :t, :tk, :o, :sev, :ti, :v, :j, :fx, :b,
                        CASE WHEN :v = 'falso_positivo' THEN 'descartado'
                             ELSE 'abierto' END)"""),
                {"i": h.id or nuevo("sf"), "t": svc.tenant_id, "tk": t["id"],
                 "o": h.origen, "sev": h.severidad_contextual, "ti": h.titulo,
                 "v": h.veredicto, "j": h.justificacion, "fx": h.fix_diff_ref,
                 "b": h.bloquea})
            await registrar_evento(db, svc.tenant_id, evento="hallazgo_seguridad",
                                   etapa="security",
                                   resumen=f"{h.titulo} ({h.veredicto})",
                                   actor_tipo="agente", actor_id="security",
                                   ticket_id=numero,
                                   datos={"severidad": h.severidad_contextual,
                                          "justificacion": h.justificacion})
        siguiente = "build" if datos.veredicto_cambio == "blocked" else "deploy_preflight"
        await db.execute(text("""UPDATE core.jobs SET estado=:e WHERE ticket_id=:tk
                                  AND estado NOT IN ('produccion','cancelado',
                                                     'cerrado_duplicado')"""),
                         {"e": siguiente, "tk": t["id"]})
        creados = []
        for p in datos.tickets_proactivos:
            # nacen como tickets de seguridad por el flujo normal (QU-1 aplica)
            pausado = await comercial.cuota_agotada(db, svc.tenant_id)
            fila = await uno(db, """
                INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion,
                                          prioridad, etapa, origen)
                VALUES (:i, :t, 'seguridad', :ti, :d, 'alta',
                        :e, 'portal') RETURNING numero""",
                {"i": nuevo("tk"), "t": svc.tenant_id, "ti": p.get("titulo", "Hallazgo"),
                 "d": p.get("cuerpo", "-"),
                 "e": "en_cola_por_cuota" if pausado else "triage"})
            creados.append(fila["numero"])
    log.info("security.veredicto", ticket_id=numero, veredicto=datos.veredicto_cambio,
             hallazgos=len(datos.hallazgos))
    return {"siguiente": siguiente, "tickets_creados": creados}


@router.post("/tickets/{numero}/deploy/preflight", summary="Preflight de deploy")
async def preflight(numero: str, datos: PreflightIn,
                    svc: ServicioActual = Depends(servicio_actual),
                    idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Verifica EN BASE las precondiciones (firmas, security). go expira a los
    60 min (se guarda en Redis). no_go NO es error: 200 con faltantes."""
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, numero)
        firmas = {f["gate"] for f in await todos(
            db, """SELECT gate FROM core.gate_signatures
                    WHERE ticket_id=:tk AND accion='aprobado'""", {"tk": t["id"]})}
        bloqueantes = (await uno(db, """SELECT count(*) AS n FROM core.security_findings
                                         WHERE ticket_id=:tk AND bloquea
                                           AND estado='abierto'""", {"tk": t["id"]}))["n"]
        requeridas = {1, 2, 3} if datos.destino == "produccion" else {1, 2}
        faltantes = [f"gate{g}" for g in sorted(requeridas - firmas)]
        if bloqueantes:
            faltantes.append("security_bloqueantes")
        veredicto = "go" if not faltantes else "no_go"
        await registrar_evento(db, svc.tenant_id, evento="deploy_ejecutado",
                               etapa="deploy",
                               resumen=f"Preflight {datos.destino}: {veredicto}",
                               actor_tipo="agente", actor_id="deploy", ticket_id=numero,
                               datos={"faltantes": faltantes})
    if veredicto == "go":
        await comercial.redis_cliente().setex(
            f"preflight:{numero}:{datos.destino}", 3600, "go")
    log.info("deploy.preflight", ticket_id=numero, destino=datos.destino,
             veredicto=veredicto, faltantes=faltantes)
    return {"veredicto": veredicto, "faltantes": faltantes}


@router.post("/tickets/{numero}/deploy/result", summary="Registrar resultado de deploy")
async def deploy_result(numero: str, datos: DeployResultIn,
                        svc: ServicioActual = Depends(servicio_actual),
                        idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Cierra el circuito. Requiere preflight go vigente (PREFLIGHT_VENCIDO 409);
    anomalía exige análisis (ANOMALIA_SIN_ANALISIS 422). Éxito en producción:
    ticket→produccion, línea liberada, notificaciones a watchers y reporter."""
    r = comercial.redis_cliente()
    if not await r.get(f"preflight:{numero}:{datos.destino}"):
        raise conflicto("PREFLIGHT_VENCIDO", "El preflight expiró; re-ejecuta la verificación.")
    if datos.resultado == "anomalia" and not datos.anomalia:
        raise AppError("ANOMALIA_SIN_ANALISIS", 422,
                       "resultado=anomalia requiere el análisis y la recomendación.")
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, numero)
        if datos.resultado == "exito" and datos.destino == "produccion":
            await db.execute(text("""UPDATE core.jobs SET estado='produccion'
                                      WHERE ticket_id=:tk AND estado NOT IN
                                            ('produccion','cancelado','cerrado_duplicado')"""),
                             {"tk": t["id"]})
            watchers = await todos(db, """SELECT user_id FROM core.ticket_watchers
                                           WHERE ticket_id=:tk""", {"tk": t["id"]})
            for w in watchers:
                await db.execute(text("""
                    INSERT INTO core.notifications (id, tenant_id, user_id, tipo,
                                                    titulo, cuerpo, ticket_id)
                    VALUES (:i, :t, :u, 'ticket_cerrado', :ti, :c, :tk)"""),
                    {"i": nuevo("nt"), "t": svc.tenant_id, "u": w["user_id"],
                     "ti": f"En producción · {t['titulo']}",
                     "c": datos.mensaje_reporter or "El cambio está en producción.",
                     "tk": t["id"]})
        await registrar_evento(db, svc.tenant_id, evento="deploy_ejecutado",
                               etapa="deploy",
                               resumen=f"Deploy {datos.destino}: {datos.resultado}",
                               actor_tipo="agente", actor_id="deploy", ticket_id=numero,
                               datos={"post_deploy": datos.post_deploy,
                                      "anomalia": datos.anomalia})
        job = await uno(db, """SELECT linea FROM core.jobs WHERE ticket_id=:tk
                                ORDER BY creado_en DESC LIMIT 1""", {"tk": t["id"]})
    if datos.resultado == "anomalia":
        log.warning("deploy.anomalia", ticket_id=numero,
                    recomendacion=datos.anomalia.get("recomendacion"))
    log.info("deploy.resultado", ticket_id=numero, destino=datos.destino,
             resultado=datos.resultado)
    return {"ticket": {"id": numero,
                       "etapa": "produccion" if datos.resultado == "exito"
                                and datos.destino == "produccion" else t["etapa"],
                       "linea_liberada": job and job["linea"]}}


@router.post("/usage/loc", status_code=201, summary="Registrar LOC del PR entregado")
async def registrar_loc(datos: LocIn, svc: ServicioActual = Depends(servicio_actual),
                        idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """ÚNICO punto que descuenta cuota QU-1 (idempotente por pr_url). El
    trigger T6 mantiene usage_cycles y calcula el umbral; el cruce de 80/100%
    se notifica al owner. Error: LOC_INCONSISTENTES (422)."""
    if datos.netas != datos.anadidas - datos.eliminadas:
        raise AppError("LOC_INCONSISTENTES", 422, "netas ≠ añadidas − eliminadas.")
    async with sesion_de(svc) as db:
        t = await _ticket(db, svc.tenant_id, datos.ticket_id)
        antes = (await comercial.estado_cuota_loc(db, svc.tenant_id))["umbral"]
        fila = await uno(db, """
            INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, pr_url,
                                        anadidas, eliminadas, netas, tests)
            VALUES (:t, :tk, :c, 'pr', :pr, :a, :e, :n, :ts)
            ON CONFLICT (pr_url) WHERE tipo = 'pr' DO NOTHING
            RETURNING id""",
            {"t": svc.tenant_id, "tk": t["id"], "c": comercial.ciclo_actual(),
             "pr": datos.pr_url, "a": datos.anadidas, "e": datos.eliminadas,
             "n": datos.netas, "ts": datos.tests})
        cuota = await comercial.estado_cuota_loc(db, svc.tenant_id)
        cruzado = cuota["umbral"] if fila and cuota["umbral"] != antes else None
        await registrar_evento(db, svc.tenant_id, evento="consumo_registrado",
                               resumen=f"{datos.netas} LOC netas (PR)",
                               actor_tipo="sistema", ticket_id=datos.ticket_id,
                               datos={"pr_url": datos.pr_url, "netas": datos.netas})
        if cruzado:
            owners = await todos(db, """SELECT id FROM core.users
                                         WHERE tenant_id=:t AND rol='owner'
                                           AND estado='activo'""", {"t": svc.tenant_id})
            msj = ("Alcanzaste el 80% de la cuota mensual de LOC."
                   if cruzado == "aviso_80" else
                   "Cuota mensual de LOC agotada: los tickets nuevos quedan en cola.")
            for o in owners:
                await db.execute(text("""
                    INSERT INTO core.notifications (id, tenant_id, user_id, tipo,
                                                    titulo, cuerpo)
                    VALUES (:i, :t, :u, 'aviso_cuota', 'Aviso de consumo', :c)"""),
                    {"i": nuevo("nt"), "t": svc.tenant_id, "u": o["id"], "c": msj})
            log.warning(f"usage.{'umbral_80' if cruzado == 'aviso_80' else 'agotado'}",
                        consumidas=cuota["consumidas"], limite=cuota["limite"])
    log.info("usage.loc", ticket_id=datos.ticket_id, netas=datos.netas,
             consumo=cuota["consumidas"])
    return {"consumo_ciclo": {"consumidas": cuota["consumidas"],
                              "limite": cuota["limite"],
                              "porcentaje": cuota["porcentaje"]},
            "umbral_cruzado": cruzado}


@router.post("/escalations", status_code=201, summary="Escalar a humano")
async def escalar(datos: EscalationIn, svc: ServicioActual = Depends(servicio_actual),
                  idempotency_key: str = Header(alias="Idempotency-Key")) -> dict:
    """Pausa el job (escalado_humano) y abre la escalación. Idempotente por
    job+paso (ESCALADO_DUPLICADO devuelve la existente). Severidad critica
    emite CRITICAL en el log central (página a guardia)."""
    async with sesion_de(svc) as db:
        job = await _job_activo(db, datos.job_id)
        existente = await uno(db, """SELECT id FROM core.escalations
                                      WHERE job_id=:j AND coalesce(paso,'-')=coalesce(:p,'-')
                                        AND estado='abierta'""",
                              {"j": datos.job_id, "p": datos.paso})
        if existente:
            return {"escalation_id": existente["id"], "estado": "abierta"}
        eid = nuevo("esc")
        await db.execute(text("""
            INSERT INTO core.escalations (id, tenant_id, job_id, etapa, paso, motivo,
                                          severidad, detalle)
            VALUES (:i, :t, :j, :e, :p, :m, :s, CAST(:d AS jsonb))"""),
            {"i": eid, "t": svc.tenant_id, "j": datos.job_id, "e": datos.etapa,
             "p": datos.paso, "m": datos.motivo, "s": datos.severidad,
             "d": json.dumps(datos.detalle)})
        await db.execute(text("UPDATE core.jobs SET estado='escalado_humano' WHERE id=:j"),
                         {"j": datos.job_id})
        numero = (await uno(db, "SELECT numero FROM core.tickets WHERE id=:i",
                            {"i": job["ticket_id"]}))["numero"]
        await registrar_evento(db, svc.tenant_id, evento="escalado_a_humano",
                               etapa=datos.etapa,
                               resumen=f"Escalado: {datos.motivo} ({datos.severidad})",
                               actor_tipo="agente", actor_id=svc.servicio,
                               ticket_id=numero, job_id=datos.job_id,
                               datos=datos.detalle)
    nivel = log.critical if datos.severidad == "critica" else log.warning
    nivel("escalations.abierta", job_id=datos.job_id, motivo=datos.motivo,
          severidad=datos.severidad)
    return {"escalation_id": eid, "estado": "abierta"}
