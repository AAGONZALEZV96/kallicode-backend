"""KC-BE-07-1 (BE-F20): Tests de conexión reales por proveedor con grabaciones VCR,
verificación de permisos, detección de privilegios excesivos en BD y rotación segura."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import asyncpg
import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_api.servicios import boveda, integraciones
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.seguridad import emitir_access_token
from sqlalchemy import text

VCR_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "vcr"


def _cargar_vcr(router: respx.MockRouter, proveedor: str, escenario: str) -> None:
    """Reproduce sobre respx una grabación VCR JSON del proveedor y escenario dados."""
    cassette = json.loads((VCR_DIR / f"{proveedor}.json").read_text(encoding="utf-8"))
    interacciones = cassette["escenarios"][escenario]
    for item in interacciones:
        router.request(item["method"], item["url"]).mock(
            return_value=httpx.Response(
                status_code=item["status_code"],
                headers=item.get("headers") or {},
                json=item.get("body"),
            )
        )


# ---------------------------------------------------------------------------
# 1. Grabaciones VCR por proveedor HTTP (GitHub, GitLab, Jira, GitHub Actions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("proveedor", "config", "credenciales", "permisos_esperados"),
    [
        (
            "github",
            {"repo": "kallicode/demo-app"},
            {"token": "ghp_valid_token_123"},
            ["repo:read", "repo:write", "pr:write", "webhooks:manage"],
        ),
        (
            "gitlab",
            {"proyecto": "kallicode/demo-app"},
            {"token": "glpat-valid-token-123"},
            ["read_repository", "write_repository", "merge_requests:write"],
        ),
        (
            "jira",
            {"url": "https://kallicode.atlassian.net"},
            {"email": "bot@kallicode.dev", "token": "ATATT3x_valid_token"},
            ["read:issues", "write:issues", "webhooks:manage"],
        ),
        (
            "github_actions",
            {"repo": "kallicode/demo-app"},
            {"token": "ghp_valid_actions_123"},
            ["workflows:read", "workflows:dispatch"],
        ),
    ],
)
async def test_proveedor_http_credencial_valida_vcr(
    proveedor: str,
    config: dict,
    credenciales: dict,
    permisos_esperados: list[str],
) -> None:
    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, proveedor, "valido")
        res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is True
    assert res["permisos"] == permisos_esperados
    assert isinstance(res["latencia_ms"], int)
    assert res["latencia_ms"] >= 1


@pytest.mark.parametrize(
    ("proveedor", "config", "credenciales"),
    [
        ("github", {"repo": "kallicode/demo-app"}, {"token": "ghp_revoked"}),
        ("gitlab", {"proyecto": "kallicode/demo-app"}, {"token": "glpat-revoked"}),
        (
            "jira",
            {"url": "https://kallicode.atlassian.net"},
            {"email": "bot@kallicode.dev", "token": "revoked"},
        ),
        ("github_actions", {"repo": "kallicode/demo-app"}, {"token": "ghp_revoked"}),
    ],
)
async def test_proveedor_http_credencial_revocada_vcr(
    proveedor: str,
    config: dict,
    credenciales: dict,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, proveedor, "revocada")
        res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is False
    assert "401" in res["mensaje"] or "inválid" in res["mensaje"].lower()


@pytest.mark.parametrize(
    ("proveedor", "config", "credenciales", "pista_remediacion"),
    [
        (
            "github",
            {"repo": "kallicode/demo-app"},
            {"token": "ghp_readonly"},
            "push",
        ),
        (
            "gitlab",
            {"proyecto": "kallicode/demo-app"},
            {"token": "glpat-reporter"},
            "Developer",
        ),
        (
            "jira",
            {"url": "https://kallicode.atlassian.net"},
            {"email": "bot@kallicode.dev", "token": "no_admin"},
            "webhooks:manage",
        ),
        (
            "github_actions",
            {"repo": "kallicode/demo-app"},
            {"token": "ghp_no_workflow_scope"},
            "workflow",
        ),
    ],
)
async def test_proveedor_http_permisos_insuficientes_vcr(
    proveedor: str,
    config: dict,
    credenciales: dict,
    pista_remediacion: str,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, proveedor, "permisos_insuficientes")
        res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is False
    assert pista_remediacion in res["mensaje"]


@pytest.mark.parametrize(
    ("proveedor", "config", "credenciales"),
    [
        ("github", {"repo": "kallicode/demo-app"}, {"token": "ghp_any"}),
        ("gitlab", {"proyecto": "kallicode/demo-app"}, {"token": "glpat-any"}),
        (
            "jira",
            {"url": "https://kallicode.atlassian.net"},
            {"email": "bot@kallicode.dev", "token": "any"},
        ),
        ("github_actions", {"repo": "kallicode/demo-app"}, {"token": "ghp_any"}),
    ],
)
async def test_proveedor_http_caido_vcr(
    proveedor: str,
    config: dict,
    credenciales: dict,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, proveedor, "caido")
        res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is False
    assert res.get("proveedor_caido") is True


# ---------------------------------------------------------------------------
# 2. Proveedor PostgreSQL: válida, revocada, permisos insuficientes/excesivos, caída
# ---------------------------------------------------------------------------


def _mock_conn_pg(*, rol_row: dict, grants_datos: int, total_tablas: int | None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=rol_row)
    conn.fetchval = AsyncMock(side_effect=[grants_datos, total_tablas])
    conn.close = AsyncMock()
    return conn


async def test_postgresql_credencial_valida_solo_metadatos() -> None:
    conn = _mock_conn_pg(
        rol_row={
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": False,
        },
        grants_datos=0,
        total_tablas=14,
    )
    with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "database": "erp"},
            {"usuario": "kallicode_meta", "password": "secret"},
        )

    assert res["ok"] is True
    assert res["permisos"] == ["information_schema:read"]
    assert "14 tablas" in res["mensaje"]
    assert res["latencia_ms"] >= 1
    conn.close.assert_awaited_once()


async def test_postgresql_credencial_revocada() -> None:
    with patch(
        "asyncpg.connect",
        new=AsyncMock(side_effect=asyncpg.InvalidPasswordError("password authentication failed")),
    ):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "database": "erp"},
            {"usuario": "kallicode_meta", "password": "wrong"},
        )

    assert res["ok"] is False
    assert "inválida" in res["mensaje"].lower()


async def test_postgresql_privilegios_excesivos() -> None:
    conn = _mock_conn_pg(
        rol_row={
            "rolsuper": True,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": True,
        },
        grants_datos=0,
        total_tablas=14,
    )
    with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "database": "erp"},
            {"usuario": "super_admin", "password": "secret"},
        )

    assert res["ok"] is False
    assert res.get("permisos_excesivos") is True
    assert "information_schema" in res["mensaje"]
    conn.close.assert_awaited_once()


async def test_postgresql_permisos_insuficientes_sin_information_schema() -> None:
    conn = _mock_conn_pg(
        rol_row={
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": False,
        },
        grants_datos=0,
        total_tablas=None,
    )
    with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "database": "erp"},
            {"usuario": "kallicode_sin_perm", "password": "secret"},
        )

    assert res["ok"] is False
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA information_schema" in res["mensaje"]


async def test_postgresql_caido() -> None:
    with patch(
        "asyncpg.connect",
        new=AsyncMock(side_effect=ConnectionRefusedError("connection refused")),
    ):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "database": "erp"},
            {"usuario": "kallicode_meta", "password": "secret"},
        )

    assert res["ok"] is False
    assert res.get("proveedor_caido") is True


# ---------------------------------------------------------------------------
# 3. Integración BD + HTTP: creación con test real y re-test de rotación segura
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def entorno_conexiones(redis_doble):
    tenant_id = f"org_kc07_{uuid.uuid4().hex[:10]}"
    user_id = f"usr_{uuid.uuid4().hex[:10]}"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Org KC-BE-07-1')"),
            {"id": tenant_id},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol, password_hash) "
                "VALUES (:uid, :t, :em, 'Admin KC07', 'owner', 'hash_dummy')"
            ),
            {"uid": user_id, "t": tenant_id, "em": f"{user_id}@kallicode.dev"},
        )
        await db.commit()

    token_jwt = emitir_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        rol="owner",
    )

    try:
        yield {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "headers": {"Authorization": f"Bearer {token_jwt}"},
        }
    finally:
        async with sesion_sistema() as db:
            await db.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("DELETE FROM audit.audit_events WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("DELETE FROM audit.chain_heads WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("DELETE FROM core.connections WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("DELETE FROM core.users WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await db.execute(
                text("DELETE FROM core.organizations WHERE id = :t"),
                {"t": tenant_id},
            )
            await db.commit()


@pytest.mark.integracion
async def test_rotacion_conserva_credencial_anterior_si_nuevo_test_falla(
    entorno_conexiones,
) -> None:
    """KC-BE-07-1: PATCH /connections/{id} mantiene la credencial previa en bóveda si el re-test falla."""
    ctx = entorno_conexiones
    tenant_id = ctx["tenant_id"]
    headers = ctx["headers"]

    with patch("kallicode_api.routers.connections.registrar_evento", return_value="sello_mock"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
            # 1. Crear conexión GitHub con credencial válida (grabación VCR)
            with respx.mock(assert_all_called=True) as router:
                _cargar_vcr(router, "github", "valido")
                resp_crear = await cliente.post(
                    "/api/v1/connections",
                    headers=headers,
                    json={
                        "categoria": "repositorio",
                        "proveedor": "github",
                        "nombre": "Repo Principal",
                        "config": {"repo": "kallicode/demo-app"},
                        "credenciales": {"token": "ghp_token_original_valido"},
                    },
                )

            assert resp_crear.status_code == 201, resp_crear.text
            body_crear = resp_crear.json()
            cid = body_crear["id"]
            assert body_crear["estado"] == "conectada"
            assert "repo:write" in body_crear["permisos_verificados"]

            # Verificar secreto inicial en bóveda
            secreto_inicial = await boveda.leer(tenant_id, f"cn--{cid}")
            assert secreto_inicial == {"token": "ghp_token_original_valido"}

            # 2. Intentar rotar con un token revocado -> debe devolver 400 y conservar el anterior
            with respx.mock(assert_all_called=True) as router:
                _cargar_vcr(router, "github", "revocada")
                resp_rotar = await cliente.patch(
                    f"/api/v1/connections/{cid}",
                    headers=headers,
                    json={
                        "credenciales": {"token": "ghp_token_nuevo_revocado"},
                    },
                )

            assert resp_rotar.status_code == 400
            assert "Se mantienen las anteriores" in resp_rotar.json()["error"]["mensaje"]

            # 3. Confirmar en bóveda y en core.connections que nada se sobrescribió
            secreto_actual = await boveda.leer(tenant_id, f"cn--{cid}")
            assert secreto_actual == {"token": "ghp_token_original_valido"}

            async with sesion_sistema() as db:
                fila_cn = await uno(
                    db,
                    "SELECT estado, ultimo_test_detalle FROM core.connections WHERE id = :id",
                    {"id": cid},
                )
            assert fila_cn is not None
            assert fila_cn["estado"] == "conectada"
