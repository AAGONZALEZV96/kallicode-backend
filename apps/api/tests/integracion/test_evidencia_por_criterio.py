"""Evidencia inmutable trazada por criterio (KC-BE-08-5).

Testing sube la corrida de QA a POST /internal/v1/tickets/{numero}/evidence con cada evidencia
como la sella su EvidenceSealer: tipo, blob_ref canónico evidence/{tenant}/{job}/{criterio}/
{archivo} y SHA-256. El backend comprueba que la ruta sea del tenant, de un job del ticket y del
criterio de su fila; no deja registrar otra vez una ruta con otro hash; sella la huella de la
evidencia en la cadena de auditoría; y el portal recibe cada evidencia con su enlace firmado.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.esquemas.interna import CriterioResultadoIn
from kallicode_api.main_interna import crear_app_interna
from kallicode_core import comercial
from kallicode_core.auditoria import verificar_cadena
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, todos
from kallicode_core.redis_keys import clave_tenant
from kallicode_core.seguridad import emitir_access_token, emitir_svc_token
from pydantic import ValidationError
from sqlalchemy import text

AQUI = Path(__file__).parent
SHA_A = "a" * 64
SHA_B = "b" * 64


def _evidencia(ref: str, sha: str = SHA_A, tipo: str = "screenshot") -> dict:
    return {"tipo": tipo, "blob_ref": ref, "sha256": sha}


# ---- el contrato, sin base de datos ----


@pytest.mark.parametrize(
    "evidencia",
    [
        # Lo que manda hoy kallicode_testing.internal_api_client: url en vez de blob_ref.
        {"tipo": "screenshot", "url": "http://localhost:10000/x.png", "sha256": SHA_A},
        _evidencia("evidence/org/job/AC-1/shot.png", tipo="foto"),
        _evidencia("evidence/org/job/AC-1/shot.png", sha="ABC"),
        {"tipo": "screenshot", "blob_ref": "evidence/org/job/AC-1/shot.png"},
        _evidencia("evidence/org/job/AC-1/../../otro/shot.png"),
        _evidencia("evidence/org/job/AC-1/..shot.png"),
        _evidencia("exports/org/job/AC-1/shot.png"),
        _evidencia("evidence/org/job/AC-1/sub/shot.png"),
        _evidencia("evidence/org/job/AC-2/shot.png"),
    ],
    ids=[
        "url-de-testing",
        "tipo-desconocido",
        "sha-invalido",
        "sin-sha",
        "traversal",
        "punto-al-inicio",
        "otro-prefijo",
        "subcarpeta",
        "otro-criterio",
    ],
)
def test_evidencias_que_el_contrato_rechaza(evidencia: dict) -> None:
    with pytest.raises(ValidationError):
        CriterioResultadoIn.model_validate(
            {"criterio_id": "AC-1", "resultado": "pasa", "evidencias": [evidencia]}
        )


def test_una_evidencia_como_la_sella_testing() -> None:
    fila = CriterioResultadoIn.model_validate(
        {
            "criterio_id": "AC-12",
            "resultado": "pasa",
            "evidencias": [
                _evidencia("evidence/org_demo/job_01ab/AC-12/test-failed-1.png"),
                _evidencia("evidence/org_demo/job_01ab/AC-12/trace.zip", SHA_B, "trace"),
            ],
        }
    )
    e = fila.evidencias[0]
    assert (e.tenant_id, e.job_id, e.criterio_id) == ("org_demo", "job_01ab", "AC-12")


def test_la_misma_evidencia_no_se_repite_en_el_criterio() -> None:
    ref = "evidence/org/job/AC-1/shot.png"
    with pytest.raises(ValidationError, match="repetidas"):
        CriterioResultadoIn.model_validate(
            {
                "criterio_id": "AC-1",
                "resultado": "pasa",
                "evidencias": [_evidencia(ref), _evidencia(ref)],
            }
        )


# ---- de punta a punta: API interna → base de datos → auditoría → portal ----


def _conexion_emulador() -> str:
    linea = next(
        x
        for x in (AQUI.parents[3] / ".env.example").read_text(encoding="utf-8").splitlines()
        if x.startswith("KC_BLOB_CONNECTION_STRING=")
    )
    return linea.split("=", 1)[1].strip().strip('"')


@pytest_asyncio.fixture
async def ticket_en_qa(monkeypatch):
    """Tenant con owner, un ticket en QA con su job y una spec aprobada con AC-1 y AC-2."""
    settings = get_settings()
    if settings.env == "local" and not settings.blob_connection_string:
        # Firmar un enlace no necesita Azurite: basta la clave del emulador.
        monkeypatch.setattr(settings, "blob_connection_string", _conexion_emulador())
    tenant = f"org_ev_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    ticket_id = str(uuid.uuid4())
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    otro_ticket_id = str(uuid.uuid4())
    otro_job_id = f"job_{uuid.uuid4().hex[:10]}"
    spec_id = f"sp_{uuid.uuid4().hex[:10]}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Evidencia QA')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner QA', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        numeros = {}
        for tk, jb in ((ticket_id, job_id), (otro_ticket_id, otro_job_id)):
            numeros[tk] = (
                await db.execute(
                    text(
                        """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                     prioridad, etapa)
                           VALUES (:id, :t, 'Ticket en QA', 'x', 'bug', 'media', 'qa')
                           RETURNING numero"""
                    ),
                    {"id": tk, "t": tenant},
                )
            ).scalar_one()
            await db.execute(
                text(
                    """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                       VALUES (:j, :t, :tk, 'qa', 'P3')"""
                ),
                {"j": jb, "t": tenant, "tk": tk},
            )
        await db.execute(
            text(
                """INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref,
                                           scope_paths, estado)
                   VALUES (:id, :t, :tk, 1, 'mem://spec.md', :sp, 'aprobada')"""
            ),
            {"id": spec_id, "t": tenant, "tk": ticket_id, "sp": ["src/"]},
        )
        for criterio in ("AC-1", "AC-2"):
            await db.execute(
                text(
                    """INSERT INTO core.spec_criteria
                       (spec_id, tenant_id, criterio_id, given_txt, when_txt, then_txt)
                       VALUES (:s, :t, :c, 'g', 'w', 't')"""
                ),
                {"s": spec_id, "t": tenant, "c": criterio},
            )
        await db.commit()
    await comercial.redis_cliente().set(clave_tenant(tenant, "pipeline", "disponible"), "1")

    yield {
        "tenant": tenant,
        "numero": numeros[ticket_id],
        "job_id": job_id,
        "otro_job_id": otro_job_id,
        "owner": {"Authorization": f"Bearer {emitir_access_token(owner, tenant, 'owner')}"},
    }

    async with sesion_sistema() as db:
        for tabla in (
            "core.idempotency_keys",
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


async def _subir(ticket: dict, corrida: int, matriz: list[dict]):
    """POST de la corrida como lo hace Testing (token de servicio e Idempotency-Key)."""
    cabeceras = {
        "Authorization": f"Bearer {emitir_svc_token('orquestador', ticket['tenant'])}",
        "Idempotency-Key": uuid.uuid4().hex,
    }
    cuerpo = {"corrida": corrida, "matriz": matriz, "regresion": {}}
    async with AsyncClient(
        transport=ASGITransport(app=crear_app_interna()), base_url="http://test"
    ) as c:
        return await c.post(
            f"/internal/v1/tickets/{ticket['numero']}/evidence", json=cuerpo, headers=cabeceras
        )


def _ref(ticket: dict, criterio: str, archivo: str, job: str | None = None) -> str:
    return f"evidence/{ticket['tenant']}/{job or ticket['job_id']}/{criterio}/{archivo}"


def _matriz(ac1: list[dict], ac2: list[dict] | None = None, resultado1: str = "pasa"):
    return [
        {"criterio_id": "AC-1", "resultado": resultado1, "evidencias": ac1},
        {"criterio_id": "AC-2", "resultado": "pasa", "evidencias": ac2 or []},
    ]


async def _corridas(tenant: str) -> int:
    async with sesion_sistema() as db:
        filas = await todos(db, "SELECT id FROM core.qa_runs WHERE tenant_id=:t", {"t": tenant})
    return len(filas)


async def _eventos(tenant: str, evento: str) -> list[dict]:
    async with sesion_sistema() as db:
        return await todos(
            db,
            "SELECT datos FROM audit.audit_events WHERE tenant_id=:t AND evento=:e ORDER BY id",
            {"t": tenant, "e": evento},
        )


@pytest.mark.integracion
async def test_la_evidencia_se_registra_por_criterio_y_el_portal_la_recibe_con_enlace(
    client: AsyncClient, ticket_en_qa
) -> None:
    captura = _evidencia(_ref(ticket_en_qa, "AC-1", "test-failed-1.png"))
    traza = _evidencia(_ref(ticket_en_qa, "AC-2", "trace.zip"), SHA_B, "trace")

    r = await _subir(ticket_en_qa, 1, _matriz([captura], [traza]))

    assert r.status_code == 201, r.text
    p = await client.get(
        f"/api/v1/tickets/{ticket_en_qa['numero']}/evidence", headers=ticket_en_qa["owner"]
    )
    assert p.status_code == 200, p.text
    por_criterio = {m["criterio_id"]: m["evidencias"] for m in p.json()["matriz"]}
    for criterio, enviada in (("AC-1", captura), ("AC-2", traza)):
        [recibida] = por_criterio[criterio]
        assert {k: recibida[k] for k in ("tipo", "blob_ref", "sha256")} == enviada
        enlace = urlparse(recibida["download_url"])
        assert enlace.scheme in ("http", "https")
        assert enlace.path.endswith(f"/evidencia/{enviada['blob_ref']}")
        assert "sig=" in enlace.query
        assert recibida["expira_en"]

    # La huella de las evidencias queda sellada en la cadena de auditoría.
    filas = sorted([["AC-1", captura["blob_ref"], SHA_A], ["AC-2", traza["blob_ref"], SHA_B]])
    esperada = hashlib.sha256(json.dumps(filas, separators=(",", ":")).encode()).hexdigest()
    [corrida] = await _eventos(ticket_en_qa["tenant"], "qa_corrida")
    assert corrida["datos"]["total_evidencias"] == 2
    assert corrida["datos"]["evidencias_sha256"] == esperada
    async with sesion_sistema() as db:
        assert (await verificar_cadena(db, ticket_en_qa["tenant"]))["integra"] is True


@pytest.mark.integracion
async def test_una_evidencia_de_otro_tenant_no_se_registra(ticket_en_qa) -> None:
    ajena = _evidencia(f"evidence/org_otro/{ticket_en_qa['job_id']}/AC-1/shot.png")

    r = await _subir(ticket_en_qa, 1, _matriz([ajena]))

    assert r.status_code == 422, r.text
    assert r.json()["error"]["codigo"] == "EVIDENCIA_FUERA_DE_ALCANCE"
    assert await _corridas(ticket_en_qa["tenant"]) == 0


@pytest.mark.integracion
@pytest.mark.parametrize("job", ["otro_job_id", "inexistente"])
async def test_una_evidencia_de_un_job_de_otro_ticket_no_se_registra(ticket_en_qa, job) -> None:
    job_id = ticket_en_qa.get(job, "job_que_no_existe")
    r = await _subir(
        ticket_en_qa, 1, _matriz([_evidencia(_ref(ticket_en_qa, "AC-1", "a.png", job_id))])
    )

    assert r.status_code == 422, r.text
    assert r.json()["error"]["codigo"] == "EVIDENCIA_FUERA_DE_ALCANCE"
    assert await _corridas(ticket_en_qa["tenant"]) == 0


async def _volver_a_qa(job_id: str) -> None:
    """La corrida anterior movió el job a gate2; para otra corrida vuelve a QA."""
    async with sesion_sistema() as db:
        await db.execute(text("UPDATE core.jobs SET estado='qa' WHERE id=:j"), {"j": job_id})
        await db.commit()


@pytest.mark.integracion
async def test_una_evidencia_registrada_no_vuelve_con_otro_hash(ticket_en_qa) -> None:
    ref = _ref(ticket_en_qa, "AC-1", "shot.png")
    primera = await _subir(ticket_en_qa, 1, _matriz([_evidencia(ref)]))
    assert primera.status_code == 201, primera.text

    # La misma evidencia, con el mismo hash, se puede citar en otra corrida.
    await _volver_a_qa(ticket_en_qa["job_id"])
    repetida = await _subir(ticket_en_qa, 2, _matriz([_evidencia(ref)]))
    assert repetida.status_code == 201, repetida.text

    await _volver_a_qa(ticket_en_qa["job_id"])
    alterada = await _subir(ticket_en_qa, 3, _matriz([_evidencia(ref, SHA_B)]))
    assert alterada.status_code == 409, alterada.text
    assert alterada.json()["error"]["codigo"] == "EVIDENCIA_ALTERADA"
    assert await _corridas(ticket_en_qa["tenant"]) == 2


@pytest.mark.integracion
async def test_la_misma_evidencia_en_dos_filas_se_rechaza(ticket_en_qa) -> None:
    ref = _ref(ticket_en_qa, "AC-1", "shot.png")
    fila = {"criterio_id": "AC-1", "resultado": "pasa", "evidencias": [_evidencia(ref)]}
    r = await _subir(ticket_en_qa, 1, [fila, fila, _matriz([])[1]])

    assert r.status_code == 422, r.text
    assert "más de un criterio" in r.text
    assert await _corridas(ticket_en_qa["tenant"]) == 0


@pytest.mark.integracion
async def test_una_corrida_con_flaky_audita_un_solo_qa_flaky(ticket_en_qa) -> None:
    # Antes el evento qa_flaky se registraba dos veces por corrida.
    r = await _subir(ticket_en_qa, 1, _matriz([], resultado1="flaky"))

    assert r.status_code == 201, r.text
    assert r.json()["criterios_flaky"] == ["AC-1"]
    assert len(await _eventos(ticket_en_qa["tenant"], "qa_flaky")) == 1
    [corrida] = await _eventos(ticket_en_qa["tenant"], "qa_corrida")
    assert "evidencias_sha256" not in corrida["datos"]
