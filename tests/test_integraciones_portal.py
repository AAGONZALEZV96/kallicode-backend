"""Verificadores de KC-BE-07-1 con el cuerpo que manda el portal (asistente de onboarding).

El portal arma `config` y `credenciales` desde su catálogo (proveedores.ts, armarCuerpo):
repositorios con `repo_url` (la URL completa, que también lee CodeMapping) y bases de datos
con `host`, `puerto`, `base` y `db_url`. Antes estos cuerpos fallaban siempre en la prueba
(«Falta el repositorio objetivo…»), así que no se podía crear ninguna conexión desde el
asistente.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from kallicode_api.servicios import integraciones

VCR_DIR = Path(__file__).resolve().parent / "fixtures" / "vcr"


def _cargar_vcr(
    router: respx.MockRouter, cassette: str, reemplazos: dict[str, str] | None = None
) -> None:
    """Reproduce la grabación «valido» de `cassette`, con las URLs ajustadas si hace falta."""
    datos = json.loads((VCR_DIR / f"{cassette}.json").read_text(encoding="utf-8"))
    for item in datos["escenarios"]["valido"]:
        url = item["url"]
        for antes, despues in (reemplazos or {}).items():
            url = url.replace(antes, despues)
        router.request(item["method"], url).mock(
            return_value=httpx.Response(
                status_code=item["status_code"],
                headers=item.get("headers") or {},
                json=item.get("body"),
            )
        )


def _cuerpo_repositorio(repo_url: str, token: str) -> tuple[dict, dict]:
    """config y credenciales como las arma el portal para un repositorio o un CI."""
    return {"repo_url": repo_url}, {"token": token, "repo_url": repo_url}


@pytest.mark.parametrize(
    ("proveedor", "cassette", "repo_url"),
    [
        ("github", "github", "https://github.com/kallicode/demo-app"),
        ("github", "github", "https://github.com/kallicode/demo-app.git"),
        ("github", "github", "https://github.com/kallicode/demo-app/tree/main"),
        ("github_actions", "github_actions", "https://github.com/kallicode/demo-app"),
        ("github_issues", "github", "https://github.com/kallicode/demo-app"),
        ("gitlab", "gitlab", "https://gitlab.com/kallicode/demo-app"),
        ("gitlab", "gitlab", "https://gitlab.com/kallicode/demo-app/-/tree/main"),
        ("gitlab_ci", "gitlab", "https://gitlab.com/kallicode/demo-app"),
    ],
)
async def test_repositorio_con_la_url_que_manda_el_portal(
    proveedor: str, cassette: str, repo_url: str
) -> None:
    config, credenciales = _cuerpo_repositorio(repo_url, "token_valido")

    # assert_all_called: se consultan exactamente las URLs grabadas (repo kallicode/demo-app).
    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, cassette)
        res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is True, res["mensaje"]
    assert "kallicode/demo-app" in res["mensaje"]


@pytest.mark.parametrize(
    ("proveedor", "cassette", "repo_url", "reemplazos"),
    [
        (
            "github",
            "github",
            "https://git.empresa.com/kallicode/demo-app",
            {"https://api.github.com": "https://git.empresa.com/api/v3"},
        ),
        (
            "github_actions",
            "github_actions",
            "https://git.empresa.com/kallicode/demo-app",
            {"https://api.github.com": "https://git.empresa.com/api/v3"},
        ),
        (
            "gitlab",
            "gitlab",
            "https://gitlab.empresa.com/kallicode/demo-app",
            {"https://gitlab.com/api/v4": "https://gitlab.empresa.com/api/v4"},
        ),
    ],
)
async def test_servidor_propio_el_token_va_a_su_api_y_no_a_la_publica(
    proveedor: str, cassette: str, repo_url: str, reemplazos: dict[str, str]
) -> None:
    config, credenciales = _cuerpo_repositorio(repo_url, "token_de_la_empresa")

    # Sin ruta para api.github.com ni gitlab.com: si el token fuera ahí, respx fallaría.
    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, cassette, reemplazos)
        res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is True, res["mensaje"]
    for llamada in router.calls:
        assert llamada.request.url.host in ("git.empresa.com", "gitlab.empresa.com")


async def test_gitlab_con_subgrupos_usa_la_ruta_completa() -> None:
    config, credenciales = _cuerpo_repositorio(
        "https://gitlab.com/kallicode/plataforma/demo-app/-/merge_requests", "glpat"
    )

    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, "gitlab", {"kallicode%2Fdemo-app": "kallicode%2Fplataforma%2Fdemo-app"})
        res = await integraciones.probar("gitlab", config, credenciales)

    assert res["ok"] is True, res["mensaje"]


@pytest.mark.parametrize("proveedor", ["github", "gitlab"])
async def test_url_sin_ruta_de_repositorio_avisa_que_falta(proveedor: str) -> None:
    config, credenciales = _cuerpo_repositorio(f"https://{proveedor}.com", "token")

    res = await integraciones.probar(proveedor, config, credenciales)

    assert res["ok"] is False
    assert "URL del repositorio" in res["mensaje"]


@pytest.mark.parametrize("api_url", ["https://api.github.com", "https://ghe.interna/api/v3"])
async def test_api_url_explicita_tiene_prioridad_sobre_repo_url(api_url: str) -> None:
    config = {"repo_url": "https://git.empresa.com/kallicode/demo-app", "api_url": api_url}

    with respx.mock(assert_all_called=True) as router:
        _cargar_vcr(router, "github", {"https://api.github.com": api_url})
        res = await integraciones.probar("github", config, {"token": "t"})

    assert res["ok"] is True, res["mensaje"]


# ---- bases de datos ----


def _conexion_pg_valida() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": False,
        }
    )
    conn.fetchval = AsyncMock(side_effect=[0, 14])
    conn.close = AsyncMock()
    return conn


async def test_postgresql_con_el_cuerpo_del_portal_usa_db_url() -> None:
    db_url = "postgresql://kallicode_meta:s%40cret@db.cliente.internal:6543/erp"
    conectar = AsyncMock(return_value=_conexion_pg_valida())

    with patch("asyncpg.connect", new=conectar):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "puerto": 6543, "base": "erp"},
            {"usuario": "kallicode_meta", "contrasena": "s@cret", "db_url": db_url},
        )

    assert res["ok"] is True, res["mensaje"]
    assert conectar.await_args.kwargs["dsn"] == db_url


async def test_postgresql_sin_db_url_usa_puerto_y_base() -> None:
    conectar = AsyncMock(return_value=_conexion_pg_valida())

    with patch("asyncpg.connect", new=conectar):
        res = await integraciones.probar(
            "postgresql",
            {"host": "db.cliente.internal", "puerto": 6543, "base": "erp"},
            {"usuario": "kallicode_meta", "contrasena": "secret"},
        )

    assert res["ok"] is True, res["mensaje"]
    argumentos = conectar.await_args.kwargs
    assert (argumentos["port"], argumentos["database"], argumentos["password"]) == (
        6543,
        "erp",
        "secret",
    )
