"""Pruebas de trazas LLM en Blob, URLs firmadas y retención (KC-BE-08-4)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request, Response
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app as api_app
from kallicode_api.routers import audit as audit_router
from kallicode_api.routers import internal as internal_router
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.llm.tiers import Tier
from kallicode_workers import housekeeping
from kallicode_workers.orquestador import _comun as comun_mod
from kallicode_workers.orquestador import trazas as trazas_mod
from kallicode_workers.orquestador.cliente_interno import ClienteInterno
from kallicode_workers.orquestador.trazas import (
    RETENCION_TRAZAS_LLM_MINIMA_DIAS,
    construir_refs_traza,
)
from sqlalchemy import text


def test_construir_refs_traza_es_determinista() -> None:
    """La ruta en Blob es determinista por (tenant, ticket, step_id)."""
    in1, out1 = construir_refs_traza("org_demo", "KC-1042", "st_abc123")
    in2, out2 = construir_refs_traza("org_demo", "KC-1042", "st_abc123")
    assert (in1, out1) == (in2, out2)
    assert in1 == "trazas-llm/org_demo/KC-1042/st_abc123/entrada.json"
    assert out1 == "trazas-llm/org_demo/KC-1042/st_abc123/salida.json"


@pytest_asyncio.fixture
async def entorno_trazas(monkeypatch):
    almacen_blob: dict[str, dict] = {}
    eventos_log: list[dict] = []

    async def subir_falso(
        ref: str,
        contenido: bytes,
        *,
        tenant_id: str,
        ticket_ref: str,
        step_id: str,
        tipo: str,
        content_type: str = "application/json",
    ) -> str:
        almacen_blob[ref] = {
            "contenido": contenido,
            "content_type": content_type,
            "metadata": {
                "tenant_id": tenant_id,
                "ticket_id": ticket_ref,
                "step_id": step_id,
                "tipo_traza": tipo,
                "retencion_minima_dias": str(RETENCION_TRAZAS_LLM_MINIMA_DIAS),
            },
        }
        return ref

    def firmar_falso(ref: str | None, minutos: int = 10) -> tuple[str | None, str | None]:
        if not ref or ref.startswith("mem://"):
            return None, None
        expira = (datetime.now(UTC) + timedelta(minutes=minutos)).isoformat()
        sig = f"signed-{minutos}m"
        return f"http://blob.local/evidencia/{ref}?sig={sig}&ttl={minutos}", expira

    monkeypatch.setattr(comun_mod, "subir_traza_blob", subir_falso)
    monkeypatch.setattr(trazas_mod, "subir_traza_blob", subir_falso)
    monkeypatch.setattr(audit_router, "url_descarga_si_existe", firmar_falso)

    orig_info = audit_router.log.info

    def capturar_log(evento: str, **kw):
        eventos_log.append({"evento": evento, **kw})
        return orig_info(evento, **kw)

    monkeypatch.setattr(audit_router.log, "info", capturar_log)

    tenant = f"org_traza84_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    ticket_id = str(uuid.uuid4())
    job_id = f"job_{uuid.uuid4().hex[:12]}"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org Trazas 084')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner Trazas', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        numero = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Ticket Trazas', 'Desc', 'feature', 'media', 'design', :u)
                       RETURNING numero"""
                ),
                {"id": ticket_id, "t": tenant, "u": owner},
            )
        ).scalar_one()
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado)
                   VALUES (:j, :t, :tk, 'design')"""
            ),
            {"j": job_id, "t": tenant, "tk": ticket_id},
        )
        await db.commit()

    api_app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=owner,
        tenant_id=tenant,
        rol="owner",
    )

    yield {
        "tenant": tenant,
        "owner": owner,
        "ticket_id": ticket_id,
        "numero": numero,
        "job_id": job_id,
        "blob": almacen_blob,
        "logs": eventos_log,
    }

    api_app.dependency_overrides.clear()
    async with sesion_sistema() as db:
        for tabla in (
            "core.llm_steps",
            "core.usage_tokens",
            "core.retention_policies",
            "core.jobs",
            "core.tickets",
            "core.ticket_counters",
            "core.users",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant})
        await db.commit()


@pytest.mark.integracion
async def test_ejecutar_paso_sube_trazas_y_devuelve_urls_firmadas_descargables(
    entorno_trazas,
    monkeypatch,
) -> None:
    """Ejecuta un paso LLM, consulta GET /audit/steps/{id}, descarga entrada/salida y verifica coincidencia."""
    tenant = entorno_trazas["tenant"]
    owner = entorno_trazas["owner"]
    ticket_id = entorno_trazas["ticket_id"]
    numero = entorno_trazas["numero"]
    job_id = entorno_trazas["job_id"]
    almacen_blob = entorno_trazas["blob"]
    logs = entorno_trazas["logs"]

    entrada_proveedor = {
        "ticket": numero,
        "prompt_contexto": "Implementar validación de firma HMAC en webhook entrante.",
        "archivos": ["apps/api/src/kallicode_api/routers/webhooks.py"],
    }
    salida_proveedor = {
        "resumen": "Se valida cabecera X-Hub-Signature-256 con hmac.compare_digest.",
        "alternativas": [{"id": "ALT-1", "recomendada": True}],
    }

    resultado_falso = SimpleNamespace(
        paso_id="design.digest",
        version="v2",
        modelo="claude-sonnet-4-20250514",
        tier=Tier.SONNET,
        confianza=0.94,
        validacion="ok",
        intentos=[{"intento": 1}],
        tokens_in=420,
        tokens_out=185,
        duracion_ms=710,
        salida=salida_proveedor,
    )
    monkeypatch.setattr(comun_mod, "ejecutar_paso", AsyncMock(return_value=resultado_falso))

    # Servidor interno ASGI para el ClienteInterno del orquestador
    internal_app = FastAPI()
    internal_app.include_router(internal_router.router, prefix="/internal/v1")

    cliente = ClienteInterno(base_url="http://internal.local/internal/v1", servicio="orquestador")
    await cliente._http.aclose()
    cliente._http = AsyncClient(
        transport=ASGITransport(app=internal_app),
        base_url="http://internal.local/internal/v1",
    )

    job = {
        "id": job_id,
        "tenant_id": tenant,
        "ticket_id": ticket_id,
        "ticket_numero": numero,
    }
    try:
        await comun_mod.ejecutar_y_publicar(
            "design.digest",
            entrada_proveedor,
            job=job,
            cliente=cliente,
            etapa="design",
            intento=1,
        )
    finally:
        await cliente.cerrar()

    async with sesion_sistema() as db:
        paso_db = await uno(
            db,
            "SELECT * FROM core.llm_steps WHERE tenant_id = :t AND job_id = :j",
            {"t": tenant, "j": job_id},
        )
    assert paso_db is not None
    step_id = paso_db["id"]
    esperado_in, esperado_out = construir_refs_traza(tenant, numero, step_id)
    assert paso_db["entrada_ref"] == esperado_in
    assert paso_db["salida_ref"] == esperado_out
    assert almacen_blob[esperado_in]["metadata"]["retencion_minima_dias"] == "365"

    # Consultar GET /api/v1/audit/steps/{step_id}
    async with AsyncClient(
        transport=ASGITransport(app=api_app), base_url="http://api.local"
    ) as api_client:
        resp = await api_client.get(f"/api/v1/audit/steps/{step_id}")
        assert resp.status_code == 200, resp.text
        cuerpo = resp.json()

    assert cuerpo["entrada_ref"] == esperado_in
    assert cuerpo["salida_ref"] == esperado_out
    assert cuerpo["entrada_url"] is not None
    assert cuerpo["salida_url"] is not None
    assert cuerpo["urls_expiran_en"] is not None

    # Verificar que el acceso quedó registrado en el log con el actor
    eventos_acceso = [e for e in logs if e["evento"] == "audit.paso.acceso_trazas"]
    assert len(eventos_acceso) >= 1
    assert eventos_acceso[-1]["actor_id"] == owner
    assert eventos_acceso[-1]["tenant_id"] == tenant
    assert eventos_acceso[-1]["step_id"] == step_id

    # Servidor HTTP Blob que valida firma SAS y sirve los bytes almacenados
    blob_app = FastAPI()

    @blob_app.get("/evidencia/{blob_path:path}")
    async def servir_blob(blob_path: str, request: Request) -> Response:
        if not request.query_params.get("sig"):
            raise HTTPException(status_code=403, detail="Falta firma SAS")
        item = almacen_blob.get(blob_path)
        if not item:
            raise HTTPException(status_code=404, detail="Blob no encontrado")
        return Response(content=item["contenido"], media_type=item["content_type"])

    async with AsyncClient(
        transport=ASGITransport(app=blob_app), base_url="http://blob.local"
    ) as blob_client:
        r_in = await blob_client.get(cuerpo["entrada_url"])
        r_out = await blob_client.get(cuerpo["salida_url"])
        assert r_in.status_code == 200
        assert r_out.status_code == 200
        assert json.loads(r_in.text) == entrada_proveedor
        assert json.loads(r_out.text) == salida_proveedor


@pytest.mark.integracion
async def test_housekeeping_purga_trazas_vencidas_segun_politica_retencion(
    entorno_trazas,
    monkeypatch,
) -> None:
    """Housekeeping purga trazas expiradas (>365d) y conserva las vigentes."""
    tenant = entorno_trazas["tenant"]
    ticket_id = entorno_trazas["ticket_id"]
    numero = entorno_trazas["numero"]
    job_id = entorno_trazas["job_id"]
    almacen_blob = entorno_trazas["blob"]

    step_antiguo = f"st_old_{uuid.uuid4().hex[:8]}"
    step_reciente = f"st_new_{uuid.uuid4().hex[:8]}"
    ref_old_in, ref_old_out = construir_refs_traza(tenant, numero, step_antiguo)
    ref_new_in, ref_new_out = construir_refs_traza(tenant, numero, step_reciente)

    for ref in (ref_old_in, ref_old_out, ref_new_in, ref_new_out):
        almacen_blob[ref] = {"contenido": b"{}", "content_type": "application/json", "metadata": {}}

    class _FakeBlobClient:
        def __init__(self, ref: str) -> None:
            self._ref = ref

        async def delete_blob(self) -> None:
            almacen_blob.pop(self._ref, None)

    class _FakeContainerClient:
        def get_blob_client(self, ref: str) -> _FakeBlobClient:
            return _FakeBlobClient(ref)

    class _FakeServiceClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get_container_client(self, _name: str) -> _FakeContainerClient:
            return _FakeContainerClient()

    from kallicode_core import adjuntos

    monkeypatch.setattr(adjuntos, "cliente_blob", lambda: _FakeServiceClient())

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.retention_policies (tenant_id, tabla, retention_days, action)
                   VALUES (:t, 'core.llm_steps', 365, 'drop')"""
            ),
            {"t": tenant},
        )
        await db.execute(
            text(
                """INSERT INTO core.llm_steps (id, tenant_id, job_id, ticket_id, etapa, paso,
                                               version_plantilla, modelo, tier, validacion,
                                               entrada_ref, salida_ref, creado_en)
                   VALUES (:i1, :t, :j, :tk, 'design', 'design.digest', 'v1', 'claude', 'sonnet', 'ok',
                           :ei1, :eo1, now() - interval '400 days'),
                          (:i2, :t, :j, :tk, 'design', 'design.digest', 'v1', 'claude', 'sonnet', 'ok',
                           :ei2, :eo2, now() - interval '10 days')"""
            ),
            {
                "i1": step_antiguo,
                "i2": step_reciente,
                "t": tenant,
                "j": job_id,
                "tk": ticket_id,
                "ei1": ref_old_in,
                "eo1": ref_old_out,
                "ei2": ref_new_in,
                "eo2": ref_new_out,
            },
        )
        res = await housekeeping.purgar_trazas_llm_vencidas(db)
        await db.commit()

    assert res["blobs_eliminados"] == 2
    assert ref_old_in not in almacen_blob
    assert ref_old_out not in almacen_blob
    assert ref_new_in in almacen_blob
    assert ref_new_out in almacen_blob
