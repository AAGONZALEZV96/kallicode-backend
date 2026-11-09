"""Regresiones de QU-3 y del protocolo ClamAV, sin servicios externos."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kallicode_core import adjuntos, comercial
from kallicode_core.errors import AppError


class DB:
    def __init__(self):
        self.fila = {
            "id": "att_test",
            "tenant_id": "org_test",
            "confirmado": False,
            "escaneo": "pendiente",
            "tamano_bytes": 1,
            "blob_ref": "org_test/subida",
            "content_type": "image/png",
            "ticket_id": None,
        }

    async def execute(self, sql, params):
        if str(sql).lstrip().startswith("UPDATE"):
            for key, col in [
                ("n", "tamano_bytes"),
                ("ct", "content_type"),
                ("e", "escaneo"),
                ("listo", "confirmado"),
                ("ref", "blob_ref"),
            ]:
                if key in params:
                    self.fila[col] = params[key]


@pytest.fixture
async def entorno(monkeypatch):
    db = DB()
    datos = b"contenido real" * 100
    objeto = SimpleNamespace(
        set_http_headers=AsyncMock(),
        get_blob_properties=AsyncMock(return_value=SimpleNamespace(size=len(datos), etag='"v1"')),
        download_blob=AsyncMock(
            return_value=SimpleNamespace(readall=AsyncMock(return_value=datos))
        ),
    )

    @asynccontextmanager
    async def cliente():
        yield SimpleNamespace(get_blob_client=lambda *a: objeto)

    monkeypatch.setattr(adjuntos, "cliente_blob", cliente)
    monkeypatch.setattr(adjuntos, "uno", AsyncMock(side_effect=lambda *a: dict(db.fila)))
    monkeypatch.setattr(adjuntos, "analizar", AsyncMock(return_value="limpio"))
    monkeypatch.setattr(adjuntos, "detectar_tipo", AsyncMock(return_value="text/plain"))
    monkeypatch.setattr(comercial, "bloquear_storage", AsyncMock())
    monkeypatch.setattr(
        comercial,
        "uno",
        AsyncMock(side_effect=lambda *a: {"limite_mb": 1, "usados": db.fila["tamano_bytes"]}),
    )
    return db, objeto


async def test_tamano_y_tipo_reales_y_confirmacion_idempotente(entorno):
    db, objeto = entorno
    resultado, error = await adjuntos.confirmar(db, "org_test", "att_test")
    assert error is None
    assert resultado["confirmado"]
    assert db.fila["tamano_bytes"] == 1400
    assert db.fila["content_type"] == "text/plain"
    assert db.fila["escaneo"] == "limpio"
    objeto.get_blob_properties.reset_mock()
    await adjuntos.confirmar(db, "org_test", "att_test")
    objeto.get_blob_properties.assert_not_awaited()


async def test_tamano_falso_rechazado_y_cuota_real(entorno):
    db, objeto = entorno
    objeto.get_blob_properties.return_value.size = 2 * 1024 * 1024
    resultado, error = await adjuntos.confirmar(db, "org_test", "att_test")
    assert error.codigo == "ALMACENAMIENTO_AGOTADO"
    assert error.http == 402
    assert not resultado["confirmado"]
    assert db.fila["tamano_bytes"] == 2 * 1024 * 1024
    with pytest.raises(AppError, match="ALMACENAMIENTO_AGOTADO"):
        await comercial.verificar_storage(db, "org_test", 1)


async def test_tamano_mayor_constraint_anterior_persiste(entorno):
    db, objeto = entorno
    objeto.get_blob_properties.return_value.size = 1024**3
    _, error = await adjuntos.confirmar(db, "org_test", "att_test")
    assert error.http == 402
    assert db.fila["tamano_bytes"] == 1024**3
    assert not db.fila["confirmado"]


async def test_infectado_retorna_423_y_no_habilita(entorno, monkeypatch):
    db, _ = entorno
    monkeypatch.setattr(adjuntos, "analizar", AsyncMock(return_value="infectado"))
    _, error = await adjuntos.confirmar(db, "org_test", "att_test")
    assert error.http == 423
    assert db.fila["escaneo"] == "infectado"
    assert not db.fila["confirmado"]


async def test_ejecutable_disfrazado_de_png(entorno, monkeypatch):
    db, _ = entorno
    monkeypatch.setattr(adjuntos, "detectar_tipo", AsyncMock(return_value="application/x-dosexec"))
    _, error = await adjuntos.confirmar(db, "org_test", "att_test")
    assert error.http == 415
    assert db.fila["content_type"] == "application/x-dosexec"
    assert not db.fila["confirmado"]


async def test_antivirus_caido_no_habilita_y_conserva_tamano(entorno, monkeypatch):
    db, _ = entorno
    monkeypatch.setattr(adjuntos, "analizar", AsyncMock(side_effect=ConnectionError))
    _, error = await adjuntos.confirmar(db, "org_test", "att_test")
    assert error.http == 503
    assert db.fila["tamano_bytes"] == 1400
    assert not db.fila["confirmado"]


@pytest.mark.parametrize("usados,aviso", [(838860, False), (838861, True), (1048576, True)])
async def test_aviso_80_por_ciento(monkeypatch, usados, aviso):
    monkeypatch.setattr(
        comercial, "uno", AsyncMock(return_value={"limite_mb": 1, "usados": usados})
    )
    resultado = await comercial.verificar_storage(DB(), "org_test", 0)
    assert bool(resultado) == aviso


@pytest.mark.parametrize(
    "respuesta,esperado",
    [
        (b"stream: OK\0", "limpio"),
        (b"stream: Eicar-Test-Signature FOUND\0", "infectado"),
        (b"stream: size limit exceeded ERROR\0", None),
    ],
)
async def test_protocolo_clamav(monkeypatch, respuesta, esperado):
    class Writer:
        def __init__(self):
            self.datos = bytearray()

        def write(self, datos):
            self.datos.extend(datos)

        async def drain(self):
            pass

        def close(self):
            pass

        async def wait_closed(self):
            pass

    writer = Writer()
    reader = SimpleNamespace(readuntil=AsyncMock(return_value=respuesta))
    monkeypatch.setattr(
        adjuntos.asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    if esperado:
        assert await adjuntos.analizar(eicar) == esperado
    else:
        with pytest.raises(AppError) as exc:
            await adjuntos.analizar(eicar)
        assert exc.value.http == 503
    assert writer.datos.startswith(b"zINSTREAM\0")
    assert eicar in writer.datos
    assert writer.datos.endswith(b"\0\0\0\0")


async def test_libmagic_no_confia_en_extension():
    assert await adjuntos.detectar_tipo(b"texto normal\n") == "text/plain"
    assert (
        await adjuntos.detectar_tipo(Path("/usr/bin/true").read_bytes())
        not in adjuntos.TIPOS_ADJUNTO
    )


async def test_error_confirmacion_se_lanza_despues_de_cerrar_transaccion(entorno, monkeypatch):
    from kallicode_api.routers import tickets

    db, objeto = entorno
    objeto.get_blob_properties.return_value.size = 2 * 1024 * 1024
    cerrado = False

    @asynccontextmanager
    async def sesion(_):
        nonlocal cerrado
        yield db
        cerrado = True

    monkeypatch.setattr(tickets, "sesion_de", sesion)
    with pytest.raises(AppError) as exc:
        await tickets.confirmar_adjunto("att_test", SimpleNamespace(tenant_id="org_test"))
    assert exc.value.http == 402
    assert cerrado
    assert db.fila["tamano_bytes"] == 2 * 1024 * 1024


@pytest.mark.parametrize("escaneo", ["pendiente", "infectado"])
async def test_descarga_bloqueada_hasta_confirmacion(monkeypatch, escaneo):
    from kallicode_api.routers import tickets

    @asynccontextmanager
    async def sesion(_):
        yield DB()

    monkeypatch.setattr(tickets, "sesion_de", sesion)
    monkeypatch.setattr(tickets, "_resolver_ticket", AsyncMock(return_value={"id": "tk_test"}))
    monkeypatch.setattr(
        tickets, "uno", AsyncMock(return_value={"escaneo": escaneo, "confirmado": False})
    )
    firmar = AsyncMock()
    monkeypatch.setattr(tickets.blob, "url_descarga", firmar)
    with pytest.raises(AppError) as exc:
        await tickets.descargar("KC-1", "att_test", SimpleNamespace(tenant_id="org_test"))
    assert exc.value.http == 423
    firmar.assert_not_called()
