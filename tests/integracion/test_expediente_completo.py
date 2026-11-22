"""Expediente completo congelado con Portal (KC-BE-08-6)."""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TypedDict

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.auditoria import registrar_evento
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema
from kallicode_workers.orquestador.trazas import construir_refs_traza, subir_traza_blob
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
PRESUPUESTO_P95_MS = 800.0


class DatosTicketCompleto(TypedDict):
    numero: str
    salidas_diff: dict[str, dict[str, str]]


@pytest_asyncio.fixture
async def ticket_completo(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DatosTicketCompleto]:
    settings = get_settings()
    if not settings.blob_connection_string:
        linea = next(
            linea
            for linea in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
            if linea.startswith("KC_BLOB_CONNECTION_STRING=")
        )
        monkeypatch.setattr(settings, "blob_connection_string", linea.split("=", 1)[1].strip('"'))
    tenant = f"org_exp86_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    ticket_id = f"tk_{uuid.uuid4().hex[:12]}"
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    salidas_diff: dict[str, dict[str, str]] = {}

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Expediente 086')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Olga Owner', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@expediente.test"},
        )
        numero = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets
                           (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa,
                            origen, reportado_por, pr_url, cerrado_en)
                       VALUES (:id, :t, 'Ticket completo', 'Recorrido completo a producción',
                               'mejora', 'alta', 'produccion', 'portal', :u,
                               'https://github.com/kallicode/app/pull/86', now())
                       RETURNING numero"""
                ),
                {"id": ticket_id, "t": tenant, "u": owner},
            )
        ).scalar_one()
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, terminado_en)
                   VALUES (:j, :t, :tk, 'produccion', 'P2', now())"""
            ),
            {"j": job_id, "t": tenant, "tk": ticket_id},
        )

        pasos = [
            *(("triage", f"triage.{i}") for i in range(1, 4)),
            *(("design", f"design.{i}") for i in range(1, 5)),
            ("build", "build.plan"),
            *(("build", f"build.diff.{i}") for i in range(1, 5)),
            ("build", "build.selfreview"),
            *(("qa", f"qa.{i}") for i in range(1, 4)),
            *(("security", f"security.{i}") for i in range(1, 3)),
            *(("deploy", f"deploy.{i}") for i in range(1, 3)),
        ]
        assert len(pasos) == 20
        for indice, (etapa, paso) in enumerate(pasos, start=1):
            step_id = f"st_{indice:02d}_{uuid.uuid4().hex[:8]}"
            ref_entrada, ref_salida = construir_refs_traza(tenant, numero, step_id)
            if paso.startswith("build.diff"):
                archivo = f"src/modulo_{indice}.py"
                diff = f"--- a/{archivo}\n+++ b/{archivo}\n@@ -1 +1 @@\n-old\n+new-{indice}\n"
                salidas_diff[ref_salida] = {
                    "diff": diff,
                    "rationale_en": f"Cambio aplicado {indice}",
                }
                await subir_traza_blob(
                    ref_salida,
                    json.dumps(salidas_diff[ref_salida]).encode(),
                    tenant_id=tenant,
                    ticket_ref=numero,
                    step_id=step_id,
                    tipo="salida",
                )
            await db.execute(
                text(
                    """INSERT INTO core.llm_steps
                           (id, tenant_id, job_id, ticket_id, etapa, paso, version_plantilla,
                            modelo, tier, confianza, validacion, reintentos, tokens_in,
                            tokens_out, duracion_ms, entrada_ref, salida_ref, creado_en)
                       VALUES (:id, :t, :j, :tk, :e, :p, '3', :m, :tier, 0.910,
                               'esquema_valido', 0, :tin, :tout, 120, :entrada, :salida,
                               now() + (:orden * interval '1 millisecond'))"""
                ),
                {
                    "id": step_id,
                    "t": tenant,
                    "j": job_id,
                    "tk": ticket_id,
                    "e": etapa,
                    "p": paso,
                    "m": "claude-fable-5" if indice % 5 == 0 else "gemini-2.5-flash",
                    "tier": "fable" if indice % 5 == 0 else "flash",
                    "tin": 100 + indice,
                    "tout": 20 + indice,
                    "entrada": ref_entrada,
                    "salida": ref_salida,
                    "orden": indice,
                },
            )
        await db.execute(
            text(
                """INSERT INTO core.job_transitions (tenant_id, job_id, de_estado, a_estado, creado_en)
                   VALUES (:t, :j, 'build', 'qa', now() + interval '13.5 milliseconds')"""
            ),
            {"t": tenant, "j": job_id},
        )

        await registrar_evento(
            db,
            tenant,
            evento="ticket_creado",
            resumen="Ticket solicitado desde el portal",
            actor_tipo="humano",
            actor_id=owner,
            ticket_id=numero,
            etapa="triage",
        )
        for gate in (1, 2, 3):
            sello = await registrar_evento(
                db,
                tenant,
                evento="gate_firmado",
                resumen=f"Gate {gate} aprobado",
                actor_tipo="humano",
                actor_id=owner,
                ticket_id=numero,
                etapa=("design", "qa", "deploy")[gate - 1],
                datos={"gate": gate},
            )
            await db.execute(
                text(
                    """INSERT INTO core.gate_signatures
                           (id, tenant_id, ticket_id, gate, accion, actor_id, comentario, sello)
                       VALUES (:id, :t, :tk, :g, 'aprobado', :u, :c, :s)"""
                ),
                {
                    "id": f"gs_{uuid.uuid4().hex[:12]}",
                    "t": tenant,
                    "tk": ticket_id,
                    "g": gate,
                    "u": owner,
                    "c": f"Firma del gate {gate}",
                    "s": sello,
                },
            )
        await registrar_evento(
            db,
            tenant,
            evento="deploy_ejecutado",
            resumen="Despliegue verificado en producción",
            actor_tipo="agente",
            actor_id="deploy",
            ticket_id=numero,
            etapa="deploy",
        )
        for formato, estado in (("csv", "lista"), ("pdf", "encolada")):
            export_id = f"exp_{uuid.uuid4().hex[:12]}"
            await db.execute(
                text(
                    """INSERT INTO core.audit_exports
                           (id, tenant_id, formato, ticket_id, estado, blob_ref, solicitado_por)
                       VALUES (:id, :t, :f, :tk, :e, :r, :u)"""
                ),
                {
                    "id": export_id,
                    "t": tenant,
                    "f": formato,
                    "tk": ticket_id,
                    "e": estado,
                    "r": f"{tenant}/exports/{export_id}.{formato}" if estado == "lista" else None,
                    "u": owner,
                },
            )
        await db.commit()

    monkeypatch.setitem(
        app.dependency_overrides,
        usuario_actual,
        lambda: UsuarioActual(user_id=owner, tenant_id=tenant, rol="owner", restringido=False),
    )
    yield {
        "numero": numero,
        "salidas_diff": salidas_diff,
    }
    async with cliente_blob() as cliente:
        contenedor = cliente.get_container_client(settings.blob_container_evidencia)
        for ref in salidas_diff:
            await contenedor.delete_blob(ref)

    async with sesion_sistema() as db:
        await db.execute(text("ALTER TABLE core.gate_signatures DISABLE TRIGGER USER"))
        await db.execute(text("DELETE FROM core.gate_signatures WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE core.gate_signatures ENABLE TRIGGER USER"))
        await db.execute(text("ALTER TABLE core.llm_steps DISABLE TRIGGER USER"))
        await db.execute(text("DELETE FROM core.llm_steps WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE core.llm_steps ENABLE TRIGGER USER"))
        for tabla in (
            "core.audit_exports",
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
async def test_ticket_completo_hasta_produccion_contiene_todo_y_es_integro(
    ticket_completo: DatosTicketCompleto,
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        respuesta = await client.get(f"/api/v1/audit/tickets/{ticket_completo['numero']}")

    assert respuesta.status_code == 200, respuesta.text
    expediente = respuesta.json()
    assert expediente["solicitante"]["nombre"] == "Olga Owner"
    assert expediente["solicitante"]["origen"] == "portal"
    pasos = [paso for etapa in expediente["etapas"] for paso in etapa["pasos"]]
    assert len(pasos) == 20
    assert all(p["tier"] in {"flash", "fable"} and p["tokens"] > 0 for p in pasos)
    assert len(expediente["firmas"]) == 3
    assert all(f["sello"] and len(f["sello"]) == 64 for f in expediente["firmas"])
    assert len(expediente["cambios"]) == 4
    for cambio in expediente["cambios"]:
        salida = ticket_completo["salidas_diff"][cambio["referencia"]]
        assert cambio["hash_sha256"] == hashlib.sha256(salida["diff"].encode()).hexdigest()
        assert cambio["archivo"].startswith("src/modulo_")
        assert cambio["pr_url"].endswith("/86")
    assert {(e["formato"], e["estado"]) for e in expediente["exportaciones"]} == {
        ("csv", "lista"),
        ("pdf", "encolada"),
    }
    assert expediente["integridad"]["alcance"] == "tramo_ticket"
    assert expediente["integridad"]["tramo_integro"] is True
    assert expediente["integridad"]["integra"] is True
    assert set(expediente["eventos"][0]) == {
        "id",
        "creado_en",
        "actor_tipo",
        "actor_id",
        "resumen",
        "sello",
    }


@pytest.mark.integracion
async def test_los_diffs_de_un_intento_sin_confirmar_solo_figuran_como_pasos(
    ticket_completo: DatosTicketCompleto,
) -> None:
    ref = next(iter(ticket_completo["salidas_diff"]))
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.llm_steps
                    (id, tenant_id, job_id, ticket_id, etapa, paso, version_plantilla,
                     modelo, tier, validacion, tokens_in, tokens_out, duracion_ms,
                     entrada_ref, salida_ref, creado_en)
                SELECT :id, tenant_id, job_id, ticket_id, etapa, 'build.diff.99', version_plantilla,
                       modelo, tier, validacion, tokens_in, tokens_out, duracion_ms,
                       entrada_ref, salida_ref, creado_en + interval '1 second'
                  FROM core.llm_steps WHERE salida_ref = :ref
            """),
            {"id": f"st_{uuid.uuid4().hex[:12]}", "ref": ref},
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        respuesta = await client.get(f"/api/v1/audit/tickets/{ticket_completo['numero']}")

    assert respuesta.status_code == 200, respuesta.text
    expediente = respuesta.json()
    pasos = [paso["paso"] for etapa in expediente["etapas"] for paso in etapa["pasos"]]
    assert "build.diff.99" in pasos
    assert len(expediente["cambios"]) == 4


@pytest.mark.integracion
async def test_presupuesto_p95_con_20_pasos_3_firmas_y_4_diffs(
    ticket_completo: DatosTicketCompleto,
) -> None:
    latencias: list[float] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        calentamiento = await client.get(f"/api/v1/audit/tickets/{ticket_completo['numero']}")
        assert calentamiento.status_code == 200
        assert len(calentamiento.json()["cambios"]) == 4
        for _ in range(25):
            inicio = time.perf_counter()
            respuesta = await client.get(f"/api/v1/audit/tickets/{ticket_completo['numero']}")
            latencias.append((time.perf_counter() - inicio) * 1000)
            assert respuesta.status_code == 200
            assert len(respuesta.json()["cambios"]) == 4

    indice = max(0, math.ceil(0.95 * len(latencias)) - 1)
    p95 = sorted(latencias)[indice]
    assert p95 < PRESUPUESTO_P95_MS, f"p95={p95:.1f} ms; presupuesto={PRESUPUESTO_P95_MS:.0f} ms"


def test_openapi_publicado_congela_el_expediente_completo() -> None:
    contrato = yaml.safe_load((ROOT / "contratos/api-v1.yaml").read_text(encoding="utf-8"))
    publicados = contrato["components"]["schemas"]
    vigentes = app.openapi()["components"]["schemas"]
    for nombre, esquema in vigentes.items():
        if nombre.startswith("Expediente"):
            assert publicados[nombre] == esquema
    expediente = publicados["ExpedienteOut"]
    assert expediente["required"] == [
        "solicitante",
        "integridad",
        "firmas",
        "etapas",
        "cambios",
        "exportaciones",
        "eventos",
    ]
    assert expediente["properties"]["cambios"]["items"]["$ref"].endswith("ExpedienteCambioOut")
    assert expediente["properties"]["exportaciones"]["items"]["$ref"].endswith(
        "ExpedienteExportacionOut"
    )
