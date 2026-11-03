"""Pruebas de la bóveda de secretos y cifrado (BE-F05)."""

import pytest
from kallicode_api.servicios import boveda
from kallicode_core.comercial import redis_cliente
from kallicode_core.redis_keys import clave_tenant


@pytest.mark.asyncio
async def test_boveda_cifra_en_redis_local():
    tenant_id = "tenant-test-01"
    nombre_secreto = "token-reset-abc"
    valor_secreto = {"token": "super-secreto-en-claro-12345"}

    # 1. Guardamos el secreto usando nuestra interfaz
    await boveda.guardar(tenant_id, nombre_secreto, valor_secreto)

    # 2. Simulamos a un atacante leyendo directamente la base de datos (Redis)
    clave_segura = clave_tenant(tenant_id, "boveda_cifrada", nombre_secreto)
    data_cruda = await redis_cliente().get(clave_segura)

    # 3. Aserciones de Seguridad (El valor existe pero es ilegible)
    assert data_cruda is not None, "El secreto debe existir en Redis"
    assert "super-secreto-en-claro-12345" not in data_cruda, (
        "Fuga de datos: El secreto está en texto plano"
    )
    assert "token" not in data_cruda, "Fuga de datos: La estructura JSON es visible"

    # 4. Verificamos que el descifrado legítimo funciona
    valor_recuperado = await boveda.leer(tenant_id, nombre_secreto)
    assert valor_recuperado == valor_secreto, "El descifrado alteró los datos originales"

    # 5. Limpieza
    await boveda.eliminar(tenant_id, nombre_secreto)
