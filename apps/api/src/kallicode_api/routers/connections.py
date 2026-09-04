"""Conexiones y onboarding (§13 del diseño).

Las credenciales van a la bóveda (Key Vault en Azure; backend env en local)
vía servicios.boveda — la base solo guarda la referencia. Los tests reales
contra proveedores externos están tras servicios.integraciones con
implementación local simulada (TODO producción).
"""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual
from ..servicios import boveda, integraciones

router = APIRouter(tags=["Conexiones"])

_PROVEEDORES = {
    "repositorio": {"github", "gitlab", "bitbucket", "corestream_repo"},
    "tickets": {"jira", "github_issues", "kallicode_help", "corestream"},
    "cicd": {"github_actions", "gitlab_ci", "jenkins"},
    "database": {"postgresql", "mysql", "sqlserver", "oracle"},
}


class ConexionIn(BaseModel):
    categoria: str = Field(pattern=r"^(repositorio|tickets|cicd|database)$")
    proveedor: str
    nombre: str | None = Field(default=None, max_length=120)
    config: dict = Field(default_factory=dict)
    credenciales: dict


class ConexionPatch(BaseModel):
    nombre: str | None = None
    config: dict | None = None
    credenciales: dict | None = None


class BranchMapIn(BaseModel):
    feature: str = Field(max_length=100)
    staging: str = Field(max_length=100)
    produccion: str = Field(max_length=100)


class DocIn(BaseModel):
    nombre_archivo: str = Field(min_length=1, max_length=255)
    content_type: str
    tamano_bytes: int = Field(ge=1, le=52_428_800)
    tipo_documento: str = Field(default="otro",
                                pattern=r"^(manual|spec|adr|negocio|otro)$")


