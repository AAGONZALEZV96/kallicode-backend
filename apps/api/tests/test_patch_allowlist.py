"""KC-BE-07-5 (cierra BE-F36): los PATCH usan actualizar() con allowlist de columnas.

Cada endpoint cambia todas sus columnas en un solo UPDATE contra PostgreSQL real, y ningún
archivo del backend vuelve a armar un UPDATE con un f-string.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema, uno

from .conftest import auth_headers

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[3]


async def _fila(sql: str, params: dict) -> dict | None:
    async with sesion_sistema() as db:
        return await uno(db, sql, params)


async def test_patch_organizacion_cambia_sus_tres_columnas(client, seed_dos_tenants) -> None:
    tenant_a, tenant_b = seed_dos_tenants
    auth_headers(tenant_a, "owner")

    r = await client.patch(
        "/api/v1/organization",
        json={"nombre": "Org renombrada", "idioma": "en", "timezone": "America/Santiago"},
    )

    assert r.status_code == 200, r.text
    assert await _fila(
        "SELECT nombre, idioma, timezone FROM core.organizations WHERE id=:t", {"t": tenant_a}
    ) == {"nombre": "Org renombrada", "idioma": "en", "timezone": "America/Santiago"}
    # Solo su organización.
    otra = await _fila("SELECT nombre FROM core.organizations WHERE id=:t", {"t": tenant_b})
    assert otra == {"nombre": "Tenant B Test"}


async def test_patch_perfil_propio_cambia_nombre_e_idioma(client, usuarios_por_rol) -> None:
    tenant_a = next(iter(usuarios_por_rol))
    member = usuarios_por_rol[tenant_a]["member"]
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=member, tenant_id=tenant_a, rol="member", restringido=False
    )

    r = await client.patch("/api/v1/users/me", json={"nombre": "Nombre nuevo", "idioma": "pt"})

    assert r.status_code == 200, r.text
    assert await _fila("SELECT nombre, idioma FROM core.users WHERE id=:i", {"i": member}) == {
        "nombre": "Nombre nuevo",
        "idioma": "pt",
    }


async def test_patch_usuario_administrado_cambia_rol_y_estado(client, usuarios_por_rol) -> None:
    tenant_a = next(iter(usuarios_por_rol))
    member = usuarios_por_rol[tenant_a]["member"]
    auth_headers(tenant_a, "owner")

    r = await client.patch(f"/api/v1/users/{member}", json={"rol": "approver", "estado": "activo"})

    assert r.status_code == 200, r.text
    assert await _fila("SELECT rol, estado FROM core.users WHERE id=:i", {"i": member}) == {
        "rol": "approver",
        "estado": "activo",
    }


async def test_patch_ticket_cambia_titulo_descripcion_y_prioridad(
    client, seed_dos_tenants, crear_ticket
) -> None:
    tenant_a, _ = seed_dos_tenants
    ticket = await crear_ticket(tenant_id=tenant_a, etapa="triage")
    auth_headers(tenant_a, "admin")

    r = await client.patch(
        f"/api/v1/tickets/{ticket['numero']}",
        json={
            "titulo": "Título corregido",
            "descripcion": "Descripción con más detalle",
            "prioridad": "alta",
        },
    )

    assert r.status_code == 200, r.text
    assert await _fila(
        "SELECT titulo, descripcion, prioridad FROM core.tickets WHERE tenant_id=:t AND numero=:n",
        {"t": tenant_a, "n": ticket["numero"]},
    ) == {
        "titulo": "Título corregido",
        "descripcion": "Descripción con más detalle",
        "prioridad": "alta",
    }


# UPDATE armado con un f-string, también con el UPDATE en la línea siguiente de un f"""
# (con o sin text()): solo kallicode_core.db.actualizar puede hacerlo.
_UPDATE_DINAMICO = re.compile(r"""\bf(?:\"\"\"|'''|"|')\s*UPDATE\b""", re.IGNORECASE)

# Los únicos permitidos, con el motivo. Uno nuevo hace fallar el test: va por actualizar().
_PERMITIDOS = {
    # El helper: identificadores de listas fijas, validados; valores como parámetros.
    (
        "packages/core/src/kallicode_core/db.py",
        'f"UPDATE {tabla} SET {asignaciones} WHERE {filtro}")',
    ),
    # SET con columnas fijas; solo interpola la constante del módulo _COLUMNAS en RETURNING.
    ("apps/api/src/kallicode_api/routers/curation.py", 'f"""UPDATE cm.curation_queue'),
}


def test_ningun_update_se_arma_con_un_f_string_fuera_de_actualizar() -> None:
    fuentes = [
        p
        for carpeta in ("apps", "packages")
        for p in (RAIZ / carpeta).glob("*/src/**/*.py")
        if "/tests/" not in p.as_posix()
    ]
    assert len(fuentes) > 50, "no se encontraron las fuentes del backend"

    encontrados = []
    for p in fuentes:
        contenido = p.read_text(encoding="utf-8")
        for m in _UPDATE_DINAMICO.finditer(contenido):
            linea = contenido[m.start() :].splitlines()[0].strip()
            encontrados.append((p.relative_to(RAIZ).as_posix(), linea))

    assert set(encontrados) == _PERMITIDOS
    assert len(encontrados) == len(_PERMITIDOS)
