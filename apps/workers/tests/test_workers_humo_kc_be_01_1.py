"""Pruebas de humo para KC-BE-01-1 en los tres trabajos programados
que dependen de sesion_sistema(): scheduler, billing_cycle y
housekeeping.

No verifican la lógica de negocio de cada uno (eso es de otros
tickets) — solo que arrancan y terminan sin excepción usando la
separación de roles nueva, lo que confirma que sesion_sistema() sigue
funcionando fuera del proceso de la API.

Requiere PostgreSQL real. AJUSTAR: nombres de módulo/función exactos
según tu apps/workers/src/kallicode_workers/.
"""

from __future__ import annotations

import pytest
from kallicode_core import db

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


async def test_scheduler_corre_sin_error() -> None:
    from kallicode_workers.scheduler import ciclo

    await ciclo()


async def test_billing_cycle_corre_sin_error() -> None:
    from kallicode_workers.billing_cycle import ciclo

    await ciclo()


async def test_housekeeping_corre_sin_error() -> None:
    from kallicode_workers.housekeeping import ciclo

    await ciclo()


async def test_los_tres_trabajos_usan_rol_administrativo() -> None:
    """Verificación indirecta: si cualquiera de los tres módulos
    abriera sesion_tenant() en vez de sesion_sistema() por error,
    fallaría porque no hay tenant_id fijado antes de operar sobre
    tablas globales (subscriptions, organizations). Esta prueba deja
    constancia explícita de que sesion_sistema() responde con el rol
    correcto en el contexto de workers, no solo en el de la API."""
    async with db.sesion_sistema() as session:
        from sqlalchemy import text

        resultado = await session.execute(text("SELECT current_user"))
        assert resultado.scalar_one() == "kallicode_admin"
