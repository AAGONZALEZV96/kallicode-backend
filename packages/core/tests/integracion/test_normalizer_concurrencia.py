"""Test de concurrencia del normalizador (KC-BE-04-4 · cierra BE-F17).

Verifica que dos ejecuciones concurrentes de `procesar_inbox` con el mismo
`inbox_id` crean exactamente un ticket.

Diseño del fix (sin estado 'procesando', que no existe en wi_estado_chk):
  T1 (sesion_sistema, solo lectura): si estado='pendiente', lee
      tenant/evento/payload.
  T2 (sesion_tenant, escritura): SELECT ... FOR UPDATE; si ya 'procesado'
      o con ticket_id, sale. Si 'pendiente': inserta ticket, marca
      'procesado', registra auditoría. Dos workers: el segundo ve
      'procesado' y sale.

Patrón (ver test_aislamiento.py): tenant + conexión fijos con ON CONFLICT
DO NOTHING, sin cleanup. Cada test usa un delivery_id único; la aserción
usa delta de count sobre core.tickets, así que es re-ejecutable.

Requiere PostgreSQL real. Se marca como integración.
"""

from __future__ import annotations

import asyncio
import json
import secrets

import pytest
from kallicode_core.db import sesion_sistema, uno, valor
from kallicode_workers import normalizer
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


# Tenant y conexión fijos (patrón test_aislamiento.py)
TENANT = "org_test_normalizer_conc"
CONNECTION = "conn_test_normalizer_conc"


async def _asegurar_tenant_y_conexion() -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) VALUES (:i, :n) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"i": TENANT, "n": "Test normalizer concurrencia"},
        )
        await db.execute(
            text("""
                INSERT INTO core.connections (
                    id, tenant_id, categoria, proveedor, nombre, estado, config
                )
                VALUES (:id, :t, 'tickets', 'github', 'Test', 'conectada',
                        CAST('{}' AS jsonb))
                ON CONFLICT (id) DO NOTHING
            """),
            {"id": CONNECTION, "t": TENANT},
        )


async def _crear_webhook_inbox(payload: dict) -> int:
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            INSERT INTO core.webhook_inbox (
                tenant_id, connection_id, evento, payload, estado, delivery_id
            )
            VALUES (:t, :c, 'issues', CAST(:p AS jsonb), 'pendiente', :d)
            RETURNING id
            """,
            {
                "t": TENANT,
                "c": CONNECTION,
                "p": json.dumps(payload),
                "d": f"del_{secrets.token_hex(8)}",
            },
        )
    return int(fila["id"])


async def _contar_tickets() -> int:
    async with sesion_sistema() as db:
        return int(
            await valor(
                db,
                "SELECT count(*) FROM core.tickets WHERE tenant_id = :t",
                {"t": TENANT},
            )
        )


def _payload_github(numero: int) -> dict:
    return {
        "action": "opened",
        "issue": {
            "title": f"Bug de prueba {numero}",
            "body": "Cuerpo de prueba",
            "labels": [{"name": "bug"}],
            "number": numero,
        },
    }


async def test_dos_procesar_inbox_concurrentes_crean_un_solo_ticket() -> None:
    await _asegurar_tenant_y_conexion()
    antes = await _contar_tickets()

    inbox_id = await _crear_webhook_inbox(_payload_github(1001))

    campos = {"inbox_id": str(inbox_id)}
    await asyncio.gather(
        normalizer.procesar_inbox(campos),
        normalizer.procesar_inbox(campos),
    )

    despues = await _contar_tickets()
    assert despues - antes == 1, f"Se esperaba 1 ticket nuevo, se crearon {despues - antes}"

    async with sesion_sistema() as db:
        fila = await uno(
            db,
            "SELECT estado, ticket_id FROM core.webhook_inbox WHERE id = :i",
            {"i": inbox_id},
        )
    assert fila["estado"] == "procesado"
    assert fila["ticket_id"] is not None


async def test_procesar_inbox_idempotente_secuencial() -> None:
    await _asegurar_tenant_y_conexion()
    antes = await _contar_tickets()

    inbox_id = await _crear_webhook_inbox(_payload_github(1002))

    await normalizer.procesar_inbox({"inbox_id": str(inbox_id)})
    await normalizer.procesar_inbox({"inbox_id": str(inbox_id)})

    despues = await _contar_tickets()
    assert despues - antes == 1, (
        f"Se esperaba 1 ticket nuevo tras dos llamadas, se crearon {despues - antes}"
    )
