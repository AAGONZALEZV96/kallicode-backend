"""Tickets y funcionalidades (§9 del diseño de endpoints).

Incluye: listado con filtros, creación (bug/mejora y funcionalidad con QU-1),
ayudas en vivo (dedup vectorial e impacto), detalle y pestañas (spec,
evidencia, seguridad, consumo, actividad), adjuntos por URL firmada,
watchers y cancelación.
"""
from __future__ import annotations

import json
from datetime import date

import httpx
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.config import get_settings
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, dependencia_no_disponible, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual
from ..servicios import blob

router = APIRouter(prefix="/tickets", tags=["Tickets"])

ROLES_CREAN = ("member", "approver", "architect", "admin", "owner")
_TERMINALES = ("produccion", "cancelado", "cerrado_duplicado")


# ------------------------------------------------------------------ esquemas
class TicketIn(BaseModel):
    """Creación de bug/mejora (§9.2)."""
    tipo: str = Field(pattern=r"^(bug|mejora)$")
    titulo: str = Field(min_length=5, max_length=200)
    descripcion: str = Field(min_length=10, max_length=10_000)
    prioridad: str = Field(default="media", pattern=r"^(alta|media|baja)$")
    modulo: str | None = Field(default=None, max_length=200)
    adjuntos: list[str] = Field(default_factory=list, max_length=10)


class CriterioIn(BaseModel):
    given: str = Field(min_length=5, max_length=500)
    when: str = Field(min_length=5, max_length=500)
    then: str = Field(min_length=5, max_length=500)


class FeatureIn(BaseModel):
    """Creación de funcionalidad (§9.3): objetivo + criterios GWT."""
    nombre: str = Field(min_length=5, max_length=200)
    objetivo: str = Field(min_length=20, max_length=10_000)
    criterios: list[CriterioIn] = Field(default_factory=list, max_length=30)
    modulos_estimados: list[str] = Field(default_factory=list, max_length=20)
    fecha_objetivo: date | None = None
    adjuntos: list[str] = Field(default_factory=list, max_length=10)


