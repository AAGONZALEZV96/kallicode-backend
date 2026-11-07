"""Tickets, funcionalidades, adjuntos y visualizadores."""

from __future__ import annotations

import json
from datetime import date

import httpx
from fastapi import APIRouter, Depends, Query, Response
from kallicode_core import adjuntos as servicio_adjuntos
from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.config import get_settings
from kallicode_core.db import todos, uno
from kallicode_core.errors import (
    AppError,
    dependencia_no_disponible,
    no_encontrado,
)
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from kallicode_core.vectores import configurar_busqueda
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual
from ..servicios import blob

router = APIRouter(prefix="/tickets", tags=["Tickets"])

ROLES_CREAN = ("member", "approver", "architect", "admin", "owner")
_TERMINALES = ("produccion", "cancelado", "cerrado_duplicado")


class TicketIn(BaseModel):
    tipo: str = Field(pattern=r"^(bug|mejora)$")
    titulo: str = Field(min_length=5, max_length=200)
    descripcion: str = Field(min_length=10, max_length=10_000)
    prioridad: str = Field(
        default="media",
        pattern=r"^(alta|media|baja)$",
    )
    modulo: str | None = Field(default=None, max_length=200)
    adjuntos: list[str] = Field(default_factory=list, max_length=10)


class CriterioIn(BaseModel):
    given: str = Field(min_length=5, max_length=500)
    when: str = Field(min_length=5, max_length=500)
    then: str = Field(min_length=5, max_length=500)


class FeatureIn(BaseModel):
    nombre: str = Field(min_length=5, max_length=200)
    objetivo: str = Field(min_length=20, max_length=10_000)
    criterios: list[CriterioIn] = Field(
        default_factory=list,
        max_length=30,
    )
    modulos_estimados: list[str] = Field(
        default_factory=list,
        max_length=20,
    )
    fecha_objetivo: date | None = None
    adjuntos: list[str] = Field(default_factory=list, max_length=10)


