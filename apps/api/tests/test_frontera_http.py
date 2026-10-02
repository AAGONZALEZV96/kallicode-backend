import pytest
from kallicode_api.main import crear_app
from kallicode_api.middleware import validar_cors
from kallicode_core import comercial
from kallicode_core import db as db_module
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

CABECERAS = [
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "content-security-policy",
]

SUPERFICIES = [
    ("GET", "/health"),
    ("GET", "/api/v1/tickets"),
    ("POST", "/api/v1/webhooks/github/inexistente"),
    ("GET", "/internal/v1/inexistente"),
]


@pytest.mark.parametrize(("metodo", "ruta"), SUPERFICIES)
async def test_toda_respuesta_lleva_cabeceras_de_seguridad(client, metodo, ruta):
    r = await client.request(metodo, ruta)
    for h in CABECERAS:
        assert h in r.headers, f"{h} falta en {metodo} {ruta} (status {r.status_code})"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


async def test_cors_con_asterisco_y_credenciales_aborta():
    with pytest.raises(RuntimeError):
        validar_cors(["*"], allow_credentials=True)
    assert validar_cors(["http://localhost:5173"], allow_credentials=True)


async def test_arranque_aborta_si_cors_origins_tiene_asterisco(monkeypatch):
    malos = get_settings().model_copy(update={"cors_origins": "*"})
    monkeypatch.setattr("kallicode_api.main.get_settings", lambda: malos)
    with pytest.raises(RuntimeError):
        crear_app()


async def test_cors_enumera_metodos_y_cabeceras(client):
    r = await client.options(
        "/api/v1/tickets",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,x-trace-id",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "*" not in r.headers["access-control-allow-methods"]
    assert "*" not in r.headers["access-control-allow-headers"]


async def test_host_no_permitido_devuelve_400(client):
    r = await client.get("/health", headers={"Host": "evil.com"})
    assert r.status_code == 400
    assert "x-frame-options" in r.headers


async def test_cuerpo_excesivo_413_en_portal(client):
    limite = get_settings().max_body_bytes
    r = await client.post("/api/v1/auth/login", content=b"x" * (limite + 1))
    assert r.status_code == 413
    assert r.json()["error"]["codigo"] == "CUERPO_DEMASIADO_GRANDE"


async def test_cuerpo_excesivo_413_en_webhooks(client):
    limite = get_settings().max_body_bytes
    r = await client.post("/api/v1/webhooks/github/inexistente", content=b"x" * (limite + 1))
    assert r.status_code == 413
    assert r.json()["error"]["codigo"] == "CUERPO_DEMASIADO_GRANDE"


async def test_lifespan_crea_y_cierra_recursos():
    app = crear_app()
    async with app.router.lifespan_context(app):
        assert db_module._engine_admin is not None
        assert comercial._redis is not None
        engine = db_module._engine_admin
        async with sesion_sistema() as db:
            await db.execute(text("SELECT 1"))
        assert engine.pool.checkedin() >= 1

    assert db_module._engine_admin is None
    assert db_module._engine_app is None
    assert comercial._redis is None
    assert engine.pool.checkedin() == 0
    assert engine.pool.checkedout() == 0
