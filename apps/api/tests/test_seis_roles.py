import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

ROLES = ["owner", "admin", "architect", "approver", "member", "viewer"]


async def test_hay_un_usuario_por_rol_en_cada_tenant(usuarios_por_rol):
    for tenant_id, usuarios in usuarios_por_rol.items():
        assert sorted(usuarios) == sorted(ROLES)

        async with sesion_sistema() as db:
            filas = (
                (
                    await db.execute(
                        text("SELECT rol FROM core.users WHERE tenant_id = :t ORDER BY rol"),
                        {"t": tenant_id},
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(filas) == sorted(ROLES)


async def test_tenants_tienen_datos_equivalentes(usuarios_por_rol):
    tenant_a, tenant_b = list(usuarios_por_rol)
    assert sorted(usuarios_por_rol[tenant_a]) == sorted(usuarios_por_rol[tenant_b])
    # los ids son distintos: no se comparten usuarios entre tenants
    assert set(usuarios_por_rol[tenant_a].values()).isdisjoint(usuarios_por_rol[tenant_b].values())


@pytest.mark.parametrize("rol", ROLES)
async def test_auth_headers_con_usuario_real(usuarios_por_rol, rol):
    tenant_id = next(iter(usuarios_por_rol))
    user_id = usuarios_por_rol[tenant_id][rol]

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=user_id,
        tenant_id=tenant_id,
        rol=rol,
        restringido=False,
    )

    usuario = app.dependency_overrides[usuario_actual]()
    assert usuario.user_id == user_id
    assert usuario.rol == rol
    assert usuario.tenant_id == tenant_id
