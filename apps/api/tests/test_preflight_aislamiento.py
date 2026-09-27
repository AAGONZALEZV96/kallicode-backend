from .conftest import auth_headers


async def test_preflight_de_tenant_a_no_autoriza_deploy_de_tenant_b(client, seed_dos_tenants, crear_ticket):
    tenant_a, tenant_b = seed_dos_tenants
    ticket = await crear_ticket(tenant_id=tenant_a, etapa="deploy")

    # Simula que tenant_a ya tiene un preflight "go" válido en Redis
    from kallicode_core import comercial
    from kallicode_core.redis_keys import clave_redis
    await comercial.redis_cliente().setex(
        clave_redis(tenant_a, "preflight", ticket["numero"], "produccion"), 3600, "go"
    )

    # tenant_b intenta usar el mismo número de ticket — no debería encontrar el preflight
    r = comercial.redis_cliente()
    valor = await r.get(clave_redis(tenant_b, "preflight", ticket["numero"], "produccion"))
    assert valor is None