from .conftest import auth_headers


async def test_kanban_no_muestra_produccion_de_otro_tenant(client, seed_dos_tenants, crear_ticket):
    tenant_a, tenant_b = seed_dos_tenants
    ticket = await crear_ticket(tenant_id=tenant_a, etapa="produccion")

    headers = auth_headers(tenant_b)
    # CORREGIDO: Se agregó el prefijo /api/v1
    resp = await client.get("/api/v1/dashboard/kanban", headers=headers)
    ids_visibles = [t["id"] for t in resp.json()["columnas"].get("produccion", [])]

    assert ticket["numero"] not in ids_visibles


async def test_kanban_si_muestra_produccion_del_propio_tenant(
    client, seed_dos_tenants, crear_ticket
):
    tenant_a, _ = seed_dos_tenants
    ticket = await crear_ticket(tenant_id=tenant_a, etapa="produccion")

    headers = auth_headers(tenant_a)
    # CORREGIDO: Se agregó el prefijo /api/v1
    resp = await client.get("/api/v1/dashboard/kanban", headers=headers)
    ids_visibles = [t["id"] for t in resp.json()["columnas"].get("produccion", [])]

    assert ticket["numero"] in ids_visibles
