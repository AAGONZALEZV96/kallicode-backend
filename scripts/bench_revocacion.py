"""Mide la latencia de estado_revocacion (un MGET de 3 claves) contra Redis real."""

import asyncio
import time
import uuid

from kallicode_core import comercial
from kallicode_core.redis_keys import clave_tenant
from kallicode_core.seguridad import (
    estado_revocacion,
    fijar_rol_vigente,
    invalidar_usuario,
)

N = 2000
CALENTAMIENTO = 100


async def main() -> None:
    tenant = f"bench-{uuid.uuid4().hex[:8]}"
    user = "u-bench"
    jti = uuid.uuid4().hex

    await fijar_rol_vigente(tenant, user, "viewer")
    await invalidar_usuario(tenant, user)

    for _ in range(CALENTAMIENTO):
        await estado_revocacion(tenant, user, jti)

    tiempos = []
    for _ in range(N):
        t0 = time.perf_counter()
        await estado_revocacion(tenant, user, jti)
        tiempos.append((time.perf_counter() - t0) * 1000)

    tiempos.sort()
    p50 = tiempos[N // 2]
    p95 = tiempos[int(N * 0.95)]
    p99 = tiempos[int(N * 0.99)]
    print(f"p50={p50:.2f} ms p95={p95:.2f} ms p99={p99:.2f} ms (n={N})")

    r = comercial.redis_cliente()
    await r.delete(
        clave_tenant(tenant, "revoc", "sello", user),
        clave_tenant(tenant, "revoc", "rol", user),
    )
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
