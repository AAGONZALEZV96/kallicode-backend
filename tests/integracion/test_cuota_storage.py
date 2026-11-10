"""KC-BE-05-5: PostgreSQL y Azurite reales; EICAR requiere ClamAV en KC_CLAMAV_*.

La migración se verifica dentro de una transacción revertida al finalizar.
No se modifica el esquema ni se conservan datos de la base local.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from kallicode_api.routers import tickets
from kallicode_core import adjuntos, comercial
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, uno
from kallicode_core.errors import AppError
from kallicode_testing.storage_client import AZURITE_CONNECTION_STRING
from sqlalchemy import text

pytestmark = pytest.mark.integracion


@pytest.fixture
async def almacen(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "blob_connection_string", AZURITE_CONNECTION_STRING)
    tenant = f"org_att_{uuid.uuid4().hex}"
    aid = f"att_{uuid.uuid4().hex}"
    ref = f"{tenant}/adjuntos/{aid}/subida.png"
    async with sesion_sistema() as db:
        # Un esquema sin migrar también puede ejecutar esta regresión, con rollback del DDL.
        existe = await uno(
            db,
            """SELECT 1 FROM information_schema.columns
            WHERE table_schema='core' AND table_name='ticket_attachments'
            AND column_name='confirmado'""",
        )
        if not existe:
            sql = Path("db/migrations/039_confirmacion_adjuntos.sql").read_text()
            for sentencia in sql.split(";"):
                if sentencia.strip():
                    await db.execute(text(sentencia))
        await db.execute(
            text("INSERT INTO core.organizations (id,nombre) VALUES (:t,'Adjuntos test')"),
            {"t": tenant},
        )
        await db.execute(
            text("INSERT INTO core.plans (codigo,nombre,storage_mb) VALUES (:p,'Adjuntos test',1)"),
            {"p": tenant},
        )
        await db.execute(
            text("""INSERT INTO core.subscriptions
            (id,tenant_id,plan_codigo,estado,inicia_el,renueva_el)
            VALUES (:t,:t,:t,'activa',current_date,current_date+30)"""),
            {"t": tenant},
        )
        await db.execute(
            text("""INSERT INTO core.ticket_attachments
            (id,tenant_id,nombre_archivo,content_type,tamano_bytes,blob_ref)
            VALUES (:a,:t,'subida.png','image/png',1,:r)"""),
            {"a": aid, "t": tenant, "r": ref},
        )
        async with adjuntos.cliente_blob() as cliente:
            contenedor = cliente.get_container_client(s.blob_container_evidencia)
            try:
                await contenedor.create_container()
            except ResourceExistsError:
                pass
            objeto = cliente.get_blob_client(s.blob_container_evidencia, ref)
            try:
                yield db, tenant, aid, objeto
            finally:
                try:
                    await objeto.delete_blob()
                except ResourceNotFoundError:
                    pass
                finally:
                    await db.rollback()


async def test_declara_un_byte_y_cuota_refleja_dos_mb(almacen):
    db, tenant, aid, objeto = almacen
    await objeto.upload_blob(b"x" * (2 * 1024 * 1024))
    resultado, error = await adjuntos.confirmar(db, tenant, aid)
    assert error.http == 402
    assert not resultado["confirmado"]
    fila = await uno(db, "SELECT * FROM core.ticket_attachments WHERE id=:a", {"a": aid})
    assert fila["tamano_bytes"] == 2 * 1024 * 1024
    assert fila["content_type"] == "text/plain"
    with pytest.raises(AppError) as exc:
        await tickets._validar_adjuntos(db, tenant, [aid])
    assert exc.value.http == 404
    with pytest.raises(AppError) as exc:
        await comercial.verificar_storage(db, tenant, 1)
    assert exc.value.codigo == "ALMACENAMIENTO_AGOTADO"


async def test_objeto_limpio_corregido_y_asociable(almacen, monkeypatch):
    db, tenant, aid, objeto = almacen
    await objeto.upload_blob(b"texto de prueba\n")
    monkeypatch.setattr(adjuntos, "analizar", AsyncMock(return_value="limpio"))
    resultado, error = await adjuntos.confirmar(db, tenant, aid)
    assert error is None
    assert resultado["content_type"] == "text/plain"
    assert resultado["tamano_bytes"] == 16
    await tickets._validar_adjuntos(db, tenant, [aid])


async def test_sas_subida_no_permite_sobrescribir(almacen):
    from azure.core.exceptions import HttpResponseError
    from azure.storage.blob.aio import BlobClient
    from kallicode_api.servicios import blob

    _, _, _, objeto = almacen
    url, _ = blob.url_subida(objeto.blob_name, "image/png")
    async with BlobClient.from_blob_url(url) as cliente:
        await cliente.upload_blob(b"original", overwrite=True)
        with pytest.raises(HttpResponseError):
            await cliente.upload_blob(b"malicioso", overwrite=True)
    assert await (await objeto.download_blob()).readall() == b"original"


async def test_eicar_confirmacion_y_descarga_423(almacen, monkeypatch):
    s = get_settings()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(s.clamav_host, s.clamav_port), 2)
        writer.close()
        await writer.wait_closed()
    except (OSError, TimeoutError):
        pytest.skip("ClamAV no está disponible; iniciar el servicio clamav de Compose")
    db, tenant, aid, objeto = almacen
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    await objeto.upload_blob(eicar)
    resultado, error = await adjuntos.confirmar(db, tenant, aid)
    assert error.http == 423
    assert resultado["escaneo"] == "infectado"
    assert not resultado["confirmado"]
    fila = await uno(db, "SELECT * FROM core.ticket_attachments WHERE id=:a", {"a": aid})

    @asynccontextmanager
    async def sesion(_):
        yield db

    monkeypatch.setattr(tickets, "sesion_de", sesion)
    monkeypatch.setattr(tickets, "_resolver_ticket", AsyncMock(return_value={"id": "tk_test"}))
    monkeypatch.setattr(tickets, "uno", AsyncMock(return_value=fila))
    with pytest.raises(AppError) as exc:
        await tickets.descargar("KC-1", aid, SimpleNamespace(tenant_id=tenant))
    assert exc.value.http == 423


async def test_aviso_80_persistente_sin_duplicados(almacen):
    db, tenant, aid, _ = almacen
    await db.execute(
        text("""INSERT INTO core.users (id,tenant_id,email,nombre,rol)
        VALUES (:t,:t,:email,'Owner test','owner')"""),
        {"t": tenant, "email": f"{tenant}@example.test"},
    )
    await db.execute(
        text("UPDATE core.ticket_attachments SET tamano_bytes=838861 WHERE id=:a"), {"a": aid}
    )
    assert (await comercial.verificar_storage(db, tenant, 0))["codigo"] == "ALMACENAMIENTO_AVISO"
    await comercial.verificar_storage(db, tenant, 0)
    fila = await uno(
        db, "SELECT count(*) AS n FROM core.notifications WHERE tenant_id=:t", {"t": tenant}
    )
    assert fila["n"] == 1
