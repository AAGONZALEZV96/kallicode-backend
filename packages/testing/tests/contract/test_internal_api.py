"""Suite de pruebas de contrato OpenAPI para la API Interna /internal/v1.

Ticket: KC-QA-B5-05
Épica: EQA5 - Pruebas E2E del Portal, Visual Diff y Accesibilidad
Propósito: Validar que todos los endpoints privados del pipeline en /internal/v1
(evidence, findings, spec-versions, deploy/preflight, escalations) exijan
autenticación estricta con token JWT de servicio (typ=svc), requieran
la cabecera atómica Idempotency-Key, procesen la idempotencia correctamente,
y retornen respuestas controladas HTTP 422 / 409 / 404 sin lanzar jamás
excepciones HTTP 500 no capturadas.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import schemathesis
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import SERVICIOS_PERMITIDOS
from kallicode_api.main_interna import app
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_access_token, emitir_svc_token
from schemathesis.specs.openapi.schemas import OpenApiSchema
from sqlalchemy import text


def _obtener_ruta_contrato_interno() -> Path:
    """Resuelve la ruta absoluta al contrato oficial OpenAPI de la API interna."""
    base = Path(__file__).resolve().parents[4]
    for rel in ("contratos/internal-v1.yaml", "contracts/internal-v1.yaml"):
        candidato = base / rel
        if candidato.exists():
            return candidato
    raise FileNotFoundError(
        "No se encontró el contrato OpenAPI internal-v1.yaml en 'contratos/' ni en 'contracts/'"
    )


@pytest.fixture
def internal_schema() -> OpenApiSchema:
    """Carga y compila la especificación OpenAPI interna."""
    ruta = _obtener_ruta_contrato_interno()
    schema: OpenApiSchema = schemathesis.openapi.from_path(str(ruta))
    schema.app = app
    return schema


@pytest.fixture
def svc_token_valido() -> str:
    """Emite un token JWT de servicio válido con typ=svc."""
    return emitir_svc_token(
        servicio="motor-pruebas",
        tenant_id="org_demo",
    )


@pytest.fixture
def user_token_portal() -> str:
    """Emite un token JWT de usuario estándar con typ=user (no autorizado en API interna)."""
    return emitir_access_token(
        user_id="u_admin",
        tenant_id="org_demo",
        rol="admin",
    )


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP asíncrono contra FastAPI interna vía transporte ASGI."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.fixture(autouse=True)
async def sembrar_base_internal_api() -> AsyncGenerator[None, None]:
    """Asegura la presencia de datos base (tenant, usuario, ticket KC-1001 y job) en entornos CI limpios."""
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO core.organizations (id, nombre, idioma, timezone, despliegue, fabrica_activa) "
                "VALUES ('org_demo', 'Demo Mobility', 'es', 'Europe/Madrid', 'cloud', true) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await db.execute(
            text(
                "INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el) "
                "VALUES ('sub_demo', 'org_demo', 'growth', 'activa', CURRENT_DATE, (CURRENT_DATE + INTERVAL '1 month')::date) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado, idioma) "
                "VALUES ('u_admin', 'org_demo', 'admin@demo.kallicode.dev', 'Ana Admin', 'admin', 'activo', 'es') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        await db.execute(
            text(
                "INSERT INTO core.production_lines (tenant_id, numero) "
                "VALUES ('org_demo', 1), ('org_demo', 2), ('org_demo', 3) "
                "ON CONFLICT DO NOTHING"
            )
        )
        await db.execute(
            text(
                "INSERT INTO core.branch_map (tenant_id) VALUES ('org_demo') ON CONFLICT DO NOTHING"
            )
        )
        res_tk = await db.execute(
            text("SELECT id FROM core.tickets WHERE tenant_id = 'org_demo' AND numero = 'KC-1001'")
        )
        fila_tk = res_tk.fetchone()
        if fila_tk:
            tk_id = fila_tk[0]
        else:
            tk_id = "tk_contract_1001"
            await db.execute(
                text(
                    "INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion, prioridad, etapa, origen, reportado_por) "
                    "VALUES (:id, 'org_demo', 'KC-1001', 'bug', 'Ticket contrato interno', 'Descripcion contrato', 'media', 'security', 'portal', 'u_admin')"
                ),
                {"id": tk_id},
            )

        res_job = await db.execute(
            text("SELECT id FROM core.jobs WHERE ticket_id = :tk_id"),
            {"tk_id": tk_id},
        )
        fila_job = res_job.fetchone()
        if fila_job:
            await db.execute(
                text("UPDATE core.jobs SET estado = 'security' WHERE id = :id"),
                {"id": fila_job[0]},
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, creado_en) "
                    "VALUES (:id, 'org_demo', :tk_id, 'security', 'P2', NOW())"
                ),
                {"id": f"job_{tk_id}", "tk_id": tk_id},
            )
        await db.commit()

    yield

    try:
        async with sesion_sistema() as db:
            await db.execute(
                text(
                    "DELETE FROM core.idempotency_keys "
                    "WHERE tenant_id = 'org_demo' AND clave LIKE 'ik-contract-%'"
                )
            )
            await db.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 1. test_autenticacion_estricta_servicio_jwt()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_autenticacion_estricta_servicio_jwt(
    async_client: AsyncClient,
    svc_token_valido: str,
    user_token_portal: str,
) -> None:
    """Valida la frontera de autenticación estricta para la API interna.

    Certifica que solo tokens de servicio válidos (typ=svc) sean aceptados,
    rechazando ausencia de token (401), tokens de usuario de portal (401),
    firmas inválidas (401) y servicios no autorizados (403).
    """
    ruta_test = "/internal/v1/tickets/KC-1001/evidence"
    headers_ik = {"Idempotency-Key": f"ik-contract-{uuid.uuid4().hex[:8]}"}

    # A. Ausencia total de cabecera Authorization -> HTTP 401
    r_sin_auth = await async_client.post(ruta_test, headers=headers_ik)
    assert r_sin_auth.status_code == 401
    body_sin_auth = r_sin_auth.json()
    assert body_sin_auth["error"]["codigo"] == "NO_AUTENTICADO"

    # B. Token malformado / firma inválida -> HTTP 401
    r_invalido = await async_client.post(
        ruta_test,
        headers={
            "Authorization": "Bearer token_falsificado_invalido",
            **headers_ik,
        },
    )
    assert r_invalido.status_code == 401
    assert r_invalido.json()["error"]["codigo"] == "NO_AUTENTICADO"

    # C. Token de usuario de portal (typ=user) usado en API interna -> HTTP 401
    r_user = await async_client.post(
        ruta_test,
        headers={
            "Authorization": f"Bearer {user_token_portal}",
            **headers_ik,
        },
    )
    assert r_user.status_code == 401
    assert r_user.json()["error"]["codigo"] == "NO_AUTENTICADO"

    # D. Servicio no permitido en SERVICIOS_PERMITIDOS -> HTTP 403
    token_svc_ajeno = emitir_svc_token(
        servicio="servicio-hacker-no-permitido",
        tenant_id="org_demo",
    )
    r_servicio_ajeno = await async_client.post(
        ruta_test,
        headers={
            "Authorization": f"Bearer {token_svc_ajeno}",
            **headers_ik,
        },
    )
    assert r_servicio_ajeno.status_code == 403
    assert r_servicio_ajeno.json()["error"]["codigo"] == "PERMISO_DENEGADO"

    # E. Token de servicio válido con servicio autorizado -> supera frontera de autenticación
    # (retorna 422 por payload vacío, confirmando que la autenticación fue aceptada)
    assert "motor-pruebas" in SERVICIOS_PERMITIDOS
    r_valido = await async_client.post(
        ruta_test,
        headers={
            "Authorization": f"Bearer {svc_token_valido}",
            **headers_ik,
        },
        json={},
    )
    assert r_valido.status_code != 401
    assert r_valido.status_code != 403
    assert r_valido.status_code == 422


# ---------------------------------------------------------------------------
# 2. test_exigencia_cabecera_idempotency_key()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exigencia_cabecera_idempotency_key(
    async_client: AsyncClient,
    svc_token_valido: str,
) -> None:
    """Verifica que la cabecera Idempotency-Key sea estrictamente obligatoria.

    Cualquier mutación interna sin Idempotency-Key o con valor en blanco
    debe ser rechazada inmediatamente con HTTP 401.
    """
    endpoints_mutacionales = [
        "/internal/v1/tickets/KC-1001/evidence",
        "/internal/v1/tickets/KC-1001/findings",
        "/internal/v1/tickets/KC-1001/spec-versions",
        "/internal/v1/tickets/KC-1001/deploy/preflight",
        "/internal/v1/escalations",
    ]

    for ruta in endpoints_mutacionales:
        # A. Sin cabecera Idempotency-Key
        r_sin_ik = await async_client.post(
            ruta,
            headers={"Authorization": f"Bearer {svc_token_valido}"},
            json={},
        )
        assert r_sin_ik.status_code == 401, (
            f"La ruta {ruta} no exigió cabecera Idempotency-Key: {r_sin_ik.text}"
        )
        data_sin_ik = r_sin_ik.json()
        assert data_sin_ik["error"]["codigo"] == "NO_AUTENTICADO"
        assert "Idempotency-Key" in data_sin_ik["error"]["mensaje"]

        # B. Con Idempotency-Key vacía
        r_ik_vacia = await async_client.post(
            ruta,
            headers={
                "Authorization": f"Bearer {svc_token_valido}",
                "Idempotency-Key": "   ",
            },
            json={},
        )
        assert r_ik_vacia.status_code == 401, (
            f"La ruta {ruta} aceptó Idempotency-Key en blanco: {r_ik_vacia.text}"
        )
        assert r_ik_vacia.json()["error"]["codigo"] == "NO_AUTENTICADO"


# ---------------------------------------------------------------------------
# 3. test_procesamiento_idempotente_reintentos_exactos()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_procesamiento_idempotente_reintentos_exactos(
    async_client: AsyncClient,
    svc_token_valido: str,
) -> None:
    """Garantiza la idempotencia determinista ante reintentos de red.

    Dos peticiones idénticas enviadas con la misma Idempotency-Key deben
    retornar exactamente el mismo código de estado y payload memorizado,
    evitando ejecuciones duplicadas de la lógica mutacional.
    """
    ik = f"ik-contract-{uuid.uuid4().hex[:12]}"
    headers = {
        "Authorization": f"Bearer {svc_token_valido}",
        "Idempotency-Key": ik,
    }

    # Asegurar que el job del ticket KC-1001 se encuentre en etapa 'security'
    # para permitir la transición válida a 'deploy_preflight'
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "UPDATE core.jobs SET estado = 'security' "
                "WHERE ticket_id = (SELECT id FROM core.tickets WHERE numero = 'KC-1001' AND tenant_id = 'org_demo')"
            )
        )
        await db.commit()

    # Payload válido para registrar findings de seguridad
    payload_findings: dict[str, Any] = {
        "hallazgos": [],
        "veredicto_cambio": "approved",
    }

    # Primera llamada
    r1 = await async_client.post(
        "/internal/v1/tickets/KC-1001/findings",
        headers=headers,
        json=payload_findings,
    )
    assert r1.status_code == 201, f"Primera llamada falló: {r1.text}"
    body1 = r1.json()

    # Segunda llamada con la misma clave Idempotency-Key
    r2 = await async_client.post(
        "/internal/v1/tickets/KC-1001/findings",
        headers=headers,
        json=payload_findings,
    )
    assert r2.status_code == 201, f"Segunda llamada falló: {r2.text}"
    body2 = r2.json()

    # Las respuestas deben coincidir exactamente
    assert body1 == body2, f"La respuesta idempotente no coincide: r1={body1} != r2={body2}"


# ---------------------------------------------------------------------------
# 4. test_manejo_errores_controlados_422_409_sin_500()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manejo_errores_controlados_422_409_sin_500(
    async_client: AsyncClient,
    svc_token_valido: str,
) -> None:
    """Verifica que entradas límite, malformadas o inconsistentes nunca lancen HTTP 500.

    Inspecciona los 5 endpoints requeridos:
    - /internal/v1/tickets/{numero}/evidence
    - /internal/v1/tickets/{numero}/findings
    - /internal/v1/tickets/{numero}/spec-versions
    - /internal/v1/tickets/{numero}/deploy/preflight
    - /internal/v1/escalations
    """

    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {svc_token_valido}",
            "Idempotency-Key": f"ik-contract-{uuid.uuid4().hex[:10]}",
        }

    casos_limite: list[dict[str, Any]] = [
        # 1. evidence: cuerpo vacío -> 422 VALIDACION_ENTRADA
        {
            "ruta": "/internal/v1/tickets/KC-1001/evidence",
            "json": {},
            "status_esperado": 422,
            "codigo_esperado": "VALIDACION_ENTRADA",
            "descripcion": "evidence con body vacío",
        },
        # 2. evidence: matriz vacía ([] viola minItems=1) -> 422
        {
            "ruta": "/internal/v1/tickets/KC-1001/evidence",
            "json": {
                "corrida": 1,
                "matriz": [],
                "regresion": {"total": 0, "pasa": 0},
            },
            "status_esperado": 422,
            "codigo_esperado": "VALIDACION_ENTRADA",
            "descripcion": "evidence con matriz vacía",
        },
        # 3. findings: veredicto blocked sin hallazgos bloqueantes -> 422 VEREDICTO_INCONSISTENTE
        {
            "ruta": "/internal/v1/tickets/KC-1001/findings",
            "json": {
                "hallazgos": [],
                "veredicto_cambio": "blocked",
            },
            "status_esperado": 422,
            "codigo_esperado": "VEREDICTO_INCONSISTENTE",
            "descripcion": "findings blocked sin hallazgos bloqueantes",
        },
        # 4. findings: enum inválido en veredicto_cambio -> 422
        {
            "ruta": "/internal/v1/tickets/KC-1001/findings",
            "json": {
                "hallazgos": [],
                "veredicto_cambio": "veredicto_invalido",
            },
            "status_esperado": 422,
            "codigo_esperado": "VALIDACION_ENTRADA",
            "descripcion": "findings con veredicto fuera de patrón",
        },
        # 5. spec-versions: alternativas sin ninguna marcada como recomendada -> 422
        {
            "ruta": "/internal/v1/tickets/KC-1001/spec-versions",
            "json": {
                "spec_md_ref": "blob://specs/spec-1.md",
                "alternativas": [
                    {
                        "titulo": "Alt 1",
                        "pros": [],
                        "contras": [],
                        "recomendada": False,
                    }
                ],
                "criterios": [{"criterio_id": "AC-1", "descripcion": "Desc"}],
                "scope_paths": ["src/modulo.py"],
            },
            "status_esperado": 422,
            "codigo_esperado": "VALIDACION_ENTRADA",
            "descripcion": "spec-versions sin alternativa recomendada",
        },
        # 6. spec-versions: scope_paths con rutas del sistema vetadas -> 422 SCOPE_PROHIBIDO
        {
            "ruta": "/internal/v1/tickets/KC-1001/spec-versions",
            "json": {
                "spec_md_ref": "blob://specs/spec-1.md",
                "alternativas": [
                    {
                        "id": "alt-1",
                        "titulo": "Alt 1",
                        "descripcion": "Desc",
                        "riesgo": "bajo",
                        "recomendada": True,
                    }
                ],
                "criterios": [
                    {
                        "id": "AC-1",
                        "given": "dado el sistema",
                        "when": "cuando se analiza",
                        "then": "entonces valida",
                    }
                ],
                "scope_paths": ["/etc/shadow", "infra/secrets/master.key"],
            },
            "status_esperado": 422,
            "codigo_esperado": "SCOPE_PROHIBIDO",
            "descripcion": "spec-versions con scope_paths vetados",
        },
        # 7. deploy/preflight: destino inválido -> 422
        {
            "ruta": "/internal/v1/tickets/KC-1001/deploy/preflight",
            "json": {"destino": "entorno_invalido"},
            "status_esperado": 422,
            "codigo_esperado": "VALIDACION_ENTRADA",
            "descripcion": "deploy/preflight con destino inválido",
        },
        # 8. deploy/preflight: ticket no existente -> 404 RECURSO_NO_ENCONTRADO
        {
            "ruta": "/internal/v1/tickets/KC-99999-INEXISTENTE/deploy/preflight",
            "json": {"destino": "staging"},
            "status_esperado": 404,
            "codigo_esperado": "RECURSO_NO_ENCONTRADO",
            "descripcion": "deploy/preflight sobre ticket inexistente",
        },
        # 9. escalations: motivo fuera de enum permitido -> 422
        {
            "ruta": "/internal/v1/escalations",
            "json": {
                "job_id": "job_inexistente",
                "etapa": "qa",
                "motivo": "motivo_no_reconocido",
                "severidad": "alta",
                "detalle": {"mensaje": "Test"},
            },
            "status_esperado": 422,
            "codigo_esperado": "VALIDACION_ENTRADA",
            "descripcion": "escalations con motivo fuera de enum",
        },
        # 10. escalations: job inexistente -> 404 RECURSO_NO_ENCONTRADO
        {
            "ruta": "/internal/v1/escalations",
            "json": {
                "job_id": "job_no_existe_9999",
                "etapa": "qa",
                "motivo": "selector_repair",
                "severidad": "alta",
                "detalle": {"mensaje": "Test"},
            },
            "status_esperado": 404,
            "codigo_esperado": "RECURSO_NO_ENCONTRADO",
            "descripcion": "escalations con job inexistente",
        },
    ]

    for caso in casos_limite:
        ruta = str(caso["ruta"])
        payload = caso["json"]
        status_esperado = int(caso["status_esperado"])
        codigo_esperado = str(caso["codigo_esperado"])
        desc = str(caso["descripcion"])

        response = await async_client.post(ruta, headers=_headers(), json=payload)

        # Regla de oro: CERO errores 500 no capturados
        assert response.status_code < 500, (
            f"Fallo de servidor {response.status_code} ante caso límite ({desc}) "
            f"en {ruta}: {response.text}"
        )

        assert response.status_code == status_esperado, (
            f"Se esperaba HTTP {status_esperado} pero se obtuvo {response.status_code} "
            f"en {ruta} ({desc}): {response.text}"
        )

        body = response.json()
        assert "error" in body, f"Respuesta sin clave 'error': {body}"
        assert body["error"].get("codigo") == codigo_esperado, (
            f"Código de error no esperado en {ruta} ({desc}): {body}"
        )
        assert "trace_id" in body["error"], f"Falta 'trace_id' en respuesta de error: {body}"


# ---------------------------------------------------------------------------
# 5. test_conformidad_esquema_respuestas_openapi()
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_conformidad_esquema_respuestas_openapi(
    async_client: AsyncClient,
    svc_token_valido: str,
    internal_schema: OpenApiSchema,
) -> None:
    """Valida la conformidad 1:1 entre respuestas generadas y el esquema OpenAPI.

    Comprueba que el modelo ErrorEnvelope y las respuestas de éxito respeten
    los tipos, propiedades requeridas y formatos de contratos/internal-v1.yaml.
    """
    ik = f"ik-contract-{uuid.uuid4().hex[:10]}"
    headers = {
        "Authorization": f"Bearer {svc_token_valido}",
        "Idempotency-Key": ik,
    }

    # Probar endpoint de preflight sobre ticket de prueba existente
    response = await async_client.post(
        "/internal/v1/tickets/KC-1001/deploy/preflight",
        headers=headers,
        json={"destino": "staging"},
    )

    assert response.status_code in (200, 422, 404)
    assert response.status_code < 500

    # Validar respuesta contra la operación OpenAPI correspondiente
    op = internal_schema["/internal/v1/tickets/{numero}/deploy/preflight"]["POST"]
    op.validate_response(response)
