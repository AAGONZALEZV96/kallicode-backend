"""
Ticket: KC-QA-B2-11
Matriz de Pruebas de RBAC y Control de Acceso Granular por Rol.
Valida la matriz real de autorización de KalliCode usando emitir_access_token (RS256):
- Gate 1/2: permitido a owner/admin/architect/approver; bloqueado (403) a member/viewer.
- Gate 3: exclusivo para architect; bloqueado (403) a todos los demás roles.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import kallicode_api.deps as api_deps
import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_core import comercial
from kallicode_core.seguridad import emitir_access_token

pytestmark = pytest.mark.asyncio

TENANT_TEST = "a0000000-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def aislar_dependencias_redis_y_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Aísla la prueba de infraestructura externa (Redis) durante la verificación
    del JWT en deps.usuario_actual:
    1. comprobar_revocacion devuelve None (token vigente, no revocado).
    2. comercial.rl_usuario no bloquea por rate-limiting.
    Esto permite evaluar de forma 100% determinista la matriz RBAC en FastAPI.
    """
    monkeypatch.setattr(api_deps, "comprobar_revocacion", AsyncMock(return_value=None))
    monkeypatch.setattr(comercial, "rl_usuario", AsyncMock(return_value=None))


@pytest.fixture
async def http_client() -> AsyncGenerator[AsyncClient, None]:
    """Provee un cliente HTTP asíncrono conectado en memoria vía ASGITransport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.parametrize(
    "rol, debe_bloquear_403",
    [
        ("owner", False),
        ("admin", False),
        ("architect", False),
        ("approver", False),
        ("member", True),
        ("viewer", True),
    ],
)
async def test_rbac_aprobacion_gate_1(
    http_client: AsyncClient,
    rol: str,
    debe_bloquear_403: bool,
) -> None:
    """
    [Criterio de Aceptación] Verifica que el rol 'viewer' (y 'member') reciba
    estrictamente HTTP 403 Forbidden al intentar aprobar el Gate 1, mientras que
    los roles aprobadores superan la barrera RBAC.
    """
    token = emitir_access_token(user_id=f"u_{rol}", tenant_id=TENANT_TEST, rol=rol)

    response = await http_client.post(
        "/api/v1/tickets/KC-1045/gates/1/approve",
        json={"comentario": " Intento de firma por matriz RBAC"},
        headers={"Authorization": f"Bearer {token}"},
    )

    if debe_bloquear_403:
        assert response.status_code == 403, (
            f"Fuga RBAC: El rol '{rol}' no fue bloqueado con HTTP 403 en Gate 1 "
            f"(recibió {response.status_code})."
        )
    else:
        # Si supera RBAC, no puede devolver ni 401 (no autenticado) ni 403 (prohibido).
        # Devolverá 404 si el ticket sintético no existe en la BD local.
        assert response.status_code not in (401, 403), (
            f"Falso positivo RBAC: El rol autorizado '{rol}' fue rechazado con "
            f"HTTP {response.status_code}."
        )


@pytest.mark.parametrize(
    "rol, debe_bloquear_403",
    [
        ("architect", False),
        ("owner", True),
        ("admin", True),
        ("approver", True),
        ("member", True),
        ("viewer", True),
    ],
)
async def test_rbac_exclusividad_gate_3_arquitecto(
    http_client: AsyncClient,
    rol: str,
    debe_bloquear_403: bool,
) -> None:
    """
    Verifica la segunda barrera granular (puede_firmar en gates.py):
    El Gate 3 (paso a producción) solo puede ser firmado por 'architect'.
    Incluso 'owner', 'admin' y 'approver' deben recibir HTTP 403 (FIRMA_NO_AUTORIZADA).
    """
    token = emitir_access_token(user_id=f"u_{rol}", tenant_id=TENANT_TEST, rol=rol)

    response = await http_client.post(
        "/api/v1/tickets/KC-1045/gates/3/approve",
        json={"comentario": "Intento de firma Gate 3"},
        headers={"Authorization": f"Bearer {token}"},
    )

    if debe_bloquear_403:
        assert response.status_code == 403, (
            f"Fuga RBAC Gate 3: El rol '{rol}' debía recibir HTTP 403 en Gate 3 "
            f"(recibió {response.status_code})."
        )
    else:
        assert response.status_code not in (401, 403), (
            f"Falso positivo RBAC: El rol 'architect' fue bloqueado en Gate 3 "
            f"con HTTP {response.status_code}."
        )


@pytest.mark.parametrize(
    "rol, debe_bloquear_403",
    [
        ("owner", False),
        ("admin", False),
        ("architect", False),
        ("approver", False),
        ("member", True),
        ("viewer", True),
    ],
)
async def test_rbac_listado_aprobaciones_pendientes(
    http_client: AsyncClient,
    rol: str,
    debe_bloquear_403: bool,
) -> None:
    """
    Verifica que la consulta de cola de aprobaciones (/api/v1/approvals/pending)
    bloquee con HTTP 403 a 'viewer' y 'member', y permita el paso a roles aprobadores.
    """
    token = emitir_access_token(user_id=f"u_{rol}", tenant_id=TENANT_TEST, rol=rol)

    response = await http_client.get(
        "/api/v1/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )

    if debe_bloquear_403:
        assert response.status_code == 403, (
            f"Fuga RBAC: El rol '{rol}' accedió a /approvals/pending sin recibir 403 "
            f"(status {response.status_code})."
        )
    else:
        assert response.status_code == 200, (
            f"El rol aprobador '{rol}' debía recibir 200 OK en /approvals/pending "
            f"(recibió {response.status_code})."
        )
