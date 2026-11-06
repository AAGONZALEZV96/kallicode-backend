"""Integración KC-BE-05-5 y SAS local de KC-BE-05-7.

Requiere PostgreSQL, Azurite y ClamAV reales.
Redis se sustituye únicamente para capturar el mensaje publicado.
El worker se ejecuta explícitamente con ese mensaje.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_api.routers.tickets import _validar_adjuntos
from kallicode_api.servicios import blob
from kallicode_core import comercial
from kallicode_core.db import sesion_sistema, sesion_tenant, uno
from kallicode_core.errors import AppError
from kallicode_workers import adjuntos
from sqlalchemy import text

pytestmark = pytest.mark.integracion

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@pytest_asyncio.fixture
async def entorno(
    seed_dos_tenants,
    usuarios_por_rol,
    monkeypatch,
):
    tenant, ajeno = seed_dos_tenants
    referencias: list[str] = []

    redis = AsyncMock()
    redis.xadd.return_value = "1-0"
    monkeypatch.setattr(comercial, "redis_cliente", lambda: redis)

    async with sesion_sistema() as db:
        plan = await uno(
            db,
            """
            SELECT codigo
            FROM core.plans
            WHERE storage_mb > 0
            ORDER BY storage_mb
            LIMIT 1
            """,
        )
        assert plan, "Se necesita un plan con cuota de almacenamiento."

        await db.execute(
            text("""
                INSERT INTO core.subscriptions (
                    id, tenant_id, plan_codigo, inicia_el, renueva_el
                )
                VALUES (
                    :id, :t, :p, current_date, current_date + 30
                )
            """),
            {
                "id": f"sub_test_{uuid4().hex}",
                "t": tenant,
                "p": plan["codigo"],
            },
        )

    def autenticar(t=tenant):
        app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
            user_id=usuarios_por_rol[t]["owner"],
            tenant_id=t,
            rol="owner",
            restringido=False,
        )

    autenticar()

    yield {
        "tenant": tenant,
        "ajeno": ajeno,
        "redis": redis,
        "refs": referencias,
        "auth": autenticar,
    }

    app.dependency_overrides.pop(usuario_actual, None)

    try:
        for ref in referencias:
            await blob.eliminar(ref)
    finally:
        async with sesion_sistema() as db:
            await db.execute(
                text("""
                    DELETE FROM core.ticket_attachments
                    WHERE tenant_id = :t
                """),
                {"t": tenant},
            )
            await db.execute(
                text("""
                    DELETE FROM core.subscriptions
                    WHERE tenant_id = :t
                """),
                {"t": tenant},
            )


async def reservar(client, entorno, tamano):
    respuesta = await client.post(
        "/api/v1/tickets/attachments",
        json={
            "nombre_archivo": f"prueba-{uuid4().hex}.txt",
            "content_type": "text/plain",
            "tamano_bytes": tamano,
        },
    )
    assert respuesta.status_code == 201, respuesta.text

    datos = respuesta.json()

    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            SELECT blob_ref
            FROM core.ticket_attachments
            WHERE id = :id
            """,
            {"id": datos["adjunto_id"]},
        )

    entorno["refs"].append(fila["blob_ref"])

    # Crea el contenedor si hace falta y luego realiza el PUT real
    # usando la URL SAS entregada al navegador.
    await blob.subir_archivo_servidor(
        fila["blob_ref"],
        b"reserva",
        "text/plain",
    )
    return datos


async def subir(client, entorno, contenido, declarado=None):
    datos = await reservar(
        client,
        entorno,
        len(contenido) if declarado is None else declarado,
    )

    async with httpx.AsyncClient(timeout=30) as http:
        respuesta = await http.put(
            datos["upload_url"],
            content=contenido,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "text/plain",
            },
        )

    assert respuesta.status_code == 201, respuesta.text
    return datos["adjunto_id"]


async def consultar(client, aid):
    respuesta = await client.get(f"/api/v1/tickets/attachments/{aid}/status")
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def test_limpio_e_idempotencia(client, entorno):
    contenido = b"Archivo limpio de prueba."
    aid = await subir(client, entorno, contenido)

    for _ in range(2):
        respuesta = await client.post(f"/api/v1/tickets/attachments/{aid}/confirm")
        assert respuesta.status_code == 202, respuesta.text

    await adjuntos.procesar(
        {
            "tenant_id": entorno["tenant"],
            "adjunto_id": aid,
        }
    )
    await adjuntos.procesar(
        {
            "tenant_id": entorno["tenant"],
            "adjunto_id": aid,
        }
    )

    estado = await consultar(client, aid)
    assert estado["estado"] == "listo"
    assert estado["escaneo"] == "limpio"
    assert estado["tamano_bytes"] == len(contenido)

    async with sesion_tenant(entorno["tenant"]) as db:
        await _validar_adjuntos(db, entorno["tenant"], [aid])


async def test_tamano_falso_guarda_consumo_real(client, entorno):
    contenido = b"x" * 4096
    aid = await subir(client, entorno, contenido, declarado=1)

    respuesta = await client.post(f"/api/v1/tickets/attachments/{aid}/confirm")

    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["error"]["codigo"] == "TAMANO_NO_COINCIDE"

    estado = await consultar(client, aid)
    assert estado["estado"] == "rechazado"
    assert estado["tamano_bytes"] == len(contenido)

    async with sesion_tenant(entorno["tenant"]) as db:
        cuota = await comercial.estado_storage(db, entorno["tenant"])
        assert cuota["usados"] == len(contenido)

        with pytest.raises(AppError) as error:
            await _validar_adjuntos(db, entorno["tenant"], [aid])

        assert error.value.codigo == "ADJUNTO_NO_LISTO"

    entorno["redis"].xadd.assert_not_awaited()


