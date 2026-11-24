"""E2E del onboarding de un tenant nuevo (KC-PT-07-7), contra el backend real.

Un tenant recién creado (sin conexiones, sin mapa de ramas y con la fábrica apagada) recibe
exactamente las solicitudes que manda el asistente del portal, leídas de
onboarding_desde_portal.json; el portal comprueba en
kallicode-portal/src/vistas/onboarding/OnboardingCompleto.test.tsx que manda eso mismo. Está
en apps/api/tests para que corra en el CI (job backend, con PostgreSQL y Redis).
Así, si portal y backend dejan de entenderse (como pasó con las claves de config y los
verificadores de KC-BE-07-1), falla una de las dos pruebas.

Base de datos y bóveda reales; GitHub y Jira responden con las grabaciones VCR de
tests/fixtures/vcr (las mismas de KC-BE-07-1).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient
from kallicode_api.servicios import boveda
from kallicode_core.auditoria import verificar_cadena
from kallicode_core.db import sesion_sistema, todos, uno
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

AQUI = Path(__file__).parent
SOLICITUDES = json.loads((AQUI / "onboarding_desde_portal.json").read_text(encoding="utf-8"))[
    "solicitudes"
]
VCR_DIR = AQUI.parents[3] / "tests" / "fixtures" / "vcr"
LINEAS_DEL_PLAN = 2


def _grabar(router: respx.MockRouter, proveedor: str) -> None:
    """Respuestas de la grabación «valido» del proveedor (sin importar los parámetros)."""
    cassette = json.loads((VCR_DIR / f"{proveedor}.json").read_text(encoding="utf-8"))
    for item in cassette["escenarios"]["valido"]:
        url = httpx.URL(item["url"])
        router.route(method=item["method"], scheme=url.scheme, host=url.host, path=url.path).mock(
            return_value=httpx.Response(
                status_code=item["status_code"],
                headers=item.get("headers") or {},
                json=item.get("body"),
            )
        )


@pytest_asyncio.fixture
async def tenant_nuevo():
    """Organización recién creada: plan, suscripción y su owner; nada configurado."""
    tenant_id = f"org_ob_{uuid.uuid4().hex[:10]}"
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    plan_codigo = f"plan_ob_{uuid.uuid4().hex[:10]}"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Tenant nuevo E2E')"),
            {"id": tenant_id},
        )
        await db.execute(
            text("INSERT INTO core.plans (codigo, nombre, lineas) VALUES (:c, 'Plan E2E', :n)"),
            {"c": plan_codigo, "n": LINEAS_DEL_PLAN},
        )
        await db.execute(
            text("""
                INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
                VALUES (:id, :t, :c, current_date, current_date + 30)
            """),
            {"id": f"sub_{uuid.uuid4().hex[:8]}", "t": tenant_id, "c": plan_codigo},
        )
        await db.execute(
            text("""
                INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado)
                VALUES (:id, :t, :email, 'Owner E2E', 'owner', 'activo')
            """),
            {"id": user_id, "t": tenant_id, "email": f"{user_id}@{tenant_id}.test"},
        )
        await db.commit()

    token = emitir_access_token(user_id, tenant_id, "owner")
    yield {"tenant_id": tenant_id, "headers": {"Authorization": f"Bearer {token}"}}

    async with sesion_sistema() as db:
        conexiones = await todos(
            db, "SELECT id FROM core.connections WHERE tenant_id=:t", {"t": tenant_id}
        )
        for tabla in (
            "cm.curation_queue",
            "cm.files",
            "cm.repo_snapshots",
            "core.production_lines",
            "core.branch_map",
            "core.connections",
            "core.users",
            "core.subscriptions",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant_id})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(
            text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant_id}
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.plans WHERE codigo=:c"), {"c": plan_codigo})
        await db.commit()
    for cn in conexiones:
        await boveda.eliminar(tenant_id, f"cn--{cn['id']}")


async def _publicar_ingesta(tenant_id: str, fase: int, cobertura: int) -> None:
    async with sesion_sistema() as db:
        params = {"t": tenant_id, "snap": f"snap_{tenant_id}"}
        await db.execute(
            text("""
                INSERT INTO cm.repo_snapshots
                    (id, tenant_id, commit_sha, rama, estado, fase_actual, stats, finalizado_en)
                VALUES
                    (:snap, :t, repeat('0', 40), 'main', 'completo', 'completo',
                     jsonb_build_object('checkpoint_fase', CAST(:fase AS integer)), now())
            """),
            {**params, "fase": fase},
        )
        await db.execute(
            text("""
                INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id)
                SELECT 'file_' || i, :t, 'src/f' || i || '.py', 'python',
                       repeat('a', 64), :snap
                FROM generate_series(1, 20) AS serie(i)
            """),
            params,
        )
        await db.execute(
            text("""
                INSERT INTO vec.definition_embeddings
                    (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash)
                SELECT 'emb_' || id, tenant_id, id, 'file', path, path,
                       CAST(:embedding AS vector), sha256
                FROM cm.files
                WHERE tenant_id=:t AND snapshot_id=:snap
                ORDER BY id
                LIMIT :n
            """),
            {
                **params,
                "embedding": "[" + ",".join(["0.01"] * 1024) + "]",
                "n": cobertura * 20 // 100,
            },
        )
        await db.execute(
            text("""
                INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
                VALUES ('cq_onboarding', :t, 'enlace', '{"tipo": "CALLS"}')
            """),
            {"t": tenant_id},
        )
        await db.commit()


@pytest.mark.parametrize(
    ("fase", "cobertura", "pendientes"),
    [
        pytest.param(6, 60, ["cobertura_grafo"], id="cobertura-insuficiente"),
        pytest.param(5, 75, ["fase_grafo"], id="fase-insuficiente"),
        pytest.param(6, 75, [], id="activa-e-idempotente"),
    ],
)
async def test_onboarding_de_un_tenant_nuevo_con_lo_que_manda_el_portal(
    client: AsyncClient, tenant_nuevo, fase: int, cobertura: int, pendientes: list[str]
) -> None:
    tenant_id = tenant_nuevo["tenant_id"]
    h = tenant_nuevo["headers"]

    # Tenant nuevo: nada configurado y la fábrica no se puede encender.
    r = await client.get("/api/v1/connections", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["conexiones"] == [] and r.json()["mapa_ramas"] is None
    r = await client.post("/api/v1/onboarding/complete", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["codigo"] == "ONBOARDING_INCOMPLETO"
    assert r.json()["error"]["detalle"]["pendientes"] == [
        "repositorio",
        "mapa_ramas",
        "fase_grafo",
        "cobertura_grafo",
    ]
    r = await client.get("/api/v1/onboarding/codemapping/status", headers=h)
    assert r.status_code == 200, r.text
    grafo = r.json()
    assert grafo["codigo"] == "GRAFO_METRICAS_NO_PUBLICADAS"
    for campo in ("fase", "progreso", "cobertura", "curacion_pendiente", "ultimo_refresh"):
        assert grafo[campo] is None

    # Las solicitudes del asistente, en el orden en que las manda el portal.
    creadas: dict[str, str] = {}
    for s in SOLICITUDES:
        if s["paso"] == "activacion":
            await _publicar_ingesta(tenant_id, fase, cobertura)
            r = await client.get("/api/v1/onboarding/codemapping/status", headers=h)
            assert r.status_code == 200, r.text
            grafo = r.json()
            assert grafo["fase"] == fase
            assert grafo["progreso"] == 1.0
            assert grafo["cobertura"] == cobertura
            assert grafo["curacion_pendiente"] == 1
            assert grafo["ultimo_refresh"] is not None
            assert grafo["codigo"] is None
        with respx.mock(assert_all_called=False) as router:
            if s["paso"] in ("repositorio", "herramientas"):
                _grabar(router, s["cuerpo"]["proveedor"])
            r = await client.request(s["metodo"], s["ruta"], json=s["cuerpo"], headers=h)

        if s["paso"] in ("repositorio", "herramientas"):
            assert r.status_code == 201, f"{s['paso']}: {r.text}"
            assert r.json()["estado"] == "conectada"
            creadas[s["paso"]] = r.json()["id"]
            if s["cuerpo"]["categoria"] == "tickets":
                assert r.json()["webhook_token"].startswith("wh_")
        elif s["paso"] == "ramas":
            assert r.status_code == 200, r.text
            assert r.json()["mapa_ramas"] == s["cuerpo"]
        else:
            if pendientes:
                assert r.status_code == 409, r.text
                error = r.json()["error"]
                assert error["codigo"] == "ONBOARDING_INCOMPLETO"
                assert error["detalle"]["pendientes"] == pendientes
                async with sesion_sistema() as db:
                    filas = await todos(
                        db,
                        "SELECT numero FROM core.production_lines WHERE tenant_id=:t",
                        {"t": tenant_id},
                    )
                assert filas == []
                return

            esperado = {
                "activado": True,
                "lineas_activadas": LINEAS_DEL_PLAN,
                "pendientes": [],
            }
            assert r.status_code == 200, r.text
            assert r.json() == esperado
            for _ in range(2):
                repetida = await client.post("/api/v1/onboarding/complete", headers=h)
                assert repetida.status_code == 200, repetida.text
                assert repetida.json() == esperado

    # Lo configurado queda como lo verá el asistente al volver.
    r = await client.get("/api/v1/connections", headers=h)
    conexiones = {c["id"]: c for c in r.json()["conexiones"]}
    assert {c["estado"] for c in conexiones.values()} == {"conectada"}
    assert conexiones[creadas["repositorio"]]["nombre"] == "app principal"
    assert r.json()["mapa_ramas"] == {
        "feature": "feature/*",
        "staging": "develop",
        "produccion": "main",
    }

    # Las credenciales quedan solo en la bóveda, con el repo_url que lee CodeMapping.
    cuerpo_repo = next(s for s in SOLICITUDES if s["paso"] == "repositorio")["cuerpo"]
    guardadas = await boveda.leer(tenant_id, f"cn--{creadas['repositorio']}")
    assert guardadas == cuerpo_repo["credenciales"]
    async with sesion_sistema() as db:
        filas = await todos(
            db,
            "SELECT config::text AS config FROM core.connections WHERE tenant_id=:t",
            {"t": tenant_id},
        )
        assert not any("token-sintetico" in f["config"] for f in filas)

        # Fábrica encendida, con las líneas del plan y el hito en una auditoría íntegra.
        org = await uno(
            db, "SELECT fabrica_activa FROM core.organizations WHERE id=:t", {"t": tenant_id}
        )
        assert org["fabrica_activa"] is True
        lineas = await todos(
            db,
            "SELECT numero FROM core.production_lines WHERE tenant_id=:t ORDER BY numero",
            {"t": tenant_id},
        )
        assert [fila["numero"] for fila in lineas] == list(range(1, LINEAS_DEL_PLAN + 1))
        eventos = [
            e["evento"]
            for e in await todos(
                db,
                "SELECT evento FROM audit.audit_events WHERE tenant_id=:t ORDER BY id",
                {"t": tenant_id},
            )
        ]
        assert eventos.count("fabrica_activada") == 1
        assert (await verificar_cadena(db, tenant_id))["integra"] is True
