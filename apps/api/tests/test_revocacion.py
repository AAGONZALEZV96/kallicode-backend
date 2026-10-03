from kallicode_core.seguridad import emitir_access_token


async def test_logout_deja_el_access_token_inservible(client, redis_doble, usuarios_por_rol):
    from kallicode_core.db import sesion_sistema
    from kallicode_core.seguridad import hashear_password
    from sqlalchemy import text

    tenant = next(iter(usuarios_por_rol))
    uid = usuarios_por_rol[tenant]["member"]
    email = f"member-{uid}@kallicode-tests.com"
    async with sesion_sistema() as db:
        await db.execute(
            text("UPDATE core.users SET password_hash=:p, email=:e WHERE id=:i"),
            {"p": hashear_password("ClaveSegura12345"), "e": email, "i": uid},
        )
        await db.commit()

    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "ClaveSegura12345"}
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    cab = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (await client.get("/api/v1/users/me", headers=cab)).status_code == 200

    out = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}, headers=cab
    )
    assert out.status_code == 200, out.text

    despues = await client.get("/api/v1/users/me", headers=cab)
    assert despues.status_code == 401
    assert despues.json()["error"]["codigo"] == "AUTH_TOKEN_REVOCADO"


async def test_desactivar_usuario_corta_su_token(client, redis_doble, usuarios_por_rol):
    tenant = next(iter(usuarios_por_rol))
    ids = usuarios_por_rol[tenant]

    token_owner = emitir_access_token(ids["owner"], tenant, "owner")
    token_member = emitir_access_token(ids["member"], tenant, "member")
    cab_member = {"Authorization": f"Bearer {token_member}"}

    assert (await client.get("/api/v1/users/me", headers=cab_member)).status_code == 200

    r = await client.patch(
        f"/api/v1/users/{ids['member']}",
        json={"estado": "desactivado"},
        headers={"Authorization": f"Bearer {token_owner}"},
    )
    assert r.status_code == 200, r.text

    despues = await client.get("/api/v1/users/me", headers=cab_member)
    assert despues.status_code == 401
    assert despues.json()["error"]["codigo"] == "AUTH_TOKEN_REVOCADO"


async def test_architect_degradado_no_puede_firmar_gate3(client, redis_doble, usuarios_por_rol):
    from kallicode_core.db import sesion_sistema
    from sqlalchemy import text

    tenant = next(iter(usuarios_por_rol))
    ids = usuarios_por_rol[tenant]

    # segundo architect para no chocar con ULTIMO_ARQUITECTO
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:i, :t, :e, 'Architect 2', 'architect')"
            ),
            {"i": f"u-arch2-{tenant[:8]}", "t": tenant, "e": f"arch2@{tenant[:8]}.test"},
        )
        await db.commit()

    token_owner = emitir_access_token(ids["owner"], tenant, "owner")
    token_arq = emitir_access_token(ids["architect"], tenant, "architect")

    r = await client.patch(
        f"/api/v1/users/{ids['architect']}",
        json={"rol": "viewer"},
        headers={"Authorization": f"Bearer {token_owner}"},
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/v1/tickets/1/gates/3/approve",
        json={},
        headers={"Authorization": f"Bearer {token_arq}"},
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["codigo"] == "PERMISO_DENEGADO", r.text


async def test_denylist_expira_y_su_memoria_esta_acotada(redis_doble):
    from kallicode_core.redis_keys import clave_tenant
    from kallicode_core.seguridad import fijar_rol_vigente, invalidar_usuario, revocar_jti

    await revocar_jti("t1", "jti1", 1800)
    for _ in range(5):
        await invalidar_usuario("t1", "u1")
        await fijar_rol_vigente("t1", "u1", "viewer")

    k_jti = clave_tenant("t1", "revoc", "jti", "jti1")
    k_sello = clave_tenant("t1", "revoc", "sello", "u1")
    k_rol = clave_tenant("t1", "revoc", "rol", "u1")

    assert await redis_doble.ttl(k_jti) == 1800  # igual al restante del token
    for k in (k_sello, k_rol):
        assert 0 < await redis_doble.ttl(k) <= 24 * 3600  # todo expira

    # repetir invalidaciones no crea claves nuevas: 1 jti + 1 sello + 1 rol
    assert len(redis_doble._data) == 3