async def test_pendiente_no_se_asocia(client, entorno):
    datos = await reservar(client, entorno, 100)
    aid = datos["adjunto_id"]

    async with sesion_tenant(entorno["tenant"]) as db:
        with pytest.raises(AppError) as error:
            await _validar_adjuntos(db, entorno["tenant"], [aid])

        assert error.value.codigo == "ADJUNTO_NO_LISTO"


async def test_eicar_y_descarga_423(client, entorno, crear_ticket):
    aid = await subir(client, entorno, EICAR)

    respuesta = await client.post(f"/api/v1/tickets/attachments/{aid}/confirm")
    assert respuesta.status_code == 202, respuesta.text

    await adjuntos.procesar(
        {
            "tenant_id": entorno["tenant"],
            "adjunto_id": aid,
        }
    )

    estado = await consultar(client, aid)
    assert estado["estado"] == "rechazado"
    assert estado["escaneo"] == "infectado"
    assert estado["motivo"] == "MALWARE_DETECTADO"

    ticket = await crear_ticket(entorno["tenant"])

    # Sólo para probar la defensa del endpoint de descarga:
    # asociamos mediante la sesión administrativa de pruebas.
    # La API pública debe impedir esta asociación.
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                UPDATE core.ticket_attachments
                SET ticket_id = (
                    SELECT id FROM core.tickets
                    WHERE tenant_id = :t AND numero = :n
                )
                WHERE id = :a
            """),
            {
                "t": entorno["tenant"],
                "n": ticket["numero"],
                "a": aid,
            },
        )

    respuesta = await client.get(f"/api/v1/tickets/{ticket['numero']}/attachments/{aid}")
    assert respuesta.status_code == 423, respuesta.text
    assert respuesta.json()["error"]["codigo"] == "ADJUNTO_BLOQUEADO"


async def test_aislamiento_tenant(client, entorno):
    aid = await subir(client, entorno, b"contenido")
    entorno["auth"](entorno["ajeno"])

    respuesta = await client.post(f"/api/v1/tickets/attachments/{aid}/confirm")
    assert respuesta.status_code == 404, respuesta.text

    respuesta = await client.get(f"/api/v1/tickets/attachments/{aid}/status")
    assert respuesta.status_code == 404, respuesta.text


async def test_cuota_80_y_exceso(client, entorno):
    datos = await reservar(client, entorno, 1)

    async with sesion_tenant(entorno["tenant"]) as db:
        inicial = await comercial.estado_storage(db, entorno["tenant"])
        limite = inicial["limite"]
        assert limite and limite > 0

        await db.execute(
            text("""
                UPDATE core.ticket_attachments
                SET tamano_bytes = :tamano
                WHERE id = :a
            """),
            {
                "a": datos["adjunto_id"],
                "tamano": (limite * 8 + 9) // 10,
            },
        )

        estado = await comercial.verificar_storage(db, entorno["tenant"], 0)
        assert estado["umbral"] == "aviso_80"

        with pytest.raises(AppError) as error:
            await comercial.verificar_storage(db, entorno["tenant"], limite)

        assert error.value.codigo == "ALMACENAMIENTO_AGOTADO"
        assert error.value.http == 402


async def test_ejecutable_disfrazado_de_texto(client, entorno):
    # Una cabecera ELF no debe aceptarse como text/plain.
    contenido = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 512
    aid = await subir(client, entorno, contenido)

    respuesta = await client.post(f"/api/v1/tickets/attachments/{aid}/confirm")
    assert respuesta.status_code == 202, respuesta.text

    await adjuntos.procesar(
        {
            "tenant_id": entorno["tenant"],
            "adjunto_id": aid,
        }
    )

    estado = await consultar(client, aid)
    assert estado["estado"] == "rechazado"
    assert estado["motivo"] == "TIPO_NO_COINCIDE"


async def test_sobrescribir_original_no_modifica_verificado(client, entorno):
    contenido = b"Contenido limpio que debe permanecer."
    aid = await subir(client, entorno, contenido)

    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            SELECT blob_ref FROM core.ticket_attachments
            WHERE id = :a
            """,
            {"a": aid},
        )
    ref_original = fila["blob_ref"]
    url_subida, _ = blob.url_subida(ref_original, "text/plain")

    respuesta = await client.post(f"/api/v1/tickets/attachments/{aid}/confirm")
    assert respuesta.status_code == 202, respuesta.text

    await adjuntos.procesar(
        {
            "tenant_id": entorno["tenant"],
            "adjunto_id": aid,
        }
    )

    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            SELECT blob_ref, estado FROM core.ticket_attachments
            WHERE id = :a
            """,
            {"a": aid},
        )

    ref_segura = fila["blob_ref"]
    entorno["refs"].append(ref_segura)

    assert fila["estado"] == "listo"
    assert ref_segura != ref_original

    async with httpx.AsyncClient(timeout=30) as http:
        respuesta = await http.put(
            url_subida,
            content=EICAR,
            headers={
                "x-ms-blob-type": "BlockBlob",
                "Content-Type": "text/plain",
            },
        )
        assert respuesta.status_code == 201, respuesta.text

        descarga, _ = blob.url_descarga(ref_segura)
        respuesta = await http.get(descarga)

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.content == contenido