class PreviewIn(BaseModel):
    texto: str = Field(max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class TicketPatch(BaseModel):
    titulo: str | None = Field(default=None, min_length=5, max_length=200)
    descripcion: str | None = Field(default=None, min_length=10, max_length=10_000)
    prioridad: str | None = Field(default=None, pattern=r"^(alta|media|baja)$")
    accion: str | None = Field(default=None, pattern=r"^cancelar$")
    motivo_cancelacion: str | None = Field(default=None, min_length=5, max_length=500)


class AdjuntoIn(BaseModel):
    nombre_archivo: str = Field(min_length=1, max_length=255)
    content_type: str
    tamano_bytes: int = Field(ge=1)


class WatcherIn(BaseModel):
    user_id: str


_TIPOS_ADJUNTO = {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4",
                  "video/webm", "application/pdf", "text/markdown", "text/plain",
                  "text/csv", "application/zip",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


# -------------------------------------------------------------------- helpers
async def _resolver_ticket(db, tenant_id: str, numero: str) -> dict:
    """Resuelve KC-#### dentro del tenant; RLS garantiza 404 para ajenos."""
    t = await uno(db, "SELECT * FROM core.tickets WHERE tenant_id=:t AND numero=:n",
                  {"t": tenant_id, "n": numero})
    if not t:
        raise no_encontrado("El ticket")
    return t


async def _validar_adjuntos(db, tenant_id: str, ids: list[str]) -> None:
    for a in ids:
        fila = await uno(db, """SELECT id FROM core.ticket_attachments
                                 WHERE id=:a AND tenant_id=:t AND ticket_id IS NULL""",
                         {"a": a, "t": tenant_id})
        if not fila:
            raise AppError("ADJUNTO_NO_ENCONTRADO", 404,
                           "Uno de los archivos adjuntos no existe o ya está "
                           "asociado a otro ticket.", {"adjunto_id": a})


async def _antisaturacion(usuario: UsuarioActual) -> None:
    r = comercial.redis_cliente()
    clave = f"tk:hora:{usuario.user_id}"
    n = await r.incr(clave)
    if n == 1:
        await r.expire(clave, 3600)
    if n > get_settings().max_tickets_hora_usuario:
        raise AppError("LIMITE_CREACION", 409,
                       "Has creado demasiados tickets en poco tiempo. Espera unos minutos.")


async def _crear_ticket(db, usuario: UsuarioActual, response: Response, *, tipo: str,
                        titulo: str, descripcion: str, prioridad: str = "media",
                        modulo: str | None = None, adjuntos: list[str] | None = None,
                        origen_ref: dict | None = None,
                        fecha_objetivo: date | None = None) -> dict:
    """Núcleo de creación con validación comercial QU-1 (§3.7)."""
    cuota = await comercial.estado_cuota_loc(db, usuario.tenant_id)
    encolado = cuota["umbral"] == "agotado"
    tid = nuevo("tk")
    fila = await uno(db, """
        INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, prioridad,
                                  etapa, origen, origen_ref, reportado_por,
                                  modulo_pista, fecha_objetivo)
        VALUES (:i, :t, :tipo, :ti, :d, :p, :etapa, 'portal', CAST(:oref AS jsonb),
                :u, :m, :f)
        RETURNING id, numero, etapa""",
        {"i": tid, "t": usuario.tenant_id, "tipo": tipo, "ti": titulo, "d": descripcion,
         "p": prioridad, "etapa": "en_cola_por_cuota" if encolado else "triage",
         "oref": json.dumps(origen_ref) if origen_ref else None,
         "u": usuario.user_id, "m": modulo, "f": fecha_objetivo})
    for a in adjuntos or []:
        await db.execute(text("UPDATE core.ticket_attachments SET ticket_id=:tk "
                              "WHERE id=:a"), {"tk": tid, "a": a})
    await registrar_evento(db, usuario.tenant_id, evento="ticket_creado",
                           resumen=f"Ticket creado ({tipo})", actor_tipo="humano",
                           actor_id=usuario.user_id, ticket_id=fila["numero"])
    if encolado:
        response.status_code = 202
        log.info("tickets.encolado_por_cuota", ticket_id=fila["numero"],
                 consumidas=cuota["consumidas"], limite=cuota["limite"])
    else:
        # publica a Triage (stream Redis que consume el orquestador)
        await comercial.redis_cliente().xadd("kc:triage", {
            "ticket_id": fila["numero"], "tenant_id": usuario.tenant_id})
    if cuota["umbral"] == "aviso_80":
        response.headers["X-Kallicode-Quota-Warning"] = f"loc={cuota['porcentaje']}%"
    log.info("tickets.creado", ticket_id=fila["numero"], tipo=tipo,
             prioridad=prioridad, adjuntos=len(adjuntos or []))
    return fila


# ------------------------------------------------------------------ endpoints
@router.get("", summary="Listar tickets")
async def listar(etapa: str | None = None, tipo: str | None = None,
                 prioridad: str | None = None, origen: str | None = None,
                 gate_pendiente: int | None = Query(default=None, ge=1, le=3),
                 q: str | None = Query(default=None, max_length=200),
                 page: int = Query(default=1, ge=1),
                 page_size: int = Query(default=25, ge=1, le=100),
                 usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Filtros combinables con AND; orden actualizado_en desc (§3.4)."""
    cond, params = ["tenant_id = :t"], {"t": usuario.tenant_id}
    for campo, v in (("etapa", etapa), ("tipo", tipo), ("prioridad", prioridad),
                     ("origen", origen), ("gate_pendiente", gate_pendiente)):
        if v is not None:
            cond.append(f"{campo} = :{campo}")
            params[campo] = v
    if q:
        cond.append("titulo ILIKE :q")
        params["q"] = f"%{q}%"
    where = " AND ".join(cond)
    async with sesion_de(usuario) as db:
        items = await todos(db, f"""
            SELECT numero AS id, titulo, tipo, prioridad, etapa, gate_pendiente,
                   origen, actualizado_en
              FROM core.tickets WHERE {where}
             ORDER BY actualizado_en DESC LIMIT :lim OFFSET :off""",
            {**params, "lim": page_size, "off": (page - 1) * page_size})
        total = (await uno(db, f"SELECT count(*) AS n FROM core.tickets WHERE {where}",
                           params))["n"]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201, summary="Crear ticket (bug / mejora)")
async def crear(datos: TicketIn, response: Response,
                usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN))) -> dict:
    """Entrada: TicketIn. Salida: {id (KC-####), etapa}.
    201 normal · 202 si la cuota está agotada (en_cola_por_cuota, QU-1).
    Errores: ADJUNTO_NO_ENCONTRADO (404) · LIMITE_CREACION (409)."""
    await _antisaturacion(usuario)
    async with sesion_de(usuario) as db:
        await _validar_adjuntos(db, usuario.tenant_id, datos.adjuntos)
        fila = await _crear_ticket(db, usuario, response, tipo=datos.tipo,
                                   titulo=datos.titulo, descripcion=datos.descripcion,
                                   prioridad=datos.prioridad, modulo=datos.modulo,
                                   adjuntos=datos.adjuntos)
    return {"id": fila["numero"], "etapa": fila["etapa"], "duplicado_potencial": None}


@router.post("/features", status_code=201, summary="Especificar nueva funcionalidad")
async def crear_feature(datos: FeatureIn, response: Response,
                        usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN))) -> dict:
    """Entrada: FeatureIn (objetivo + criterios GWT). Salida: {id, etapa,
    impacto_estimado}. Errores: FECHA_PASADA (422) + los de crear()."""
    if datos.fecha_objetivo and datos.fecha_objetivo < date.today():
        raise AppError("FECHA_PASADA", 422, "La fecha objetivo no puede ser anterior a hoy.")
    await _antisaturacion(usuario)
    descripcion = datos.objetivo
    if datos.criterios:
        gwt = "\n".join(f"- Dado {c.given}, cuando {c.when}, entonces {c.then}"
                        for c in datos.criterios)
        descripcion += f"\n\nCriterios de aceptación propuestos:\n{gwt}"
    async with sesion_de(usuario) as db:
        await _validar_adjuntos(db, usuario.tenant_id, datos.adjuntos)
        fila = await _crear_ticket(
            db, usuario, response, tipo="funcionalidad", titulo=datos.nombre,
            descripcion=descripcion, modulo=", ".join(datos.modulos_estimados) or None,
            adjuntos=datos.adjuntos, fecha_objetivo=datos.fecha_objetivo,
            origen_ref={"criterios": [c.model_dump() for c in datos.criterios]})
    return {"id": fila["numero"], "etapa": fila["etapa"],
            "impacto_estimado": {"modulos": datos.modulos_estimados, "tablas": []}}


@router.post("/dedup-preview", summary="Deduplicación en vivo")
async def dedup_preview(datos: PreviewIn,
                        usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN))) -> dict:
    """Busca los k tickets más similares por embedding (pgvector, coseno).
    Texto <10 caracteres => lista vacía (sin error). RL-2: 30 req/min.
    Error: BUSQUEDA_NO_DISPONIBLE (503) con degradación elegante."""
    await comercial.rl_typing(usuario.user_id)
    if len(datos.texto.strip()) < 10:
        return {"similares": []}
    try:
        emb = await _embeber(datos.texto)
    except Exception:
        log.warning("tickets.dedup_preview.embeddings_caidos")
        raise dependencia_no_disponible("embeddings")
    async with sesion_de(usuario) as db:
        similares = await todos(db, """
            SELECT t.numero AS ticket_id, t.titulo,
                   round((1 - (e.embedding <=> CAST(:emb AS vector)))::numeric, 2) AS similitud,
                   t.etapa, t.cerrado_en::date AS resuelto_en
              FROM vec.ticket_embeddings e JOIN core.tickets t ON t.id = e.ticket_id
             WHERE e.tenant_id = :t
             ORDER BY e.embedding <=> CAST(:emb AS vector) LIMIT :k""",
            {"t": usuario.tenant_id, "emb": str(emb), "k": datos.k})
    umbral = get_settings().dedup_umbral
    return {"similares": [s for s in similares if float(s["similitud"]) >= umbral * 0.7]}


@router.post("/impact-preview", summary="Impacto estimado en vivo")
async def impact_preview(datos: PreviewIn,
                         usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN))) -> dict:
    """Resuelve el texto contra las definiciones de CodeMapping (vec.definition_
    embeddings). Error: GRAFO_NO_DISPONIBLE (503)."""
    await comercial.rl_typing(usuario.user_id)
    if len(datos.texto.strip()) < 10:
        return {"modulos": [], "tablas": [], "confianza": 0}
    try:
        emb = await _embeber(datos.texto)
    except Exception:
        raise AppError("GRAFO_NO_DISPONIBLE", 503,
                       "El análisis de impacto no está disponible ahora mismo.")
    async with sesion_de(usuario) as db:
        filas = await todos(db, """
            SELECT nombre, capa, modulo,
                   1 - (embedding <=> CAST(:emb AS vector)) AS sim
              FROM vec.definition_embeddings WHERE tenant_id = :t
             ORDER BY embedding <=> CAST(:emb AS vector) LIMIT 8""",
            {"t": usuario.tenant_id, "emb": str(emb)})
    modulos = sorted({f["modulo"] for f in filas if f["modulo"] and f["sim"] > 0.5})
    tablas = sorted({f["nombre"] for f in filas if f["capa"] == "codigo"
                     and f["sim"] > 0.5 and "." not in f["nombre"]})[:5]
    conf = round(max((f["sim"] for f in filas), default=0), 2)
    return {"modulos": modulos, "tablas": tablas, "confianza": conf}


async def _embeber(texto: str) -> list[float]:
    """Llama al servicio BGE-M3 (endpoint OpenAI-compatible, D15)."""
    s = get_settings()
    async with httpx.AsyncClient(timeout=10) as cli:
        r = await cli.post(f"{s.embeddings_url}/v1/embeddings",
                           json={"model": "bge-m3", "input": texto})
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


@router.get("/{numero}", summary="Detalle de ticket")
async def detalle(numero: str, usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: cabecera + pipeline + detalle + visualizadores (§9.6)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        reporter = t["reportado_por"] and await uno(
            db, "SELECT id, nombre FROM core.users WHERE id=:i", {"i": t["reportado_por"]})
        watchers = await todos(db, """SELECT u.id, u.nombre FROM core.ticket_watchers w
                                       JOIN core.users u ON u.id = w.user_id
                                      WHERE w.ticket_id = :tk""", {"tk": t["id"]})
        job = await uno(db, """SELECT linea FROM core.jobs WHERE ticket_id=:tk
                                AND estado NOT IN ('produccion','cancelado','cerrado_duplicado')""",
                        {"tk": t["id"]})
    etapas = ["triage", "design", "build", "qa", "security", "deploy", "produccion"]
    idx = etapas.index(t["etapa"]) if t["etapa"] in etapas else -1
    pipeline = [{"etapa": e,
                 "estado": "done" if i < idx else ("actual" if i == idx else "pendiente"),
                 "gate": {1: "design", 2: "qa", 3: "deploy"}.get(t["gate_pendiente"]) == e
                         and t["gate_pendiente"] or None}
                for i, e in enumerate(etapas)]
    return {"id": t["numero"], "titulo": t["titulo"], "tipo": t["tipo"],
            "prioridad": t["prioridad"], "origen": t["origen"],
            "reportado_por": reporter, "etapa": t["etapa"],
            "gate_pendiente": t["gate_pendiente"], "pipeline": pipeline,
            "detalle": {"linea": job and job["linea"], "radio_impacto": t["impacto"],
                        "estimacion": None, "pr_url": t["pr_url"]},
            "visualizadores": watchers}


@router.patch("/{numero}", summary="Actualizar / cancelar ticket")
async def actualizar(numero: str, datos: TicketPatch,
                     usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Edición solo en Triage; cancelación hasta antes de Deploy (§9.7).
    Errores: TICKET_YA_EN_PIPELINE / CANCELACION_TARDIA (409) ·
    PERMISO_DENEGADO (403). La cancelación libera la línea (QU-2)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        es_admin = usuario.rol in ("admin", "owner")
        if not es_admin and t["reportado_por"] != usuario.user_id:
            raise AppError("PERMISO_DENEGADO", 403,
                           "Solo el creador o un administrador pueden modificar este ticket.")
        if datos.accion == "cancelar":
            if t["etapa"] in ("deploy", "produccion"):
                raise AppError("CANCELACION_TARDIA", 409,
                               "No se puede cancelar durante el despliegue. "
                               "Contacta soporte si es urgente.")
            if not datos.motivo_cancelacion:
                raise AppError("VALIDACION_ENTRADA", 422,
                               "Indica el motivo de la cancelación.")
            await db.execute(text("""UPDATE core.jobs SET estado='cancelado'
                                      WHERE ticket_id=:tk AND estado NOT IN
                                            ('produccion','cancelado','cerrado_duplicado')"""),
                             {"tk": t["id"]})
            await db.execute(text("""UPDATE core.tickets SET etapa='cancelado',
                                            gate_pendiente=NULL, motivo_cancelacion=:m,
                                            cancelado_por=:u WHERE id=:tk"""),
                             {"m": datos.motivo_cancelacion, "u": usuario.user_id,
                              "tk": t["id"]})
            await registrar_evento(db, usuario.tenant_id, evento="ticket_cancelado",
                                   resumen=f"Cancelado: {datos.motivo_cancelacion}",
                                   actor_tipo="humano", actor_id=usuario.user_id,
                                   ticket_id=numero)
            log.info("tickets.cancelado", ticket_id=numero)
            return {"id": numero, "etapa": "cancelado",
                    "cancelado_por": usuario.user_id, "motivo": datos.motivo_cancelacion}
        # edición de contenido: solo en triage / en_cola_por_cuota
        cambios = {k: v for k, v in datos.model_dump(exclude_none=True).items()
                   if k in ("titulo", "descripcion", "prioridad")}
        if cambios and t["etapa"] not in ("triage", "en_cola_por_cuota"):
            raise AppError("TICKET_YA_EN_PIPELINE", 409,
                           f"El ticket ya está en {t['etapa']}: los cambios de alcance "
                           "se hacen como comentarios en el gate correspondiente.")
        for campo, v in cambios.items():
            await db.execute(text(f"UPDATE core.tickets SET {campo}=:v WHERE id=:tk"),
                             {"v": v, "tk": t["id"]})
        if cambios:
            await registrar_evento(db, usuario.tenant_id, evento="ticket_modificado",
                                   resumen=f"Editado: {', '.join(cambios)}",
                                   actor_tipo="humano", actor_id=usuario.user_id,
                                   ticket_id=numero)
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
    return {"id": t["numero"], "titulo": t["titulo"], "prioridad": t["prioridad"],
            "etapa": t["etapa"]}


# ------------------------------------------------------------------- adjuntos
@router.post("/attachments", status_code=201, summary="Subir adjunto (URL firmada)")
async def presign(datos: AdjuntoIn,
                  usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN))) -> dict:
    """Devuelve upload_url firmada (15 min); el adjunto queda pendiente hasta
    asociarse en la creación del ticket. Errores: TIPO_NO_PERMITIDO (415) ·
    ADJUNTO_DEMASIADO_GRANDE (413) · ALMACENAMIENTO_AGOTADO (402, QU-3)."""
    s = get_settings()
    if datos.content_type not in _TIPOS_ADJUNTO:
        raise AppError("TIPO_NO_PERMITIDO", 415,
                       "Tipo de archivo no permitido. Formatos aceptados: imágenes, "
                       "video, PDF, DOCX, MD, TXT, CSV, ZIP.")
    if datos.tamano_bytes > s.max_adjunto_bytes:
        raise AppError("ADJUNTO_DEMASIADO_GRANDE", 413,
                       "El archivo supera el máximo de 25 MB.")
    async with sesion_de(usuario) as db:
        await comercial.verificar_storage(db, usuario.tenant_id, datos.tamano_bytes)
        aid = nuevo("att")
        ref = f"{usuario.tenant_id}/adjuntos/{aid}/{datos.nombre_archivo.replace('/', '_')}"
        await db.execute(text("""
            INSERT INTO core.ticket_attachments (id, tenant_id, nombre_archivo,
                                                 content_type, tamano_bytes, blob_ref,
                                                 subido_por)
            VALUES (:i, :t, :n, :ct, :tb, :ref, :u)"""),
            {"i": aid, "t": usuario.tenant_id, "n": datos.nombre_archivo,
             "ct": datos.content_type, "tb": datos.tamano_bytes, "ref": ref,
             "u": usuario.user_id})
    url, expira = blob.url_subida(ref, datos.content_type)
    log.info("attachments.presign", adjunto_id=aid, tamano=datos.tamano_bytes)
    return {"adjunto_id": aid, "upload_url": url, "expira_en": expira}


@router.get("/{numero}/attachments/{adjunto_id}", summary="Descargar adjunto")
async def descargar(numero: str, adjunto_id: str,
                    usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Devuelve download_url firmada (10 min). Errores: ADJUNTO_NO_ENCONTRADO
    (404) · ADJUNTO_BLOQUEADO (423, antimalware). El acceso queda auditado."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        a = await uno(db, """SELECT * FROM core.ticket_attachments
                              WHERE id=:a AND ticket_id=:tk""",
                      {"a": adjunto_id, "tk": t["id"]})
        if not a:
            raise no_encontrado("El archivo")
        if a["escaneo"] == "infectado":
            raise AppError("ADJUNTO_BLOQUEADO", 423,
                           "El archivo fue bloqueado por el análisis de seguridad.")
    url, expira = blob.url_descarga(a["blob_ref"])
    log.info("attachments.descarga", adjunto_id=adjunto_id, ticket_id=numero)
    return {"download_url": url, "expira_en": expira}


# ------------------------------------------------------------------- pestañas
@router.get("/{numero}/spec", summary="Spec del ticket")
async def spec(numero: str, version: int | None = Query(default=None, ge=1),
               usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: versión + alternativas + criterios + scope_paths (§9.10).
    Error: SPEC_NO_DISPONIBLE (404) si el ticket no pasó por Diseño."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        cond = "ticket_id = :tk" + (" AND version = :v" if version else "")
        sp = await uno(db, f"""SELECT * FROM core.specs WHERE {cond}
                                ORDER BY version DESC LIMIT 1""",
                       {"tk": t["id"], **({"v": version} if version else {})})
        if not sp:
            raise AppError("SPEC_NO_DISPONIBLE", 404,
                           f"Este ticket aún no tiene spec: está en {t['etapa']}.")
        alts = await todos(db, """SELECT alt_id AS id, titulo, descripcion, riesgo,
                                         recomendada, elegida
                                    FROM core.spec_alternatives WHERE spec_id=:s
                                    ORDER BY alt_id""", {"s": sp["id"]})
        crits = await todos(db, """SELECT criterio_id AS id, given_txt AS given,
                                          when_txt AS "when", then_txt AS "then"
                                     FROM core.spec_criteria WHERE spec_id=:s
                                     ORDER BY criterio_id""", {"s": sp["id"]})
    return {"version": sp["version"], "estado": sp["estado"], "alternativas": alts,
            "criterios": crits, "scope_paths": sp["scope_paths"],
            "adr_borrador": sp["adr_borrador"], "estimacion_loc": sp["estimacion_loc"]}


@router.get("/{numero}/evidence", summary="Evidencia QA")
async def evidence(numero: str, corrida: int | None = Query(default=None, ge=1),
                   usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: matriz criterio a criterio + regresión (§9.11).
    Error: EVIDENCIA_NO_DISPONIBLE (404)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        cond = "ticket_id = :tk" + (" AND corrida = :c" if corrida else "")
        run = await uno(db, f"""SELECT * FROM core.qa_runs WHERE {cond}
                                 ORDER BY corrida DESC LIMIT 1""",
                        {"tk": t["id"], **({"c": corrida} if corrida else {})})
        if not run:
            raise AppError("EVIDENCIA_NO_DISPONIBLE", 404,
                           "Este ticket aún no tiene corridas de QA.")
        matriz = await todos(db, """SELECT criterio_id, resultado, extracto_fallo,
                                           veredicto_visual, evidencias
                                      FROM core.qa_results WHERE qa_run_id=:r
                                      ORDER BY criterio_id""", {"r": run["id"]})
        log.info("evidence.consultada", ticket_id=numero, corrida=run["corrida"])
    return {"corrida": run["corrida"], "ejecutada_en": run["ejecutada_en"],
            "matriz": matriz, "regresion": run["regresion"], "flaky": run["flaky"]}


@router.get("/{numero}/security", summary="Hallazgos de seguridad")
async def security(numero: str, usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: hallazgos + bloquea_deploy (§9.12).
    Error: SECURITY_NO_DISPONIBLE (404)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        hallazgos = await todos(db, """SELECT id, severidad, titulo, origen, estado,
                                              justificacion
                                         FROM core.security_findings
                                        WHERE ticket_id=:tk ORDER BY creado_en""",
                                {"tk": t["id"]})
    if not hallazgos and t["etapa"] in ("triage", "design", "build", "qa",
                                        "en_cola_por_cuota"):
        raise AppError("SECURITY_NO_DISPONIBLE", 404,
                       "Este ticket aún no pasó por la etapa de seguridad.")
    bloquea = any(h["estado"] == "abierto" and h["severidad"] in ("alta", "critica")
                  for h in hallazgos)
    return {"hallazgos": hallazgos, "bloquea_deploy": bloquea}


@router.get("/{numero}/usage", summary="Consumo del ticket")
async def usage(numero: str, usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: totales + por_funcion + desglose_loc (§9.13).
    Sin consumo devuelve totales a cero (no es error)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        por_funcion = await todos(db, """
            SELECT etapa AS funcion, modelo, sum(invocaciones)::int AS invocaciones,
                   sum(tokens_in)::bigint AS tokens_in, sum(tokens_out)::bigint AS tokens_out,
                   avg(duracion_ms)::int AS duracion_media_ms
              FROM core.usage_tokens WHERE ticket_id=:tk
             GROUP BY etapa, modelo ORDER BY etapa""", {"tk": t["id"]})
        loc = await uno(db, """SELECT coalesce(sum(anadidas),0)::int AS anadidas,
                                      coalesce(sum(eliminadas),0)::int AS eliminadas,
                                      coalesce(sum(netas),0)::int AS netas,
                                      coalesce(sum(tests),0)::int AS tests
                                 FROM core.usage_loc WHERE ticket_id=:tk AND tipo='pr'""",
                        {"tk": t["id"]})
    totales = {"tokens_in": sum(f["tokens_in"] for f in por_funcion),
               "tokens_out": sum(f["tokens_out"] for f in por_funcion),
               "invocaciones": sum(f["invocaciones"] for f in por_funcion),
               "loc_netas": loc["netas"], "loc_tests": loc["tests"]}
    return {"totales": totales, "por_funcion": por_funcion, "desglose_loc": loc}


@router.get("/{numero}/activity", summary="Actividad del ticket")
async def activity(numero: str, page: int = Query(default=1, ge=1),
                   page_size: int = Query(default=25, ge=1, le=100),
                   usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Cronología humanos+agentes (vista de auditoría filtrada, §9.14)."""
    async with sesion_de(usuario) as db:
        await _resolver_ticket(db, usuario.tenant_id, numero)
        items = await todos(db, """
            SELECT a.creado_en AS fecha, a.actor_tipo, a.actor_id, a.resumen AS evento,
                   a.etapa, u.nombre AS actor_nombre
              FROM audit.audit_events a
              LEFT JOIN core.users u ON u.id = a.actor_id
             WHERE a.tenant_id = :t AND a.ticket_id = :n
             ORDER BY a.id DESC LIMIT :lim OFFSET :off""",
            {"t": usuario.tenant_id, "n": numero,
             "lim": page_size, "off": (page - 1) * page_size})
    for i in items:
        i["actor"] = {"tipo": i.pop("actor_tipo"),
                      "nombre": i.pop("actor_nombre") or i.pop("actor_id") or "sistema"}
        i.pop("actor_id", None)
    return {"items": items, "page": page, "page_size": page_size}


# ------------------------------------------------------------------- watchers
@router.post("/{numero}/watchers", summary="Añadir visualizador")
async def add_watcher(numero: str, datos: WatcherIn,
                      usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN))) -> dict:
    """Idempotente. Errores: USUARIO_NO_ENCONTRADO (404) ·
    LIMITE_VISUALIZADORES (409, máx. 25)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        u = await uno(db, "SELECT id FROM core.users WHERE id=:i AND estado='activo'",
                      {"i": datos.user_id})
        if not u:
            raise AppError("USUARIO_NO_ENCONTRADO", 404,
                           "El usuario no existe en tu organización.")
        n = (await uno(db, "SELECT count(*) AS n FROM core.ticket_watchers "
                           "WHERE ticket_id=:tk", {"tk": t["id"]}))["n"]
        if n >= 25:
            raise AppError("LIMITE_VISUALIZADORES", 409,
                           "Máximo 25 visualizadores por ticket.")
        await db.execute(text("""INSERT INTO core.ticket_watchers (tenant_id, ticket_id, user_id)
                                 VALUES (:t, :tk, :u) ON CONFLICT DO NOTHING"""),
                         {"t": usuario.tenant_id, "tk": t["id"], "u": datos.user_id})
        watchers = await todos(db, """SELECT u.id, u.nombre FROM core.ticket_watchers w
                                       JOIN core.users u ON u.id = w.user_id
                                      WHERE w.ticket_id=:tk""", {"tk": t["id"]})
    return {"visualizadores": watchers}


@router.delete("/{numero}/watchers/{user_id}", summary="Quitar visualizador")
async def del_watcher(numero: str, user_id: str,
                      usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Errores: PERMISO_DENEGADO (403, terceros) · CREADOR_OBLIGATORIO (409,
    el trigger T10 protege al creador)."""
    async with sesion_de(usuario) as db:
        t = await _resolver_ticket(db, usuario.tenant_id, numero)
        if user_id != usuario.user_id and usuario.rol not in ("admin", "owner") \
                and t["reportado_por"] != usuario.user_id:
            raise AppError("PERMISO_DENEGADO", 403,
                           "No puedes quitar a otros visualizadores.")
        try:
            await db.execute(text("""DELETE FROM core.ticket_watchers
                                      WHERE ticket_id=:tk AND user_id=:u"""),
                             {"tk": t["id"], "u": user_id})
        except Exception:
            raise AppError("CREADOR_OBLIGATORIO", 409,
                           "El creador del ticket siempre recibe las notificaciones "
                           "de cierre.")
        watchers = await todos(db, """SELECT u.id, u.nombre FROM core.ticket_watchers w
                                       JOIN core.users u ON u.id = w.user_id
                                      WHERE w.ticket_id=:tk""", {"tk": t["id"]})
    return {"visualizadores": watchers}
