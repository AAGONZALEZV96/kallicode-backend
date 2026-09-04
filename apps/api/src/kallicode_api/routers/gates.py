"""Aprobaciones y gates (§10 del diseño).

Una firma es una transacción: verificación de precondiciones + inserción de
la firma + actualización del job + evento de auditoría sellado, todo atómico.
El constraint trigger T5 de la base refuerza que el Gate 3 exige los gates
1 y 2 aprobados aunque la aplicación fallara.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from kallicode_core import comercial
from kallicode_core.auditoria import registrar_evento
from kallicode_core.config import get_settings
from kallicode_core.db import todos, uno
from kallicode_core.errors import AppError, no_encontrado
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from kallicode_core.seguridad import puede_firmar

from ..deps import UsuarioActual, requiere_rol, sesion_de, usuario_actual

router = APIRouter(tags=["Aprobaciones"])

_GATE_ESTADO_JOB = {1: "gate1", 2: "gate2", 3: "gate3"}
_GATE_AVANZA_A = {1: "build", 2: "security", 3: "deploy"}
_ROLES_APROBADOR = ("owner", "admin", "architect", "approver")


class FirmaIn(BaseModel):
    """Entrada de aprobación de gate."""
    comentario: str | None = Field(default=None, max_length=2000)
    alternativa_id: str | None = None       # Gate 1 con spec multi-alternativa
    acepta_fallos: bool = False             # Gate 2 con criterios en falla


class CambiosIn(BaseModel):
    """Entrada de devolución: el comentario re-entra como contexto del agente."""
    comentario: str = Field(min_length=10, max_length=2000)
    devolver_a: str | None = Field(default=None, pattern=r"^(design|build|qa)$")  # Gate 3


async def _ticket_y_job(db, tenant_id: str, numero: str) -> tuple[dict, dict | None]:
    t = await uno(db, "SELECT * FROM core.tickets WHERE tenant_id=:t AND numero=:n",
                  {"t": tenant_id, "n": numero})
    if not t:
        raise no_encontrado("El ticket")
    job = await uno(db, """SELECT * FROM core.jobs WHERE ticket_id=:tk
                            AND estado NOT IN ('produccion','cancelado','cerrado_duplicado')
                            FOR UPDATE""", {"tk": t["id"]})
    return t, job


@router.get("/approvals/pending", summary="Aprobaciones pendientes")
async def pendientes(gate: int | None = None,
                     usuario: UsuarioActual = Depends(
                         requiere_rol(*_ROLES_APROBADOR))) -> dict:
    """Tickets detenidos en un gate, filtrados a los firmables por el rol."""
    async with sesion_de(usuario) as db:
        filas = await todos(db, """
            SELECT numero AS ticket_id, titulo, gate_pendiente AS gate, actualizado_en
              FROM core.tickets
             WHERE tenant_id = :t AND gate_pendiente IS NOT NULL
             ORDER BY actualizado_en""", {"t": usuario.tenant_id})
    que = {1: "Aprobar spec y alcance", 2: "Validar evidencia en staging",
           3: "Firma de arquitecto → producción"}
    items = [{"ticket_id": f["ticket_id"], "titulo": f["titulo"], "gate": f["gate"],
              "que_se_aprueba": que[f["gate"]], "esperando_desde": f["actualizado_en"]}
             for f in filas
             if puede_firmar(usuario.rol, f["gate"]) and (gate is None or f["gate"] == gate)]
    return {"items": items}


@router.get("/tickets/{numero}/gates", summary="Estado de gates del ticket")
async def estado_gates(numero: str,
                       usuario: UsuarioActual = Depends(usuario_actual)) -> dict:
    """Cadena de firmas: estado, actor, fecha, comentario y sello por gate."""
    async with sesion_de(usuario) as db:
        t, _ = await _ticket_y_job(db, usuario.tenant_id, numero)
        firmas = await todos(db, """
            SELECT g.gate, g.accion, u.nombre AS actor, g.creado_en AS fecha,
                   g.comentario, g.sello
              FROM core.gate_signatures g JOIN core.users u ON u.id = g.actor_id
             WHERE g.ticket_id = :tk ORDER BY g.gate, g.creado_en""", {"tk": t["id"]})
    gates = []
    for n in (1, 2, 3):
        aprobada = next((f for f in firmas if f["gate"] == n and f["accion"] == "aprobado"),
                        None)
        if aprobada:
            gates.append({"gate": n, "estado": "firmado", "actor": aprobada["actor"],
                          "fecha": aprobada["fecha"], "comentario": aprobada["comentario"],
                          "sello": aprobada["sello"]})
        else:
            gates.append({"gate": n, "estado": "pendiente"
                          if t["gate_pendiente"] == n else "no_alcanzado"})
    return {"gates": gates}


@router.post("/tickets/{numero}/gates/{gate}/approve", summary="Aprobar gate (firmar)")
async def aprobar(numero: str, gate: int, datos: FirmaIn,
                  usuario: UsuarioActual = Depends(
                      requiere_rol(*_ROLES_APROBADOR))) -> dict:
    """Firma el gate en una transacción y reanuda el job.

    Precondiciones — G1: spec pendiente + alternativa elegida si hay varias.
    G2: matriz de QA completa; fallos exigen acepta_fallos con comentario.
    G3: solo architect; G1+G2 firmados; sin bloqueantes de seguridad.
    Errores: GATE_NO_ACTIVO / PRECONDICION_GATE (409) · FIRMA_NO_AUTORIZADA
    (403) · ALTERNATIVA_REQUERIDA / FALLOS_SIN_ACEPTAR (422).
    Auditoría: gate_firmado (el sello queda enlazado en la firma).
    """
    if gate not in (1, 2, 3):
        raise no_encontrado("El gate")
    if not puede_firmar(usuario.rol, gate):
        raise AppError("FIRMA_NO_AUTORIZADA", 403,
                       "El Gate 3 requiere la firma de un arquitecto." if gate == 3
                       else "Tu rol no puede firmar este gate.")
    async with sesion_de(usuario) as db:
        t, job = await _ticket_y_job(db, usuario.tenant_id, numero)
        if t["gate_pendiente"] != gate or not job:
            raise AppError("GATE_NO_ACTIVO", 409,
                           f"Este ticket no está esperando el Gate {gate}.")
        spec = await uno(db, """SELECT * FROM core.specs WHERE ticket_id=:tk
                                 ORDER BY version DESC LIMIT 1""", {"tk": t["id"]})
        # ----- precondiciones por gate -----
        if gate == 1:
            if not spec or spec["estado"] != "pendiente_gate1":
                raise AppError("PRECONDICION_GATE", 409,
                               "No se puede firmar: no hay spec pendiente de aprobación.")
            alts = await todos(db, "SELECT * FROM core.spec_alternatives WHERE spec_id=:s",
                               {"s": spec["id"]})
            if len(alts) > 1 and not datos.alternativa_id:
                raise AppError("ALTERNATIVA_REQUERIDA", 422,
                               "Debes elegir una alternativa de la spec para aprobar "
                               "el diseño.")
            if datos.alternativa_id:
                ok = await db.execute(text("""
                    UPDATE core.spec_alternatives SET elegida = true
                     WHERE spec_id = :s AND alt_id = :a"""),
                    {"s": spec["id"], "a": datos.alternativa_id})
                if ok.rowcount == 0:
                    raise AppError("VALIDACION_ENTRADA", 422,
                                   "La alternativa indicada no existe en la spec.")
            await db.execute(text("UPDATE core.specs SET estado='aprobada' WHERE id=:s"),
                             {"s": spec["id"]})
        elif gate == 2:
            run = await uno(db, """SELECT * FROM core.qa_runs WHERE ticket_id=:tk
                                    ORDER BY corrida DESC LIMIT 1""", {"tk": t["id"]})
            if not run:
                raise AppError("PRECONDICION_GATE", 409,
                               "No se puede firmar: no hay corrida de QA registrada.")
            fallos = await todos(db, """SELECT criterio_id FROM core.qa_results
                                         WHERE qa_run_id=:r AND resultado='falla'""",
                                 {"r": run["id"]})
            if fallos and not (datos.acepta_fallos and datos.comentario):
                raise AppError("FALLOS_SIN_ACEPTAR", 422,
                               "Hay criterios en falla: debes aceptarlos explícitamente "
                               "con justificación o pedir cambios.",
                               {"criterios_en_falla": [f["criterio_id"] for f in fallos]})
        else:  # gate 3
            bloqueantes = await todos(db, """SELECT id FROM core.security_findings
                                              WHERE ticket_id=:tk AND bloquea
                                                AND estado='abierto'""", {"tk": t["id"]})
            if bloqueantes:
                raise AppError("PRECONDICION_GATE", 409,
                               "No se puede firmar: hay hallazgos de seguridad "
                               "bloqueantes sin resolver.")
        # ----- firma (única por índice parcial; T5 refuerza G3) -----
        sello = await registrar_evento(
            db, usuario.tenant_id, evento="gate_firmado",
            resumen=f"Gate {gate} aprobado", actor_tipo="humano",
            actor_id=usuario.user_id, ticket_id=numero,
            datos={"gate": gate, "alternativa": datos.alternativa_id,
                   "acepta_fallos": datos.acepta_fallos})
        try:
            await db.execute(text("""
                INSERT INTO core.gate_signatures
                    (id, tenant_id, ticket_id, gate, accion, actor_id, comentario,
                     alternativa_id, acepta_fallos, spec_id, sello)
                VALUES (:i, :t, :tk, :g, 'aprobado', :u, :c, :alt, :af, :sp, :se)"""),
                {"i": nuevo("gs"), "t": usuario.tenant_id, "tk": t["id"], "g": gate,
                 "u": usuario.user_id, "c": datos.comentario, "alt": datos.alternativa_id,
                 "af": datos.acepta_fallos, "sp": spec and spec["id"], "se": sello})
        except Exception as e:  # UNIQUE gate_aprobado_uq o trigger T5
            raise AppError("GATE_NO_ACTIVO", 409,
                           f"Este gate ya fue firmado o no cumple precondiciones "
                           f"({type(e).__name__}).")
        # ----- reanuda el job (T7/T8/T9 sincronizan línea, historial y ticket) --
        destino = _GATE_AVANZA_A[gate]
        await db.execute(text("UPDATE core.jobs SET estado=:e WHERE id=:j"),
                         {"e": destino, "j": job["id"]})
        cuota = await comercial.estado_cuota_loc(db, usuario.tenant_id)
        aviso = None
        if gate == 1 and spec and spec["estimacion_loc"] and cuota["limite"]:
            if spec["estimacion_loc"] > cuota["limite"] - cuota["consumidas"]:
                aviso = ("La estimación de LOC de esta spec supera la cuota restante "
                         "del ciclo: el Build podrá pausarse por cuota (QU-1).")
    log.info("gates.firmado", ticket_id=numero, gate=gate, sello=sello[:12])
    return {"gate": gate, "estado": "firmado", "sello": sello,
            "ticket": {"id": numero, "etapa": destino, "gate_pendiente": None},
            **({"aviso_cuota": aviso} if aviso else {})}


@router.post("/tickets/{numero}/gates/{gate}/request-changes",
             summary="Pedir cambios en un gate")
async def pedir_cambios(numero: str, gate: int, datos: CambiosIn,
                        usuario: UsuarioActual = Depends(
                            requiere_rol(*_ROLES_APROBADOR))) -> dict:
    """Devuelve el trabajo con el comentario como contexto del agente.

    G1 → re-itera la spec · G2 → Build con lo observado · G3 → devolver_a.
    Errores: GATE_NO_ACTIVO (409) · COMENTARIO_REQUERIDO (422) ·
    MAX_ITERACIONES (409, tras 5 devoluciones escala a humano).
    """
    if gate not in (1, 2, 3):
        raise no_encontrado("El gate")
    if not puede_firmar(usuario.rol, gate):
        raise AppError("FIRMA_NO_AUTORIZADA", 403, "Tu rol no puede decidir este gate.")
    destino = {1: "design", 2: "build", 3: datos.devolver_a or "qa"}[gate]
    async with sesion_de(usuario) as db:
        t, job = await _ticket_y_job(db, usuario.tenant_id, numero)
        if t["gate_pendiente"] != gate or not job:
            raise AppError("GATE_NO_ACTIVO", 409,
                           f"Este ticket no está esperando el Gate {gate}.")
        iteraciones = dict(job["iteraciones"] or {})
        n = iteraciones.get(f"gate{gate}", 0) + 1
        iteraciones[f"gate{gate}"] = n
        maximo = get_settings().max_iteraciones_gate
        escalado = n > maximo
        import json as _json
        await db.execute(text("""UPDATE core.jobs SET estado=:e,
                                        iteraciones=CAST(:it AS jsonb),
                                        contexto=CAST(:cx AS jsonb) WHERE id=:j"""),
                         {"e": "escalado_humano" if escalado else destino,
                          "it": _json.dumps(iteraciones),
                          "cx": _json.dumps({"gate": gate, "comentario": datos.comentario}),
                          "j": job["id"]})
        sello = await registrar_evento(
            db, usuario.tenant_id, evento="gate_devuelto",
            resumen=f"Gate {gate}: cambios pedidos (iteración {n})",
            actor_tipo="humano", actor_id=usuario.user_id, ticket_id=numero,
            datos={"gate": gate, "iteracion": n, "destino": destino})
        await db.execute(text("""
            INSERT INTO core.gate_signatures
                (id, tenant_id, ticket_id, gate, accion, actor_id, comentario,
                 devolver_a, sello)
            VALUES (:i, :t, :tk, :g, 'cambios_pedidos', :u, :c, :d, :se)"""),
            {"i": nuevo("gs"), "t": usuario.tenant_id, "tk": t["id"], "g": gate,
             "u": usuario.user_id, "c": datos.comentario,
             "d": datos.devolver_a, "se": sello})
        if escalado:
            await db.execute(text("""
                INSERT INTO core.escalations (id, tenant_id, job_id, etapa, motivo,
                                              severidad, detalle)
                VALUES (:i, :t, :j, :e, 'max_iteraciones', 'media',
                        CAST(:d AS jsonb))"""),
                {"i": nuevo("esc"), "t": usuario.tenant_id, "j": job["id"],
                 "e": destino, "d": _json.dumps({"gate": gate, "iteracion": n})})
    log.info("gates.cambios_pedidos", ticket_id=numero, gate=gate, iteracion=n,
             escalado=escalado)
    if escalado:
        raise AppError("MAX_ITERACIONES", 409,
                       "Se alcanzó el máximo de iteraciones de este gate; "
                       "el caso pasó a revisión con el equipo Kallicode.")
    return {"ticket": {"id": numero, "etapa": destino, "iteracion": n}}
