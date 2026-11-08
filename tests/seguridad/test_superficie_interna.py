"""KC-BE-05-6: aislamiento de rutas, identidades, claves y audiencia.

ASGITransport no ejecuta lifespan. Estas pruebas no necesitan
PostgreSQL ni Redis.
"""

from datetime import UTC, datetime

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api import main
from kallicode_api.deps import SERVICIOS_PERMITIDOS, servicio_actual
from kallicode_api.main_interna import crear_app_interna
from kallicode_core import seguridad
from kallicode_core.config import Settings
from kallicode_core.errors import AppError
from pydantic import ValidationError


@pytest.fixture
def settings(monkeypatch):
    privada, publica = seguridad._generar_par_efimero()
    privada_svc, publica_svc = seguridad._generar_par_efimero()

    configuracion = Settings(
        _env_file=None,
        env="local",
        database_url=("postgresql+asyncpg://kallicode_app:dev@localhost/kallicode"),
        database_url_admin=("postgresql+asyncpg://kallicode_admin:dev@localhost/kallicode"),
        database_url_worker=("postgresql+asyncpg://kallicode_worker:dev@localhost/kallicode"),
        jwt_private_key=privada,
        jwt_public_key=publica,
        jwt_private_key_svc=privada_svc,
        jwt_public_key_svc=publica_svc,
        allowed_hosts="testserver",
        cors_origins="http://localhost:5173",
    )

    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: configuracion,
    )
    monkeypatch.setattr(
        seguridad,
        "get_settings",
        lambda: configuracion,
    )

    return configuracion


@pytest.fixture
def apps(settings):
    return main.crear_app(), crear_app_interna()


def operaciones(app):
    return [
        (metodo.upper(), ruta)
        for ruta, definicion in app.openapi()["paths"].items()
        if ruta.startswith("/internal/v1")
        for metodo in definicion
        if metodo
        in {
            "get",
            "post",
            "put",
            "patch",
            "delete",
        }
    ]


def ruta_concreta(ruta):
    return ruta.replace("{job_id}", "job-test").replace("{numero}", "1")


def token_modificado(settings, **cambios):
    ahora = int(datetime.now(UTC).timestamp())

    claims = {
        "sub": "svc:orquestador",
        "org": "tenant-test",
        "svc": "orquestador",
        "typ": "svc",
        "jti": "jti-test",
        "iss": seguridad.ISS_INTERNAL,
        "aud": seguridad.AUD_INTERNAL,
        "iat": ahora,
        "exp": ahora + 300,
    }
    claims.update(cambios)

    return jwt.encode(
        claims,
        settings.jwt_private_key_svc,
        algorithm="RS256",
    )


def test_aplicaciones_y_superficies_distintas(apps):
    publica, interna = apps

    assert publica is not interna
    assert len(operaciones(interna)) >= 15
    assert operaciones(publica) == []
    assert not any(ruta.startswith("/api/v1") for ruta in interna.openapi()["paths"])


@pytest.mark.asyncio
async def test_publica_responde_404_en_todas_las_operaciones_internas(
    apps,
):
    publica, interna = apps
    token = seguridad.emitir_svc_token(
        "orquestador",
        "tenant-test",
    )

    async with AsyncClient(
        transport=ASGITransport(app=publica),
        base_url="http://testserver",
    ) as client:
        for metodo, ruta in operaciones(interna) + [
            ("GET", "/internal/v1"),
        ]:
            respuesta = await client.request(
                metodo,
                ruta_concreta(ruta),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "test",
                },
            )

            assert respuesta.status_code == 404, (
                metodo,
                ruta,
                respuesta.text,
            )


@pytest.mark.asyncio
async def test_usuario_recibe_401_en_todas_las_operaciones_internas(
    apps,
):
    _, interna = apps
    token = seguridad.emitir_access_token(
        "usuario-test",
        "tenant-test",
        "admin",
    )

    async with AsyncClient(
        transport=ASGITransport(app=interna),
        base_url="http://testserver",
    ) as client:
        for metodo, ruta in operaciones(interna):
            respuesta = await client.request(
                metodo,
                ruta_concreta(ruta),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "test",
                },
            )

            assert respuesta.status_code == 401, (
                metodo,
                ruta,
                respuesta.text,
            )