@router.get("/connections", summary="Listar conexiones")
async def listar(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Salida: conexiones por categoría + mapa de ramas. Sin credenciales."""
    async with sesion_de(usuario) as db:
        conexiones = await todos(db, """
            SELECT id, categoria, proveedor, nombre, estado, ultimo_test
              FROM core.connections WHERE tenant_id=:t ORDER BY categoria, proveedor""",
            {"t": usuario.tenant_id})
        ramas = await uno(db, "SELECT * FROM core.branch_map WHERE tenant_id=:t",
                          {"t": usuario.tenant_id})
    return {"conexiones": conexiones,
            "mapa_ramas": ramas and {"feature": ramas["feature_pattern"],
                                     "staging": ramas["staging_branch"],
                                     "produccion": ramas["prod_branch"]}}


@router.post("/connections", status_code=201, summary="Crear conexión")
async def crear(datos: ConexionIn,
                usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Test real antes de persistir; credenciales a la bóveda; token de ruta
    wh_ para webhooks entrantes. Errores: CONFIG_PROVEEDOR (422) ·
    CREDENCIALES_INVALIDAS / PERMISOS_EXCESIVOS (400) · CONEXION_DUPLICADA (409)."""
    if datos.proveedor not in _PROVEEDORES.get(datos.categoria, set()):
        raise AppError("CONFIG_PROVEEDOR", 422,
                       f"El proveedor {datos.proveedor} no es válido para la "
                       f"categoría {datos.categoria}.")
    test = await integraciones.probar(datos.proveedor, datos.config, datos.credenciales)
    if not test["ok"]:
        codigo = "PERMISOS_EXCESIVOS" if test.get("permisos_excesivos") else \
                 "CREDENCIALES_INVALIDAS"
        raise AppError(codigo, 400, test["mensaje"])
    async with sesion_de(usuario) as db:
        dup = await uno(db, """SELECT id FROM core.connections
                                WHERE tenant_id=:t AND proveedor=:p
                                  AND config = CAST(:c AS jsonb)
                                  AND estado <> 'deshabilitada'""",
                        {"t": usuario.tenant_id, "p": datos.proveedor,
                         "c": json.dumps(datos.config)})
        if dup:
            raise AppError("CONEXION_DUPLICADA", 409, "Ya existe una conexión equivalente.")
        cid = nuevo("cn")
        secreto_ref = await boveda.guardar(f"cn--{cid}", datos.credenciales)
        ruta_token = f"wh_{secrets.token_urlsafe(24)}" \
            if datos.categoria in ("tickets", "cicd") else None
        await db.execute(text("""
            INSERT INTO core.connections (id, tenant_id, categoria, proveedor, nombre,
                                          config, secreto_ref, ruta_token, estado,
                                          ultimo_test, ultimo_test_detalle)
            VALUES (:i, :t, :cat, :p, :n, CAST(:c AS jsonb), :sr, :rt, 'conectada',
                    now(), CAST(:td AS jsonb))"""),
            {"i": cid, "t": usuario.tenant_id, "cat": datos.categoria,
             "p": datos.proveedor, "n": datos.nombre, "c": json.dumps(datos.config),
             "sr": secreto_ref, "rt": ruta_token, "td": json.dumps(test)})
        await registrar_evento(db, usuario.tenant_id, evento="conexion_creada",
                               resumen=f"Conexión {datos.proveedor} creada",
                               actor_tipo="humano", actor_id=usuario.user_id)
    log.info("connections.creada", id=cid, categoria=datos.categoria,
             proveedor=datos.proveedor)
    return {"id": cid, "estado": "conectada",
            "permisos_verificados": test.get("permisos", []),
            **({"webhook_token": ruta_token} if ruta_token else {})}


@router.patch("/connections/{connection_id}", summary="Actualizar conexión")
async def actualizar(connection_id: str, datos: ConexionPatch,
                     usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Rotación segura: si el re-test falla se conserva la credencial anterior."""
    async with sesion_de(usuario) as db:
        cn = await uno(db, "SELECT * FROM core.connections WHERE id=:i", {"i": connection_id})
        if not cn:
            raise no_encontrado("La conexión")
        config = datos.config if datos.config is not None else cn["config"]
        if datos.credenciales is not None:
            test = await integraciones.probar(cn["proveedor"], config, datos.credenciales)
            if not test["ok"]:
                raise AppError("CREDENCIALES_INVALIDAS", 400,
                               "Las nuevas credenciales no funcionan; se mantienen "
                               "las anteriores.")
            await boveda.guardar(f"cn--{connection_id}", datos.credenciales)
        for campo, v in (("nombre", datos.nombre),):
            if v is not None:
                await db.execute(text("UPDATE core.connections SET nombre=:v WHERE id=:i"),
                                 {"v": v, "i": connection_id})
        if datos.config is not None:
            await db.execute(text("""UPDATE core.connections SET config=CAST(:c AS jsonb)
                                      WHERE id=:i"""),
                             {"c": json.dumps(datos.config), "i": connection_id})
        await db.execute(text("""UPDATE core.connections SET ultimo_test=now(),
                                        estado='conectada' WHERE id=:i"""),
                         {"i": connection_id})
        await registrar_evento(db, usuario.tenant_id, evento="conexion_modificada",
                               resumen=f"Conexión {cn['proveedor']} actualizada",
                               actor_tipo="humano", actor_id=usuario.user_id)
        cn = await uno(db, "SELECT id, estado, ultimo_test FROM core.connections "
                           "WHERE id=:i", {"i": connection_id})
    return cn


@router.delete("/connections/{connection_id}", summary="Eliminar conexión")
async def eliminar(connection_id: str,
                   usuario: UsuarioActual = Depends(requiere_rol("owner"))) -> dict:
    """Error: CONEXION_EN_USO (409) con jobs activos dependientes."""
    async with sesion_de(usuario) as db:
        cn = await uno(db, "SELECT * FROM core.connections WHERE id=:i", {"i": connection_id})
        if not cn:
            raise no_encontrado("La conexión")
        activos = (await uno(db, """SELECT count(*) AS n FROM core.jobs
                                     WHERE tenant_id=:t AND estado NOT IN
                                           ('produccion','cancelado','cerrado_duplicado')""",
                             {"t": usuario.tenant_id}))["n"]
        if cn["categoria"] == "repositorio" and activos:
            raise AppError("CONEXION_EN_USO", 409,
                           "Hay trabajos activos usando esta conexión; espera a que "
                           "terminen o cancélalos.")
        await boveda.eliminar(f"cn--{connection_id}")
        await db.execute(text("DELETE FROM core.connections WHERE id=:i"),
                         {"i": connection_id})
        await registrar_evento(db, usuario.tenant_id, evento="conexion_eliminada",
                               resumen=f"Conexión {cn['proveedor']} eliminada",
                               actor_tipo="humano", actor_id=usuario.user_id)
    log.info("connections.eliminada", id=connection_id)
    return {"eliminada": True}


@router.post("/connections/{connection_id}/test", summary="Probar conexión")
async def probar(connection_id: str,
                 usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """El fallo del test NO es error HTTP: 200 con estado='error' y detalle."""
    async with sesion_de(usuario) as db:
        cn = await uno(db, "SELECT * FROM core.connections WHERE id=:i", {"i": connection_id})
        if not cn:
            raise no_encontrado("La conexión")
        creds = await boveda.leer(f"cn--{connection_id}")
        test = await integraciones.probar(cn["proveedor"], cn["config"], creds or {})
        estado = "conectada" if test["ok"] else "error"
        await db.execute(text("""UPDATE core.connections SET estado=:e, ultimo_test=now(),
                                        ultimo_test_detalle=CAST(:d AS jsonb) WHERE id=:i"""),
                         {"e": estado, "d": json.dumps(test), "i": connection_id})
    log.info("connections.test", id=connection_id, resultado=estado)
    return {"estado": estado, "detalle": test}


@router.put("/connections/branch-map", summary="Configurar mapa de ramas")
async def branch_map(datos: BranchMapIn,
                     usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """Errores: RAMA_INEXISTENTE (422, verificación contra el repo — TODO
    producción) · JOBS_ACTIVOS (409, no cambiar con build/deploy en curso)."""
    if datos.staging == datos.produccion:
        raise AppError("VALIDACION_ENTRADA", 422,
                       "Las ramas de staging y producción deben ser distintas.")
    async with sesion_de(usuario) as db:
        activos = (await uno(db, """SELECT count(*) AS n FROM core.jobs
                                     WHERE tenant_id=:t AND estado IN ('build','deploy')""",
                             {"t": usuario.tenant_id}))["n"]
        if activos:
            raise AppError("JOBS_ACTIVOS", 409,
                           "Hay trabajos en Build o Deploy; el mapa de ramas no puede "
                           "cambiarse ahora.")
        await db.execute(text("""
            INSERT INTO core.branch_map (tenant_id, feature_pattern, staging_branch,
                                         prod_branch)
            VALUES (:t, :f, :s, :p)
            ON CONFLICT (tenant_id) DO UPDATE
               SET feature_pattern=:f, staging_branch=:s, prod_branch=:p,
                   actualizado_en=now()"""),
            {"t": usuario.tenant_id, "f": datos.feature, "s": datos.staging,
             "p": datos.produccion})
        await registrar_evento(db, usuario.tenant_id, evento="configuracion_modificada",
                               resumen="Mapa de ramas actualizado", actor_tipo="humano",
                               actor_id=usuario.user_id, datos=datos.model_dump())
    return {"mapa_ramas": datos.model_dump()}


# --------------------------------------------------------------- onboarding
router_ob = APIRouter(prefix="/onboarding", tags=["Onboarding"])


@router_ob.post("/documents", status_code=201, summary="Subir documentación para el grafo")
async def subir_doc(datos: DocIn,
                    usuario: UsuarioActual = Depends(requiere_rol("owner", "admin"))) -> dict:
    """URL firmada de subida; el documento queda en cola de ingesta.
    Errores: TIPO_NO_PERMITIDO (415) · ADJUNTO_DEMASIADO_GRANDE (413)."""
    from ..servicios import blob
    permitidos = {"application/pdf", "text/markdown", "text/plain",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    if datos.content_type not in permitidos:
        raise AppError("TIPO_NO_PERMITIDO", 415, "Solo se aceptan PDF, DOCX, MD y TXT.")
    async with sesion_de(usuario) as db:
        did = nuevo("doc")
        ref = f"{usuario.tenant_id}/docs/{did}/{datos.nombre_archivo.replace('/', '_')}"
        await db.execute(text("""
            INSERT INTO core.onboarding_documents (id, tenant_id, nombre_archivo,
                                                   content_type, tamano_bytes,
                                                   tipo_documento, blob_ref, subido_por)
            VALUES (:i, :t, :n, :ct, :tb, :tp, :ref, :u)"""),
            {"i": did, "t": usuario.tenant_id, "n": datos.nombre_archivo,
             "ct": datos.content_type, "tb": datos.tamano_bytes,
             "tp": datos.tipo_documento, "ref": ref, "u": usuario.user_id})
    url, expira = blob.url_subida(ref, datos.content_type)
    log.info("onboarding.doc.subido", documento_id=did, tipo=datos.tipo_documento)
    return {"documento_id": did, "upload_url": url, "expira_en": expira}


@router_ob.get("/codemapping/status", summary="Estado de construcción del grafo")
async def cm_status(usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Métricas del grafo. TODO producción: consultar el servicio CodeMapping;
    aquí se derivan de las tablas locales (definiciones/documentos ingeridos)."""
    async with sesion_de(usuario) as db:
        docs = await uno(db, """SELECT count(*) FILTER (WHERE estado='procesado') AS ok,
                                       count(*) AS total
                                  FROM core.onboarding_documents WHERE tenant_id=:t""",
                         {"t": usuario.tenant_id})
        defs = (await uno(db, """SELECT count(*) AS n FROM vec.definition_embeddings
                                  WHERE tenant_id=:t""", {"t": usuario.tenant_id}))["n"]
    progreso = 1.0 if docs["total"] == 0 else docs["ok"] / docs["total"]
    return {"fase": 7 if progreso >= 1 else 4, "progreso": round(progreso, 2),
            "metricas": {"definiciones": defs, "documentos": docs["total"]},
            "curacion_pendiente": 0, "ultimo_refresh": None}


@router_ob.post("/complete", summary="Encender la primera línea de producción")
async def completar(usuario: UsuarioActual = Depends(requiere_rol("owner"))) -> dict:
    """Verifica precondiciones, crea las líneas del plan y activa la fábrica.
    Error: ONBOARDING_INCOMPLETO (409) con la lista de pendientes.
    Idempotente. Auditoría: fabrica_activada (hito de facturación)."""
    from kallicode_core.comercial import plan_vigente
    async with sesion_de(usuario) as db:
        org = await uno(db, "SELECT * FROM core.organizations WHERE id=:t",
                        {"t": usuario.tenant_id})
        if org["fabrica_activa"]:
            lineas = (await uno(db, """SELECT count(*) AS n FROM core.production_lines
                                        WHERE tenant_id=:t""", {"t": usuario.tenant_id}))["n"]
            return {"activado": True, "lineas_activadas": lineas, "pendientes": []}
        pendientes = []
        repo = await uno(db, """SELECT id FROM core.connections
                                 WHERE tenant_id=:t AND categoria='repositorio'
                                   AND estado='conectada' LIMIT 1""", {"t": usuario.tenant_id})
        if not repo:
            pendientes.append("repositorio")
        ramas = await uno(db, "SELECT 1 AS x FROM core.branch_map WHERE tenant_id=:t",
                          {"t": usuario.tenant_id})
        if not ramas:
            pendientes.append("mapa_ramas")
        if pendientes:
            raise AppError("ONBOARDING_INCOMPLETO", 409,
                           "Faltan pasos para encender la fábrica.",
                           {"pendientes": pendientes})
        plan = await plan_vigente(db, usuario.tenant_id)
        n_lineas = plan["lineas"] or 1
        for n in range(1, n_lineas + 1):
            await db.execute(text("""INSERT INTO core.production_lines (tenant_id, numero)
                                     VALUES (:t, :n) ON CONFLICT DO NOTHING"""),
                             {"t": usuario.tenant_id, "n": n})
        await db.execute(text("UPDATE core.organizations SET fabrica_activa=true "
                              "WHERE id=:t"), {"t": usuario.tenant_id})
        await registrar_evento(db, usuario.tenant_id, evento="fabrica_activada",
                               resumen=f"Fábrica activada con {n_lineas} líneas",
                               actor_tipo="humano", actor_id=usuario.user_id)
    log.info("onboarding.completado", lineas=n_lineas)
    return {"activado": True, "lineas_activadas": n_lineas, "pendientes": []}


router.include_router(router_ob)