class PreviewIn(BaseModel):
    texto: str = Field(max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class TicketPatch(BaseModel):
    titulo: str | None = Field(
        default=None,
        min_length=5,
        max_length=200,
    )
    descripcion: str | None = Field(
        default=None,
        min_length=10,
        max_length=10_000,
    )
    prioridad: str | None = Field(
        default=None,
        pattern=r"^(alta|media|baja)$",
    )
    accion: str | None = Field(
        default=None,
        pattern=r"^cancelar$",
    )
    motivo_cancelacion: str | None = Field(
        default=None,
        min_length=5,
        max_length=500,
    )


class AdjuntoIn(BaseModel):
    nombre_archivo: str = Field(min_length=1, max_length=255)
    content_type: str
    tamano_bytes: int = Field(ge=1)


class WatcherIn(BaseModel):
    user_id: str


_TIPOS_ADJUNTO = servicio_adjuntos.TIPOS_ADJUNTO

_RESPUESTA_BLOQUEADO = {
    "description": "Adjunto pendiente o bloqueado por antimalware.",
    "content": {
        "application/json": {
            "schema": {
                "$ref": "#/components/schemas/ErrorEnvelope",
            },
        },
    },
}


async def _resolver_ticket(
    db,
    tenant_id: str,
    numero: str,
) -> dict:
    ticket = await uno(
        db,
        """
        SELECT *
          FROM core.tickets
         WHERE tenant_id = :t AND numero = :n
        """,
        {"t": tenant_id, "n": numero},
    )

    if not ticket:
        raise no_encontrado("El ticket")

    return ticket


async def _validar_adjuntos(
    db,
    tenant_id: str,
    ids: list[str],
) -> None:
    for adjunto_id in sorted(set(ids)):
        fila = await uno(
            db,
            """
            SELECT id
              FROM core.ticket_attachments
             WHERE id = :a
               AND tenant_id = :t
               AND ticket_id IS NULL
               AND confirmado = true
               AND escaneo = 'limpio'
             FOR UPDATE
            """,
            {"a": adjunto_id, "t": tenant_id},
        )

        if not fila:
            raise AppError(
                "ADJUNTO_NO_ENCONTRADO",
                404,
                "El adjunto no está confirmado, no está disponible "
                "o ya está asociado a otro ticket.",
                {"adjunto_id": adjunto_id},
            )


async def _antisaturacion(usuario: UsuarioActual) -> None:
    redis = comercial.redis_cliente()
    clave = f"tk:hora:{usuario.user_id}"
    cantidad = await redis.incr(clave)

    if cantidad == 1:
        await redis.expire(clave, 3600)

    if cantidad > get_settings().max_tickets_hora_usuario:
        raise AppError(
            "LIMITE_CREACION",
            409,
            "Has creado demasiados tickets en poco tiempo. Espera unos minutos.",
        )


async def _crear_ticket(
    db,
    usuario: UsuarioActual,
    response: Response,
    *,
    tipo: str,
    titulo: str,
    descripcion: str,
    prioridad: str = "media",
    modulo: str | None = None,
    adjuntos: list[str] | None = None,
    origen_ref: dict | None = None,
    fecha_objetivo: date | None = None,
) -> dict:
    cuota = await comercial.estado_cuota_loc(
        db,
        usuario.tenant_id,
    )
    encolado = cuota["umbral"] == "agotado"
    ticket_id = nuevo("tk")

    fila = await uno(
        db,
        """
        INSERT INTO core.tickets (
            id, tenant_id, tipo, titulo, descripcion, prioridad,
            etapa, origen, origen_ref, reportado_por,
            modulo_pista, fecha_objetivo
        )
        VALUES (
            :i, :t, :tipo, :ti, :d, :p,
            :etapa, 'portal', CAST(:oref AS jsonb), :u, :m, :f
        )
        RETURNING id, numero, etapa
        """,
        {
            "i": ticket_id,
            "t": usuario.tenant_id,
            "tipo": tipo,
            "ti": titulo,
            "d": descripcion,
            "p": prioridad,
            "etapa": "en_cola_por_cuota" if encolado else "triage",
            "oref": json.dumps(origen_ref) if origen_ref else None,
            "u": usuario.user_id,
            "m": modulo,
            "f": fecha_objetivo,
        },
    )

    for adjunto_id in set(adjuntos or []):
        await db.execute(
            text("""
                UPDATE core.ticket_attachments
                   SET ticket_id = :tk
                 WHERE id = :a AND tenant_id = :t
            """),
            {
                "tk": ticket_id,
                "a": adjunto_id,
                "t": usuario.tenant_id,
            },
        )

    await registrar_evento(
        db,
        usuario.tenant_id,
        evento="ticket_creado",
        resumen=f"Ticket creado ({tipo})",
        actor_tipo="humano",
        actor_id=usuario.user_id,
        ticket_id=fila["numero"],
    )

    if encolado:
        response.status_code = 202

        log.info(
            "tickets.encolado_por_cuota",
            ticket_id=fila["numero"],
            consumidas=cuota["consumidas"],
            limite=cuota["limite"],
        )
    else:
        await comercial.redis_cliente().xadd(
            "kc:triage",
            {
                "ticket_id": fila["numero"],
                "tenant_id": usuario.tenant_id,
            },
        )

    if cuota["umbral"] == "aviso_80":
        response.headers["X-Kallicode-Quota-Warning"] = f"loc={cuota['porcentaje']}%"

    log.info(
        "tickets.creado",
        ticket_id=fila["numero"],
        tipo=tipo,
        prioridad=prioridad,
        adjuntos=len(adjuntos or []),
    )

    return fila


@router.get("", summary="Listar tickets")
async def listar(
    etapa: str | None = None,
    tipo: str | None = None,
    prioridad: str | None = None,
    origen: str | None = None,
    gate_pendiente: int | None = Query(default=None, ge=1, le=3),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    condiciones = ["tenant_id = :t"]
    parametros = {"t": usuario.tenant_id}

    for campo, valor in (
        ("etapa", etapa),
        ("tipo", tipo),
        ("prioridad", prioridad),
        ("origen", origen),
        ("gate_pendiente", gate_pendiente),
    ):
        if valor is not None:
            condiciones.append(f"{campo} = :{campo}")
            parametros[campo] = valor

    if q:
        condiciones.append("titulo ILIKE :q")
        parametros["q"] = f"%{q}%"

    where = " AND ".join(condiciones)

    async with sesion_de(usuario) as db:
        items = await todos(
            db,
            f"""
            SELECT numero AS id, titulo, tipo, prioridad, etapa,
                   gate_pendiente, origen, actualizado_en
              FROM core.tickets
             WHERE {where}
             ORDER BY actualizado_en DESC
             LIMIT :lim OFFSET :off
            """,
            {
                **parametros,
                "lim": page_size,
                "off": (page - 1) * page_size,
            },
        )

        total = (
            await uno(
                db,
                f"SELECT count(*) AS n FROM core.tickets WHERE {where}",
                parametros,
            )
        )["n"]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "",
    status_code=201,
    summary="Crear ticket (bug / mejora)",
)
async def crear(
    datos: TicketIn,
    response: Response,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    await _antisaturacion(usuario)

    async with sesion_de(usuario) as db:
        await _validar_adjuntos(
            db,
            usuario.tenant_id,
            datos.adjuntos,
        )

        fila = await _crear_ticket(
            db,
            usuario,
            response,
            tipo=datos.tipo,
            titulo=datos.titulo,
            descripcion=datos.descripcion,
            prioridad=datos.prioridad,
            modulo=datos.modulo,
            adjuntos=datos.adjuntos,
        )

    return {
        "id": fila["numero"],
        "etapa": fila["etapa"],
        "duplicado_potencial": None,
    }


@router.post(
    "/features",
    status_code=201,
    summary="Especificar nueva funcionalidad",
)
async def crear_feature(
    datos: FeatureIn,
    response: Response,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    if datos.fecha_objetivo and datos.fecha_objetivo < date.today():
        raise AppError(
            "FECHA_PASADA",
            422,
            "La fecha objetivo no puede ser anterior a hoy.",
        )

    await _antisaturacion(usuario)
    descripcion = datos.objetivo

    if datos.criterios:
        gwt = "\n".join(
            f"- Dado {criterio.given}, cuando {criterio.when}, entonces {criterio.then}"
            for criterio in datos.criterios
        )
        descripcion += f"\n\nCriterios de aceptación propuestos:\n{gwt}"

    async with sesion_de(usuario) as db:
        await _validar_adjuntos(
            db,
            usuario.tenant_id,
            datos.adjuntos,
        )

        fila = await _crear_ticket(
            db,
            usuario,
            response,
            tipo="funcionalidad",
            titulo=datos.nombre,
            descripcion=descripcion,
            modulo=", ".join(datos.modulos_estimados) or None,
            adjuntos=datos.adjuntos,
            fecha_objetivo=datos.fecha_objetivo,
            origen_ref={
                "criterios": [criterio.model_dump() for criterio in datos.criterios],
            },
        )

    return {
        "id": fila["numero"],
        "etapa": fila["etapa"],
        "impacto_estimado": {
            "modulos": datos.modulos_estimados,
            "tablas": [],
        },
    }


@router.post("/dedup-preview", summary="Deduplicación en vivo")
async def dedup_preview(
    datos: PreviewIn,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    await comercial.rl_typing(
        usuario.tenant_id,
        usuario.user_id,
    )

    if len(datos.texto.strip()) < 10:
        return {"similares": []}

    try:
        embedding = await _embeber(datos.texto)
    except Exception:
        log.warning("tickets.dedup_preview.embeddings_caidos")
        raise dependencia_no_disponible("embeddings")

    async with sesion_de(usuario) as db:
        await configurar_busqueda(db, "dedup")

        similares = await todos(
            db,
            """
            WITH cand AS MATERIALIZED (
                SELECT e.ticket_id,
                       e.embedding <=> CAST(:emb AS vector) AS dist
                  FROM vec.ticket_embeddings e
                 WHERE e.tenant_id = :t
                 ORDER BY e.embedding <=> CAST(:emb AS vector)
                 LIMIT :k
            )
            SELECT t.numero AS ticket_id,
                   t.titulo,
                   round((1 - c.dist)::numeric, 2) AS similitud,
                   t.etapa,
                   t.cerrado_en::date AS resuelto_en
              FROM cand c
              JOIN core.tickets t ON t.id = c.ticket_id
             ORDER BY c.dist + 0
            """,
            {
                "t": usuario.tenant_id,
                "emb": str(embedding),
                "k": datos.k,
            },
        )

    umbral = get_settings().dedup_umbral

    return {
        "similares": [
            similar for similar in similares if float(similar["similitud"]) >= umbral * 0.7
        ],
    }


@router.post("/impact-preview", summary="Impacto estimado en vivo")
async def impact_preview(
    datos: PreviewIn,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    await comercial.rl_typing(
        usuario.tenant_id,
        usuario.user_id,
    )

    if len(datos.texto.strip()) < 10:
        return {
            "modulos": [],
            "tablas": [],
            "confianza": 0,
        }

    try:
        embedding = await _embeber(datos.texto)
    except Exception:
        raise AppError(
            "GRAFO_NO_DISPONIBLE",
            503,
            "El análisis de impacto no está disponible ahora mismo.",
        )

    async with sesion_de(usuario) as db:
        await configurar_busqueda(db, "diseno")

        filas = await todos(
            db,
            """
            WITH cand AS MATERIALIZED (
                SELECT nombre,
                       capa,
                       modulo,
                       embedding <=> CAST(:emb AS vector) AS dist
                  FROM vec.definition_embeddings
                 WHERE tenant_id = :t
                 ORDER BY embedding <=> CAST(:emb AS vector)
                 LIMIT 8
            )
            SELECT nombre, capa, modulo, 1 - dist AS sim
              FROM cand
             ORDER BY dist + 0
            """,
            {
                "t": usuario.tenant_id,
                "emb": str(embedding),
            },
        )

    modulos = sorted({fila["modulo"] for fila in filas if fila["modulo"] and fila["sim"] > 0.5})

    tablas = sorted(
        {
            fila["nombre"]
            for fila in filas
            if (fila["capa"] == "symbol" and fila["sim"] > 0.5 and "." not in fila["nombre"])
        }
    )[:5]

    confianza = round(
        max((fila["sim"] for fila in filas), default=0),
        2,
    )

    return {
        "modulos": modulos,
        "tablas": tablas,
        "confianza": confianza,
    }


async def _embeber(texto: str) -> list[float]:
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10) as cliente:
        respuesta = await cliente.post(
            f"{settings.embeddings_url}/v1/embeddings",
            json={
                "model": "bge-m3",
                "input": texto,
            },
        )
        respuesta.raise_for_status()

        return respuesta.json()["data"][0]["embedding"]


@router.get("/{numero}", summary="Detalle de ticket")
async def detalle(
    numero: str,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        reporter = ticket["reportado_por"] and await uno(
            db,
            "SELECT id, nombre FROM core.users WHERE id = :i",
            {"i": ticket["reportado_por"]},
        )

        watchers = await todos(
            db,
            """
            SELECT u.id, u.nombre
              FROM core.ticket_watchers w
              JOIN core.users u ON u.id = w.user_id
             WHERE w.ticket_id = :tk
            """,
            {"tk": ticket["id"]},
        )

        job = await uno(
            db,
            """
            SELECT linea
              FROM core.jobs
             WHERE ticket_id = :tk
               AND estado NOT IN (
                   'produccion', 'cancelado', 'cerrado_duplicado'
               )
            """,
            {"tk": ticket["id"]},
        )

    etapas = [
        "triage",
        "design",
        "build",
        "qa",
        "security",
        "deploy",
        "produccion",
    ]
    indice = etapas.index(ticket["etapa"]) if ticket["etapa"] in etapas else -1

    pipeline = [
        {
            "etapa": etapa,
            "estado": (
                "done" if posicion < indice else "actual" if posicion == indice else "pendiente"
            ),
            "gate": (
                {
                    1: "design",
                    2: "qa",
                    3: "deploy",
                }.get(ticket["gate_pendiente"])
                == etapa
                and ticket["gate_pendiente"]
            )
            or None,
        }
        for posicion, etapa in enumerate(etapas)
    ]

    return {
        "id": ticket["numero"],
        "titulo": ticket["titulo"],
        "tipo": ticket["tipo"],
        "prioridad": ticket["prioridad"],
        "origen": ticket["origen"],
        "reportado_por": reporter,
        "etapa": ticket["etapa"],
        "gate_pendiente": ticket["gate_pendiente"],
        "pipeline": pipeline,
        "detalle": {
            "linea": job and job["linea"],
            "radio_impacto": ticket["impacto"],
            "estimacion": None,
            "pr_url": ticket["pr_url"],
        },
        "visualizadores": watchers,
    }


@router.patch("/{numero}", summary="Actualizar / cancelar ticket")
async def actualizar(
    numero: str,
    datos: TicketPatch,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        es_admin = usuario.rol in ("admin", "owner")

        if not es_admin and ticket["reportado_por"] != usuario.user_id:
            raise AppError(
                "PERMISO_DENEGADO",
                403,
                "Solo el creador o un administrador pueden modificar este ticket.",
            )

        if datos.accion == "cancelar":
            if ticket["etapa"] in ("deploy", "produccion"):
                raise AppError(
                    "CANCELACION_TARDIA",
                    409,
                    "No se puede cancelar durante el despliegue. Contacta soporte si es urgente.",
                )

            if not datos.motivo_cancelacion:
                raise AppError(
                    "VALIDACION_ENTRADA",
                    422,
                    "Indica el motivo de la cancelación.",
                )

            await db.execute(
                text("""
                    UPDATE core.jobs
                       SET estado = 'cancelado'
                     WHERE ticket_id = :tk
                       AND estado NOT IN (
                           'produccion', 'cancelado', 'cerrado_duplicado'
                       )
                """),
                {"tk": ticket["id"]},
            )

            await db.execute(
                text("""
                    UPDATE core.tickets
                       SET etapa = 'cancelado',
                           gate_pendiente = NULL,
                           motivo_cancelacion = :m,
                           cancelado_por = :u
                     WHERE id = :tk
                """),
                {
                    "m": datos.motivo_cancelacion,
                    "u": usuario.user_id,
                    "tk": ticket["id"],
                },
            )

            await registrar_evento(
                db,
                usuario.tenant_id,
                evento="ticket_cancelado",
                resumen=f"Cancelado: {datos.motivo_cancelacion}",
                actor_tipo="humano",
                actor_id=usuario.user_id,
                ticket_id=numero,
            )

            log.info("tickets.cancelado", ticket_id=numero)

            return {
                "id": numero,
                "etapa": "cancelado",
                "cancelado_por": usuario.user_id,
                "motivo": datos.motivo_cancelacion,
            }

        cambios = {
            campo: valor
            for campo, valor in datos.model_dump(exclude_none=True).items()
            if campo in ("titulo", "descripcion", "prioridad")
        }

        if cambios and ticket["etapa"] not in (
            "triage",
            "en_cola_por_cuota",
        ):
            raise AppError(
                "TICKET_YA_EN_PIPELINE",
                409,
                f"El ticket ya está en {ticket['etapa']}: "
                "los cambios de alcance se hacen como comentarios "
                "en el gate correspondiente.",
            )

        for campo, valor in cambios.items():
            # campo está limitado por la whitelist anterior.
            await db.execute(
                text(f"UPDATE core.tickets SET {campo} = :v WHERE id = :tk"),
                {"v": valor, "tk": ticket["id"]},
            )

        if cambios:
            await registrar_evento(
                db,
                usuario.tenant_id,
                evento="ticket_modificado",
                resumen=f"Editado: {', '.join(cambios)}",
                actor_tipo="humano",
                actor_id=usuario.user_id,
                ticket_id=numero,
            )

        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

    return {
        "id": ticket["numero"],
        "titulo": ticket["titulo"],
        "prioridad": ticket["prioridad"],
        "etapa": ticket["etapa"],
    }


@router.post(
    "/attachments",
    status_code=201,
    summary="Subir adjunto (URL firmada)",
)
async def presign(
    datos: AdjuntoIn,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    settings = get_settings()

    if datos.content_type not in _TIPOS_ADJUNTO:
        raise AppError(
            "TIPO_NO_PERMITIDO",
            415,
            "Tipo de archivo no permitido. Formatos aceptados: "
            "imágenes, video, PDF, DOCX, MD, TXT, CSV, ZIP.",
        )

    if datos.tamano_bytes > settings.max_adjunto_bytes:
        raise AppError(
            "ADJUNTO_DEMASIADO_GRANDE",
            413,
            "El archivo supera el máximo de 25 MB.",
        )

    async with sesion_de(usuario) as db:
        await comercial.bloquear_storage(
            db,
            usuario.tenant_id,
        )

        aviso = await comercial.verificar_storage(
            db,
            usuario.tenant_id,
            datos.tamano_bytes,
        )

        adjunto_id = nuevo("att")
        nombre_seguro = datos.nombre_archivo.replace("/", "_")
        ref = f"{usuario.tenant_id}/adjuntos/{adjunto_id}/{nombre_seguro}"

        url, expira = blob.url_subida(
            ref,
            datos.content_type,
        )

        await db.execute(
            text("""
                INSERT INTO core.ticket_attachments (
                    id, tenant_id, nombre_archivo,
                    content_type, tamano_bytes, blob_ref,
                    subido_por
                )
                VALUES (:i, :t, :n, :ct, :tb, :ref, :u)
            """),
            {
                "i": adjunto_id,
                "t": usuario.tenant_id,
                "n": datos.nombre_archivo,
                "ct": datos.content_type,
                "tb": datos.tamano_bytes,
                "ref": ref,
                "u": usuario.user_id,
            },
        )

    log.info(
        "attachments.presign",
        adjunto_id=adjunto_id,
        tamano=datos.tamano_bytes,
    )

    return {
        "adjunto_id": adjunto_id,
        "upload_url": url,
        "expira_en": expira,
        "aviso": aviso,
    }


@router.post(
    "/attachments/{adjunto_id}/confirm",
    summary="Confirmar y analizar adjunto",
    responses={423: _RESPUESTA_BLOQUEADO},
)
async def confirmar_adjunto(
    adjunto_id: str,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    async with sesion_de(usuario) as db:
        resultado, error = await servicio_adjuntos.confirmar(
            db,
            usuario.tenant_id,
            adjunto_id,
        )

    # El rechazo se lanza después del commit para conservar tamaño real.
    if error:
        raise error

    return resultado


@router.get(
    "/{numero}/attachments/{adjunto_id}",
    summary="Descargar adjunto",
    responses={423: _RESPUESTA_BLOQUEADO},
)
async def descargar(
    numero: str,
    adjunto_id: str,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        adjunto = await uno(
            db,
            """
            SELECT *
              FROM core.ticket_attachments
             WHERE id = :a
               AND ticket_id = :tk
               AND tenant_id = :t
            """,
            {
                "a": adjunto_id,
                "tk": ticket["id"],
                "t": usuario.tenant_id,
            },
        )

        if not adjunto:
            raise no_encontrado("El archivo")

        if not adjunto["confirmado"] or adjunto["escaneo"] != "limpio":
            raise AppError(
                "ADJUNTO_BLOQUEADO",
                423,
                "El archivo está pendiente de verificación o fue bloqueado por seguridad.",
            )

    url, expira = blob.url_descarga(adjunto["blob_ref"])

    log.info(
        "attachments.descarga",
        adjunto_id=adjunto_id,
        ticket_id=numero,
    )

    return {
        "download_url": url,
        "expira_en": expira,
    }


@router.get("/{numero}/spec", summary="Spec del ticket")
async def spec(
    numero: str,
    version: int | None = Query(default=None, ge=1),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        condicion = "ticket_id = :tk"
        if version:
            condicion += " AND version = :v"

        especificacion = await uno(
            db,
            f"""
            SELECT *
              FROM core.specs
             WHERE {condicion}
             ORDER BY version DESC
             LIMIT 1
            """,
            {
                "tk": ticket["id"],
                **({"v": version} if version else {}),
            },
        )

        if not especificacion:
            raise AppError(
                "SPEC_NO_DISPONIBLE",
                404,
                f"Este ticket aún no tiene spec: está en {ticket['etapa']}.",
            )

        alternativas = await todos(
            db,
            """
            SELECT alt_id AS id, titulo, descripcion,
                   riesgo, recomendada, elegida
              FROM core.spec_alternatives
             WHERE spec_id = :s
             ORDER BY alt_id
            """,
            {"s": especificacion["id"]},
        )

        criterios = await todos(
            db,
            """
            SELECT criterio_id AS id,
                   given_txt AS given,
                   when_txt AS "when",
                   then_txt AS "then"
              FROM core.spec_criteria
             WHERE spec_id = :s
             ORDER BY criterio_id
            """,
            {"s": especificacion["id"]},
        )

    return {
        "version": especificacion["version"],
        "estado": especificacion["estado"],
        "alternativas": alternativas,
        "criterios": criterios,
        "scope_paths": especificacion["scope_paths"],
        "adr_borrador": especificacion["adr_borrador"],
        "estimacion_loc": especificacion["estimacion_loc"],
    }


@router.get("/{numero}/evidence", summary="Evidencia QA")
async def evidence(
    numero: str,
    corrida: int | None = Query(default=None, ge=1),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        condicion = "ticket_id = :tk"
        if corrida:
            condicion += " AND corrida = :c"

        corrida_qa = await uno(
            db,
            f"""
            SELECT *
              FROM core.qa_runs
             WHERE {condicion}
             ORDER BY corrida DESC
             LIMIT 1
            """,
            {
                "tk": ticket["id"],
                **({"c": corrida} if corrida else {}),
            },
        )

        if not corrida_qa:
            raise AppError(
                "EVIDENCIA_NO_DISPONIBLE",
                404,
                "Este ticket aún no tiene corridas de QA.",
            )

        matriz = await todos(
            db,
            """
            SELECT criterio_id, resultado, extracto_fallo,
                   veredicto_visual, evidencias
              FROM core.qa_results
             WHERE qa_run_id = :r
             ORDER BY criterio_id
            """,
            {"r": corrida_qa["id"]},
        )

        log.info(
            "evidence.consultada",
            ticket_id=numero,
            corrida=corrida_qa["corrida"],
        )

    return {
        "corrida": corrida_qa["corrida"],
        "ejecutada_en": corrida_qa["ejecutada_en"],
        "matriz": matriz,
        "regresion": corrida_qa["regresion"],
        "flaky": corrida_qa["flaky"],
    }


@router.get(
    "/{numero}/security",
    summary="Hallazgos de seguridad",
)
async def security(
    numero: str,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        hallazgos = await todos(
            db,
            """
            SELECT id, severidad, titulo, origen,
                   estado, justificacion
              FROM core.security_findings
             WHERE ticket_id = :tk
             ORDER BY creado_en
            """,
            {"tk": ticket["id"]},
        )

    if not hallazgos and ticket["etapa"] in (
        "triage",
        "design",
        "build",
        "qa",
        "en_cola_por_cuota",
    ):
        raise AppError(
            "SECURITY_NO_DISPONIBLE",
            404,
            "Este ticket aún no pasó por la etapa de seguridad.",
        )

    bloquea = any(
        hallazgo["estado"] == "abierto" and hallazgo["severidad"] in ("alta", "critica")
        for hallazgo in hallazgos
    )

    return {
        "hallazgos": hallazgos,
        "bloquea_deploy": bloquea,
    }


@router.get("/{numero}/usage", summary="Consumo del ticket")
async def usage(
    numero: str,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        por_funcion = await todos(
            db,
            """
            SELECT etapa AS funcion,
                   modelo,
                   sum(invocaciones)::int AS invocaciones,
                   sum(tokens_in)::bigint AS tokens_in,
                   sum(tokens_out)::bigint AS tokens_out,
                   avg(duracion_ms)::int AS duracion_media_ms
              FROM core.usage_tokens
             WHERE ticket_id = :tk
             GROUP BY etapa, modelo
             ORDER BY etapa
            """,
            {"tk": ticket["id"]},
        )

        loc = await uno(
            db,
            """
            SELECT coalesce(sum(anadidas), 0)::int AS anadidas,
                   coalesce(sum(eliminadas), 0)::int AS eliminadas,
                   coalesce(sum(netas), 0)::int AS netas,
                   coalesce(sum(tests), 0)::int AS tests
              FROM core.usage_loc
             WHERE ticket_id = :tk AND tipo = 'pr'
            """,
            {"tk": ticket["id"]},
        )

    totales = {
        "tokens_in": sum(funcion["tokens_in"] for funcion in por_funcion),
        "tokens_out": sum(funcion["tokens_out"] for funcion in por_funcion),
        "invocaciones": sum(funcion["invocaciones"] for funcion in por_funcion),
        "loc_netas": loc["netas"],
        "loc_tests": loc["tests"],
    }

    return {
        "totales": totales,
        "por_funcion": por_funcion,
        "desglose_loc": loc,
    }


@router.get("/{numero}/activity", summary="Actividad del ticket")
async def activity(
    numero: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        items = await todos(
            db,
            """
            SELECT a.creado_en AS fecha,
                   a.actor_tipo,
                   a.actor_id,
                   a.resumen AS evento,
                   a.etapa,
                   u.nombre AS actor_nombre
              FROM audit.audit_events a
              LEFT JOIN core.users u ON u.id = a.actor_id
             WHERE a.tenant_id = :t AND a.ticket_id = :n
             ORDER BY a.id DESC
             LIMIT :lim OFFSET :off
            """,
            {
                "t": usuario.tenant_id,
                "n": numero,
                "lim": page_size,
                "off": (page - 1) * page_size,
            },
        )

    for item in items:
        actor_tipo = item.pop("actor_tipo")
        actor_nombre = item.pop("actor_nombre")
        actor_id = item.pop("actor_id", None)

        item["actor"] = {
            "tipo": actor_tipo,
            "nombre": actor_nombre or actor_id or "sistema",
        }

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
    }


@router.post(
    "/{numero}/watchers",
    summary="Añadir visualizador",
)
async def add_watcher(
    numero: str,
    datos: WatcherIn,
    usuario: UsuarioActual = Depends(requiere_rol(*ROLES_CREAN)),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        usuario_destino = await uno(
            db,
            """
            SELECT id
              FROM core.users
             WHERE id = :i AND estado = 'activo'
            """,
            {"i": datos.user_id},
        )

        if not usuario_destino:
            raise AppError(
                "USUARIO_NO_ENCONTRADO",
                404,
                "El usuario no existe en tu organización.",
            )

        cantidad = (
            await uno(
                db,
                """
                SELECT count(*) AS n
                  FROM core.ticket_watchers
                 WHERE ticket_id = :tk
                """,
                {"tk": ticket["id"]},
            )
        )["n"]

        if cantidad >= 25:
            raise AppError(
                "LIMITE_VISUALIZADORES",
                409,
                "Máximo 25 visualizadores por ticket.",
            )

        await db.execute(
            text("""
                INSERT INTO core.ticket_watchers (
                    tenant_id, ticket_id, user_id
                )
                VALUES (:t, :tk, :u)
                ON CONFLICT DO NOTHING
            """),
            {
                "t": usuario.tenant_id,
                "tk": ticket["id"],
                "u": datos.user_id,
            },
        )

        watchers = await todos(
            db,
            """
            SELECT u.id, u.nombre
              FROM core.ticket_watchers w
              JOIN core.users u ON u.id = w.user_id
             WHERE w.ticket_id = :tk
            """,
            {"tk": ticket["id"]},
        )

    return {"visualizadores": watchers}


@router.delete(
    "/{numero}/watchers/{user_id}",
    summary="Quitar visualizador",
)
async def del_watcher(
    numero: str,
    user_id: str,
    usuario: UsuarioActual = Depends(usuario_actual),
) -> dict:
    async with sesion_de(usuario) as db:
        ticket = await _resolver_ticket(
            db,
            usuario.tenant_id,
            numero,
        )

        if (
            user_id != usuario.user_id
            and usuario.rol not in ("admin", "owner")
            and ticket["reportado_por"] != usuario.user_id
        ):
            raise AppError(
                "PERMISO_DENEGADO",
                403,
                "No puedes quitar a otros visualizadores.",
            )

        try:
            await db.execute(
                text("""
                    DELETE FROM core.ticket_watchers
                     WHERE ticket_id = :tk AND user_id = :u
                """),
                {"tk": ticket["id"], "u": user_id},
            )
        except Exception:
            raise AppError(
                "CREADOR_OBLIGATORIO",
                409,
                "El creador del ticket siempre recibe las notificaciones de cierre.",
            )

        watchers = await todos(
            db,
            """
            SELECT u.id, u.nombre
              FROM core.ticket_watchers w
              JOIN core.users u ON u.id = w.user_id
             WHERE w.ticket_id = :tk
            """,
            {"tk": ticket["id"]},
        )

    return {"visualizadores": watchers}
