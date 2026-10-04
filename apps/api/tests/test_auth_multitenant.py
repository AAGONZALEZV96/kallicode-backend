import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema
from kallicode_core.ids import nuevo
from kallicode_core.seguridad import hashear_password
from sqlalchemy import text


@pytest.mark.asyncio
async def test_login_multitenant_desambiguacion():
    email = "consultor@kallicode.dev"
    pass_t1 = "Clave_T1_Segura!"
    pass_t2 = "Clave_T2_Distinta!"

    t1_id = nuevo("t")
    t2_id = nuevo("t")
    u1_id = nuevo("u")
    u2_id = nuevo("u")

    # 1. Preparar la BD: 2 organizaciones, 2 usuarios con el MISMO correo
    async with sesion_sistema() as db:
        await db.execute(
            text("""
            INSERT INTO core.organizations (id, nombre) 
            VALUES (:id1, 'Empresa 1'), (:id2, 'Empresa 2')
        """),
            {"id1": t1_id, "id2": t2_id},
        )

        await db.execute(
            text("""
            INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado, password_hash)
            VALUES 
            (:u1, :t1, :e, 'User T1', 'admin', 'activo', :p1),
            (:u2, :t2, :e, 'User T2', 'admin', 'activo', :p2)
        """),
            {
                "u1": u1_id,
                "t1": t1_id,
                "e": email,
                "p1": hashear_password(pass_t1),
                "u2": u2_id,
                "t2": t2_id,
                "p2": hashear_password(pass_t2),
            },
        )

    # Usar AsyncClient en lugar de TestClient para no cruzar los hilos asíncronos
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 2. Login ambiguo (sin org_id) -> Frena y devuelve 409
        res_ambiguo = await ac.post(
            "/api/v1/auth/login", json={"email": email, "password": pass_t1}
        )
        assert res_ambiguo.status_code == 409
        assert "AUTH_MULTIPLE_TENANTS" in res_ambiguo.text

        # 3. Login en T1 explícito -> 200 OK (Entra a T1)
        res_t1 = await ac.post(
            "/api/v1/auth/login", json={"email": email, "password": pass_t1, "org_id": t1_id}
        )
        assert res_t1.status_code == 200
        assert res_t1.json()["usuario"]["org_id"] == t1_id

        # 4. Login en T2 explícito -> 200 OK (Entra a T2)
        res_t2 = await ac.post(
            "/api/v1/auth/login", json={"email": email, "password": pass_t2, "org_id": t2_id}
        )
        assert res_t2.status_code == 200
        assert res_t2.json()["usuario"]["org_id"] == t2_id

        # 5. Intento de cruce: Entrar a T1 con la clave de T2 -> 401 Fallo
        res_cruce = await ac.post(
            "/api/v1/auth/login", json={"email": email, "password": pass_t2, "org_id": t1_id}
        )
        assert res_cruce.status_code == 401