@pytest.mark.asyncio
async def test_servicio_desconocido_recibe_403_en_todas_las_operaciones(
    apps,
):
    _, interna = apps
    token = seguridad.emitir_svc_token(
        "servicio-no-declarado",
        "tenant-test",
    )

    async with AsyncClient(
        transport=ASGITransport(app=interna),
        base_url="http://testserver",
    ) as client:
        for metodo, ruta in operaciones(interna):
            respuesta = await client.request(
                metodo,
                ruta_concreta(ruta),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "test",
                },
            )

            assert respuesta.status_code == 403, (
                metodo,
                ruta,
                respuesta.text,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "servicio",
    [
        "orquestador",
        "normalizador",
        "motor-pruebas",
        "ingesta",
    ],
)
async def test_identidades_permitidas(settings, servicio):
    assert SERVICIOS_PERMITIDOS == frozenset(
        {
            "orquestador",
            "normalizador",
            "motor-pruebas",
            "ingesta",
        }
    )

    token = seguridad.emitir_svc_token(
        servicio,
        "tenant-test",
        linea=2,
    )
    identidad = await servicio_actual(
        authorization=f"Bearer {token}",
        idempotency_key="test",
    )

    assert identidad.servicio == servicio
    assert identidad.tenant_id == "tenant-test"
    assert identidad.linea == 2


@pytest.mark.asyncio
async def test_svc_desconocido_es_403_incluso_sin_idempotency_key(
    settings,
):
    token = seguridad.emitir_svc_token(
        "desconocido",
        "tenant-test",
    )

    with pytest.raises(AppError) as error:
        await servicio_actual(
            authorization=f"Bearer {token}",
            idempotency_key=None,
        )

    assert error.value.http == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "clave",
    [None, "", "   "],
)
async def test_servicio_exige_idempotency_key(settings, clave):
    token = seguridad.emitir_svc_token(
        "orquestador",
        "tenant-test",
    )

    with pytest.raises(AppError) as error:
        await servicio_actual(
            authorization=f"Bearer {token}",
            idempotency_key=clave,
        )

    assert error.value.http == 401


@pytest.mark.parametrize(
    "cambios",
    [
        {"aud": seguridad.AUD_PORTAL},
        {"iss": seguridad.ISS_PORTAL},
        {"typ": "user"},
        {"aud": None},
    ],
)
def test_interna_rechaza_audiencia_emisor_y_tipo_incorrectos(
    settings,
    cambios,
):
    token = token_modificado(settings, **cambios)

    with pytest.raises(AppError) as error:
        seguridad.verificar_token(token, "svc")

    assert error.value.http == 401


@pytest.mark.asyncio
async def test_sub_debe_corresponder_a_svc(settings):
    token = token_modificado(
        settings,
        sub="svc:ingesta",
    )

    with pytest.raises(AppError) as error:
        await servicio_actual(
            authorization=f"Bearer {token}",
            idempotency_key="test",
        )

    assert error.value.http == 401


def test_firma_portal_no_sirve_con_claims_internos(settings):
    claims = jwt.decode(
        token_modificado(settings),
        options={"verify_signature": False},
    )
    token = jwt.encode(
        claims,
        settings.jwt_private_key,
        algorithm="RS256",
    )

    with pytest.raises(AppError) as error:
        seguridad.verificar_token(token, "svc")

    assert error.value.http == 401


def test_token_servicio_no_sirve_en_portal(settings):
    token = seguridad.emitir_svc_token(
        "orquestador",
        "tenant-test",
    )

    with pytest.raises(AppError) as error:
        seguridad.verificar_token(token, "user")

    assert error.value.http == 401


def test_configuracion_rechaza_claves_compartidas(settings):
    datos = settings.model_dump()
    datos.update(
        jwt_private_key_svc=settings.jwt_private_key,
        jwt_public_key_svc=settings.jwt_public_key,
    )

    with pytest.raises(
        ValidationError,
        match="pares RSA distintos",
    ):
        Settings(_env_file=None, **datos)


def test_configuracion_rechaza_par_incompleto(settings):
    datos = settings.model_dump()
    datos["jwt_private_key_svc"] = ""

    with pytest.raises(
        ValidationError,
        match="par JWT completo",
    ):
        Settings(_env_file=None, **datos)


def test_configuracion_rechaza_par_inconsistente(settings):
    datos = settings.model_dump()
    datos["jwt_public_key_svc"] = settings.jwt_public_key

    with pytest.raises(
        ValidationError,
        match="no corresponde",
    ):
        Settings(_env_file=None, **datos)


def test_produccion_exige_ambos_pares(settings):
    datos = settings.model_dump()
    datos.update(
        env="production",
        jwt_private_key_svc="",
        jwt_public_key_svc="",
    )

    with pytest.raises(
        ValidationError,
        match="obligatorios",
    ):
        Settings(_env_file=None, **datos)
