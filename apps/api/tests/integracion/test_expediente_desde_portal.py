"""E2E del expediente de auditoría (KC-PT-08-6), contra el backend real.

Recorre lo mismo que el portal: explorador filtrado → expediente → verificar los sellos →
inspeccionar un paso LLM (con sus trazas en Blob) → generar el PDF con el worker de
exportaciones → descargarlo. Las solicitudes salen de expediente_desde_portal.json, el mismo
archivo con el que kallicode-portal/src/vistas/expediente/RecorridoExpediente.test.tsx recorre
la interfaz respondiendo con lo grabado aquí.

- Cada respuesta debe mantener la forma de la grabada (mismas claves y tipos JSON; puede haber
  claves nuevas). Así un cambio como el de `confianza` serializada como texto falla aquí, y no
  en el navegador.
- Con KC_REGRABAR_EXPEDIENTE=1 se regraban las respuestas (las URLs firmadas quedan con
  ?sig=grabado) y el portal las usa en su prueba.

Base de datos, Redis y Azurite reales (el job backend del CI levanta Azurite en :10000). Sin
KC_BLOB_CONNECTION_STRING se usa la del emulador que trae .env.example.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.auditoria import registrar_evento
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_access_token
from kallicode_workers import audit_export
from kallicode_workers.orquestador.trazas import publicar_trazas_paso
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

AQUI = Path(__file__).parent
CONTRATO_PATH = AQUI / "expediente_desde_portal.json"
CONTRATO = json.loads(CONTRATO_PATH.read_text(encoding="utf-8"))
REGRABAR = os.environ.get("KC_REGRABAR_EXPEDIENTE") == "1"

TRAZA_ENTRADA = {
    "titulo": "La exportación mensual falla con timeout",
    "descripcion": "Desde ayer el reporte no termina.",
}
TRAZA_SALIDA = {"tipo": "bug", "prioridad": "alta", "confianza": 0.87}


# ---- forma de una respuesta: claves y tipos JSON ----


def _forma(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {k: _forma(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_forma(valor[0])] if valor else []
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, int | float):
        return "number"
    if isinstance(valor, str):
        return "string"
    return "null"


def _diferencias(grabada: Any, real: Any, ruta: str = "$") -> list[str]:
    """Lo que la respuesta real perdió o cambió de tipo respecto de la grabada."""
    if isinstance(grabada, dict):
        if not isinstance(real, dict):
            return [f"{ruta}: era un objeto y ahora es {real!r}"]
        return [
            d
            for clave, valor in grabada.items()
            for d in (
                _diferencias(valor, real[clave], f"{ruta}.{clave}")
                if clave in real
                else [f"{ruta}.{clave}: ya no viene"]
            )
        ]
    if isinstance(grabada, list):
        if not isinstance(real, list):
            return [f"{ruta}: era una lista y ahora es {real!r}"]
        if grabada and not real:
            return [f"{ruta}: venía con elementos y ahora está vacía"]
        return _diferencias(grabada[0], real[0], f"{ruta}[0]") if grabada else []
    return [] if grabada == real else [f"{ruta}: era {grabada} y ahora es {real}"]


# ---- marcadores y grabación ----


def _rellenar(valor: Any, valores: dict[str, str]) -> Any:
    if isinstance(valor, str):
        return re.sub(r"\{(\w+)\}", lambda m: valores[m.group(1)], valor)
    if isinstance(valor, dict):
        return {k: _rellenar(v, valores) for k, v in valor.items()}
    return valor


def _sin_firma(valor: Any) -> Any:
    """Las URLs firmadas (SAS) se graban sin su firma."""
    if isinstance(valor, str) and valor.startswith("http") and "sig=" in valor:
        return valor.split("?", 1)[0] + "?sig=grabado"
    if isinstance(valor, dict):
        return {k: _sin_firma(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_sin_firma(v) for v in valor]
    return valor


def _conexion_emulador() -> str:
    linea = next(
        x
        for x in (AQUI.parents[3] / ".env.example").read_text(encoding="utf-8").splitlines()
        if x.startswith("KC_BLOB_CONNECTION_STRING=")
    )
    return linea.split("=", 1)[1].strip().strip('"')


# ---- fixtures ----


@pytest.fixture
def azurite(monkeypatch):
    settings = get_settings()
    if settings.env != "local":
        pytest.skip("solo con el almacenamiento local (Azurite)")
    if not settings.blob_connection_string:
        monkeypatch.setattr(settings, "blob_connection_string", _conexion_emulador())
    try:
        httpx.get("http://127.0.0.1:10000/", timeout=3)
    except httpx.HTTPError:
        # En el CI el job backend siempre lo levanta: ahí no responder es un error.
        motivo = "Azurite no responde en :10000"
        pytest.fail(motivo) if os.environ.get("CI") else pytest.skip(motivo)


@pytest_asyncio.fixture
async def expediente_e2e(azurite):
    """Tenant con su owner y un ticket con eventos de varios actores y un paso LLM en Blob."""
    tenant = f"org_rec_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    ticket_id = str(uuid.uuid4())
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    step_id = f"st_{uuid.uuid4().hex[:12]}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Recorrido expediente')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Olga Owner', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        numero = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Exportación con timeout', 'El reporte no termina',
                               'bug', 'alta', 'design', :u)
                       RETURNING numero"""
                ),
                {"id": ticket_id, "t": tenant, "u": owner},
            )
        ).scalar_one()
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                   VALUES (:j, :t, :tk, 'design', 'P2')"""
            ),
            {"j": job_id, "t": tenant, "tk": ticket_id},
        )
        await db.commit()

    # Las trazas se suben con el mismo código que el orquestador (KC-BE-08-4).
    entrada_ref, salida_ref = await publicar_trazas_paso(
        tenant_id=tenant,
        ticket_ref=numero,
        step_id=step_id,
        entrada=TRAZA_ENTRADA,
        salida=TRAZA_SALIDA,
    )
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.llm_steps (id, tenant_id, job_id, ticket_id, etapa, paso,
                       version_plantilla, modelo, tier, confianza, validacion, reintentos,
                       tokens_in, tokens_out, duracion_ms, entrada_ref, salida_ref)
                   VALUES (:id, :t, :j, :tk, 'triage', 'triage.classify', '3',
                       'gemini-2.5-flash', 'flash', 0.87, 'reintento', 1, 1520, 318, 2310,
                       :er, :sr)"""
            ),
            {
                "id": step_id,
                "t": tenant,
                "j": job_id,
                "tk": ticket_id,
                "er": entrada_ref,
                "sr": salida_ref,
            },
        )
        for evento, resumen, actor, etapa in (
            ("ticket_creado", "Ticket creado desde el portal", "humano", "triage"),
            ("triage_clasificado", "Clasificado como bug de prioridad alta", "agente", "triage"),
            ("spec_propuesta", "Spec propuesta para revisión", "agente", "design"),
        ):
            await registrar_evento(
                db,
                tenant,
                evento=evento,
                resumen=resumen,
                actor_tipo=actor,
                actor_id=owner if actor == "humano" else f"agente-{etapa}",
                etapa=etapa,
                ticket_id=numero,
                step_id=step_id if evento == "triage_clasificado" else None,
            )
        await db.commit()

    token = emitir_access_token(owner, tenant, "owner")
    yield {
        "tenant": tenant,
        "numero": numero,
        "step_id": step_id,
        "headers": {"Authorization": f"Bearer {token}"},
    }

    async with cliente_blob() as cliente:
        contenedor = cliente.get_container_client(get_settings().blob_container_evidencia)
        for prefijo in (f"trazas-llm/{tenant}/", f"{tenant}/exports/"):
            async for b in contenedor.list_blobs(name_starts_with=prefijo):
                await contenedor.delete_blob(b.name)
    async with sesion_sistema() as db:
        for tabla in ("core.audit_exports", "core.tickets", "core.ticket_counters", "core.users"):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant})
        await db.commit()


# ---- el recorrido ----


async def test_recorrido_del_expediente_con_lo_que_pide_el_portal(
    client: AsyncClient, expediente_e2e
) -> None:
    tenant, numero = expediente_e2e["tenant"], expediente_e2e["numero"]
    valores = {"numero": numero}
    respuestas: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=10) as almacen:
        for s in CONTRATO["solicitudes"]:
            if "url" in s:
                # Las trazas se leen directo del Blob con la URL firmada, como el navegador.
                r = await almacen.get(_rellenar(s["url"], valores))
            else:
                r = await client.request(
                    s["metodo"],
                    _rellenar(s["ruta"], valores),
                    params=_rellenar(s.get("consulta"), valores),
                    json=_rellenar(s.get("cuerpo"), valores),
                    headers=expediente_e2e["headers"],
                )
            assert r.status_code in (200, 202), f"{s['paso']}: {r.status_code} {r.text}"
            cuerpo = r.json()
            respuestas[s["paso"]] = {"status": r.status_code, "cuerpo": cuerpo}

            if s["paso"] == "expediente":
                valores["step_id"] = cuerpo["etapas"][0]["pasos"][0]["step_id"]
            elif s["paso"] == "paso":
                valores["entrada_url"] = cuerpo["entrada_url"]
                valores["salida_url"] = cuerpo["salida_url"]
            elif s["paso"] == "exportar":
                valores["export_id"] = cuerpo["export_id"]
                # El worker de kc:exports procesa la exportación encolada.
                await audit_export.procesar({"export_id": cuerpo["export_id"], "tenant_id": tenant})
            elif s["paso"] == "estado-exportacion":
                valores["download_url"] = cuerpo["download_url"]

        # Lo que el portal muestra en cada paso.
        eventos = respuestas["explorador"]["cuerpo"]["items"]
        assert {(e["ticket_id"], e["actor_tipo"]) for e in eventos} == {(numero, "agente")}
        assert len(eventos) == 2
        expediente = respuestas["expediente"]["cuerpo"]
        assert expediente["integridad"]["integra"] is True
        assert expediente["integridad"]["eventos_verificados"] == 3
        assert expediente["etapas"][0]["pasos"][0]["confianza"] == 0.87
        verificacion = respuestas["verificar"]["cuerpo"]
        assert verificacion["alcance"] == "tramo_ticket"
        assert (verificacion["integra"], verificacion["eventos_verificados"]) == (True, 3)
        paso = respuestas["paso"]["cuerpo"]
        assert paso["paso"]["plantilla"] == "triage.classify"
        assert paso["ejecucion"]["tokens_in"] == 1520
        assert respuestas["traza-entrada"]["cuerpo"] == TRAZA_ENTRADA
        assert respuestas["traza-salida"]["cuerpo"] == TRAZA_SALIDA
        assert respuestas["exportar"]["status"] == 202
        assert respuestas["estado-exportacion"]["cuerpo"]["estado"] == "lista"

        # La descarga: el PDF del expediente desde el Blob, con la URL que ofrece el portal.
        pdf = await almacen.get(valores["download_url"])
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF-") and pdf.content.rstrip().endswith(b"%%EOF")

    if REGRABAR:
        grabado = {
            **CONTRATO,
            "valores": _sin_firma(valores),
            "respuestas": _sin_firma(respuestas),
        }
        CONTRATO_PATH.write_text(
            json.dumps(grabado, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return

    assert "respuestas" in CONTRATO, "Falta grabar: correr con KC_REGRABAR_EXPEDIENTE=1"
    problemas = [
        f"{paso}: {d}"
        for paso, grabada in CONTRATO["respuestas"].items()
        for d in (
            [f"status {respuestas[paso]['status']} (grabado {grabada['status']})"]
            if respuestas[paso]["status"] != grabada["status"]
            else _diferencias(_forma(grabada["cuerpo"]), _forma(respuestas[paso]["cuerpo"]))
        )
    ]
    assert problemas == [], "El backend ya no responde lo que el portal espera:\n" + "\n".join(
        problemas
    )
