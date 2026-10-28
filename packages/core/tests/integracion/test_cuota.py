"""Trigger de cuota que respeta los ajustes negativos y reacciona a UPDATE y DELETE
(KC-DB-05-3, cierra DB-F07).

Requiere db/migrations/028 aplicada. Desde KC-DB-05-2 usage_loc es append-only:
las pruebas de UPDATE y DELETE se hacen como mantenimiento privilegiado, con el
trigger de inmutabilidad desactivado a propósito dentro de la misma transacción.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion

CICLO = "2026-10"
OTRO_CICLO = "2026-11"


class _Revertir(Exception):
    """Fuerza el rollback de la transacción de sistema tras comprobar."""


@dataclass
class Tenant:
    id: str
    plan: str


async def _crear_tenant(limite: int | None) -> Tenant:
    sfx = uuid.uuid4().hex[:10]
    t, plan = f"org_cuota_{sfx}", f"plan_cuota_{sfx}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba cuota')"),
            {"t": t},
        )
        await admin.execute(
            text("INSERT INTO core.plans (codigo, nombre, loc_mes) VALUES (:p, 'Plan prueba', :l)"),
            {"p": plan, "l": limite},
        )
        await admin.execute(
            text(
                "INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el, "
                "renueva_el) VALUES (:s, :t, :p, 'activa', '2026-01-01', '2026-02-01')"
            ),
            {"s": f"sub_{sfx}", "t": t, "p": plan},
        )
    return Tenant(t, plan)


async def _borrar_tenant(tn: Tenant) -> None:
    async with db.sesion_sistema() as admin:
        # usage_loc es append-only: la limpieza de pruebas desactiva su trigger a propósito.
        await admin.execute(
            text("ALTER TABLE core.usage_loc DISABLE TRIGGER trg_probatoria_append_only")
        )
        await admin.execute(text("DELETE FROM core.usage_loc WHERE tenant_id = :t"), {"t": tn.id})
        await admin.execute(
            text("ALTER TABLE core.usage_loc ENABLE TRIGGER trg_probatoria_append_only")
        )
        await admin.execute(
            text("DELETE FROM core.usage_cycles WHERE tenant_id = :t"), {"t": tn.id}
        )
        await admin.execute(text("DELETE FROM core.tickets WHERE tenant_id = :t"), {"t": tn.id})
        await admin.execute(
            text("DELETE FROM core.ticket_counters WHERE tenant_id = :t"), {"t": tn.id}
        )
        await admin.execute(
            text("DELETE FROM core.subscriptions WHERE tenant_id = :t"), {"t": tn.id}
        )
        await admin.execute(text("DELETE FROM core.plans WHERE codigo = :p"), {"p": tn.plan})
        await admin.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tn.id})


@pytest.fixture
async def tenant_1500() -> AsyncIterator[Tenant]:
    tn = await _crear_tenant(1500)
    try:
        yield tn
    finally:
        await _borrar_tenant(tn)


async def _pr(s: AsyncSession, netas: int, ciclo: str = CICLO, ticket: str | None = None) -> None:
    await s.execute(
        text(
            "INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, pr_url, anadidas, "
            "eliminadas, netas) VALUES (core.current_tenant(), :tk, :c, 'pr', :u, :n, 0, :n)"
        ),
        {"tk": ticket, "c": ciclo, "u": f"https://git/pr/{uuid.uuid4().hex}", "n": netas},
    )


async def _ajuste(s: AsyncSession, netas: int, ciclo: str = CICLO) -> None:
    await s.execute(
        text(
            "INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, netas, motivo_ajuste) "
            "VALUES (core.current_tenant(), :c, 'ajuste', :n, 'Credito comercial de prueba')"
        ),
        {"c": ciclo, "n": netas},
    )


async def _ciclo(tenant: str, ciclo: str = CICLO) -> tuple[int, int, str] | None:
    """(loc_consumidas, loc_netas, umbral) del ciclo, o None si no existe."""
    async with db.sesion_tenant(tenant) as s:
        fila = (
            await s.execute(
                text(
                    "SELECT loc_consumidas, loc_netas, umbral FROM core.usage_cycles WHERE ciclo = :c"
                ),
                {"c": ciclo},
            )
        ).one_or_none()
    return None if fila is None else (fila[0], fila[1], fila[2])


async def _problemas(chequeo: str) -> int:
    async with db.sesion_sistema() as admin:
        n: int = (
            await admin.execute(
                text("SELECT problemas FROM core.validar_integridad() WHERE chequeo = :c"),
                {"c": chequeo},
            )
        ).scalar_one()
    return int(n)


async def _mantenimiento(admin: AsyncSession, sql: str, params: dict[str, object]) -> None:
    """UPDATE/DELETE sobre usage_loc como lo haría un rol privilegiado."""
    await admin.execute(
        text("ALTER TABLE core.usage_loc DISABLE TRIGGER trg_probatoria_append_only")
    )
    await admin.execute(text(sql), params)
    await admin.execute(
        text("ALTER TABLE core.usage_loc ENABLE TRIGGER trg_probatoria_append_only")
    )


# --- criterio 1 y 2: -500 sobre 1.200 deja 700 y recalcula el umbral ---------------
@pytest.mark.parametrize(
    ("limite", "umbral_antes", "umbral_despues"),
    [
        (700, "agotado", "agotado"),  # 700 >= 700
        (800, "agotado", "aviso_80"),  # 700 >= 640
        (1500, "aviso_80", "normal"),  # 1.200 >= 1.200 y luego 700 < 1.200
    ],
)
async def test_un_ajuste_negativo_resta_del_agregado_y_recalcula_el_umbral(
    limite: int, umbral_antes: str, umbral_despues: str
) -> None:
    tn = await _crear_tenant(limite)
    try:
        v10_antes = await _problemas("V10_cuota_cuadra")
        async with db.sesion_tenant(tn.id) as s:
            for netas in (400, 400, 400):
                await _pr(s, netas)
        assert await _ciclo(tn.id) == (1200, 1200, umbral_antes)

        async with db.sesion_tenant(tn.id) as s:
            await _ajuste(s, -500)
        assert await _ciclo(tn.id) == (700, 700, umbral_despues)
        assert await _problemas("V10_cuota_cuadra") == v10_antes
    finally:
        await _borrar_tenant(tn)


async def test_los_tres_umbrales_se_alcanzan_con_el_mismo_ajuste() -> None:
    """Resumen de la prueba anterior: el ajuste de -500 deja 700 y, según el límite del plan,
    el ciclo termina en agotado, aviso_80 o normal."""
    resultados: set[str] = set()
    for limite in (700, 800, 1500):
        tn = await _crear_tenant(limite)
        try:
            async with db.sesion_tenant(tn.id) as s:
                await _pr(s, 1200)
                await _ajuste(s, -500)
            ciclo = await _ciclo(tn.id)
            assert ciclo is not None
            resultados.add(ciclo[2])
        finally:
            await _borrar_tenant(tn)
    assert resultados == {"agotado", "aviso_80", "normal"}


# --- el piso en cero se aplica solo al total del ciclo -----------------------------
async def test_el_piso_en_cero_se_aplica_al_total_y_no_pierde_credito(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 100)
        await _ajuste(s, -300)
    assert await _ciclo(tn.id) == (0, -200, "normal")  # crédito mayor que lo consumido

    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 50)
    # 50 LOC nuevas se cubren con el crédito que sobraba: el consumo sigue en cero.
    assert await _ciclo(tn.id) == (0, -150, "normal")

    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 400)
    assert await _ciclo(tn.id) == (250, 250, "normal")


async def test_un_insert_de_varias_filas_en_una_sentencia_no_cuenta_doble(
    tenant_1500: Tenant,
) -> None:
    tn = tenant_1500
    async with db.sesion_tenant(tn.id) as s:
        await s.execute(
            text(
                "INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, pr_url, anadidas, netas) "
                "SELECT core.current_tenant(), :c, 'pr', 'https://git/pr/' || g || :sfx, 100, 100 "
                "FROM generate_series(1, 3) g"
            ),
            {"c": CICLO, "sfx": uuid.uuid4().hex},
        )
    assert await _ciclo(tn.id) == (300, 300, "normal")


# --- criterio 3: UPDATE y DELETE dejan usage_cycles cuadrado ------------------------
async def test_un_update_de_netas_deja_el_agregado_cuadrado(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    v10_antes = await _problemas("V10_cuota_cuadra")
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 400)
        await _pr(s, 800)
    assert await _ciclo(tn.id) == (1200, 1200, "aviso_80")

    async with db.sesion_sistema() as admin:
        await _mantenimiento(
            admin,
            "UPDATE core.usage_loc SET anadidas = 300, netas = 300 "
            "WHERE tenant_id = :t AND netas = 800",
            {"t": tn.id},
        )
    assert await _ciclo(tn.id) == (700, 700, "normal")
    assert await _problemas("V10_cuota_cuadra") == v10_antes


async def test_un_update_que_cambia_de_ciclo_mueve_el_consumo(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 300)
        await _pr(s, 200)
    async with db.sesion_sistema() as admin:
        await _mantenimiento(
            admin,
            "UPDATE core.usage_loc SET ciclo = :nuevo WHERE tenant_id = :t AND netas = 200",
            {"t": tn.id, "nuevo": OTRO_CICLO},
        )
    assert await _ciclo(tn.id) == (300, 300, "normal")
    assert await _ciclo(tn.id, OTRO_CICLO) == (200, 200, "normal")


async def test_un_delete_deja_el_agregado_cuadrado(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    v10_antes = await _problemas("V10_cuota_cuadra")
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 700)
        await _pr(s, 500)
    async with db.sesion_sistema() as admin:
        await _mantenimiento(
            admin, "DELETE FROM core.usage_loc WHERE tenant_id = :t AND netas = 500", {"t": tn.id}
        )
    assert await _ciclo(tn.id) == (700, 700, "normal")
    assert await _problemas("V10_cuota_cuadra") == v10_antes


async def test_borrar_un_ajuste_negativo_devuelve_el_consumo(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 1200)
        await _ajuste(s, -500)
    assert await _ciclo(tn.id) == (700, 700, "normal")
    async with db.sesion_sistema() as admin:
        await _mantenimiento(
            admin,
            "DELETE FROM core.usage_loc WHERE tenant_id = :t AND tipo = 'ajuste'",
            {"t": tn.id},
        )
    assert await _ciclo(tn.id) == (1200, 1200, "aviso_80")


async def test_la_cascada_set_null_del_ticket_no_cambia_el_agregado(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    tk = f"tk_{uuid.uuid4().hex[:8]}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text(
                "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) "
                "VALUES (:tk, :t, 'bug', 'T', 'D')"
            ),
            {"tk": tk, "t": tn.id},
        )
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 600, ticket=tk)
    antes = await _ciclo(tn.id)
    v10_antes = await _problemas("V10_cuota_cuadra")

    async with db.sesion_sistema() as admin:
        await admin.execute(text("DELETE FROM core.tickets WHERE id = :tk"), {"tk": tk})
        sin_ticket: int = (
            await admin.execute(
                text(
                    "SELECT count(*) FROM core.usage_loc WHERE tenant_id = :t AND ticket_id IS NULL"
                ),
                {"t": tn.id},
            )
        ).scalar_one()
    assert sin_ticket == 1
    assert await _ciclo(tn.id) == antes == (600, 600, "normal")
    assert await _problemas("V10_cuota_cuadra") == v10_antes


# --- V10 detecta la desincronización y V22 el consumo negativo -----------------------
async def test_v10_detecta_un_agregado_desincronizado(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 400)
    antes = await _problemas("V10_cuota_cuadra")

    with pytest.raises(_Revertir):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text(
                    "UPDATE core.usage_cycles SET loc_consumidas = 999, loc_netas = 999 "
                    "WHERE tenant_id = :t"
                ),
                {"t": tn.id},
            )
            despues: int = (
                await admin.execute(
                    text(
                        "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V10_cuota_cuadra'"
                    )
                )
            ).scalar_one()
            assert despues == antes + 1
            raise _Revertir


async def test_v22_devuelve_problemas_si_un_ciclo_tiene_consumo_negativo(
    tenant_1500: Tenant,
) -> None:
    tn = tenant_1500
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 400)
    antes = await _problemas("V22_consumo_no_negativo")
    assert antes == 0

    with pytest.raises(_Revertir):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text("UPDATE core.usage_cycles SET loc_consumidas = -5 WHERE tenant_id = :t"),
                {"t": tn.id},
            )
            despues: int = (
                await admin.execute(
                    text(
                        "SELECT problemas FROM core.validar_integridad() "
                        "WHERE chequeo = 'V22_consumo_no_negativo'"
                    )
                )
            ).scalar_one()
            assert despues == antes + 1
            raise _Revertir


async def test_con_ajustes_que_superan_el_consumo_v22_sigue_en_cero(tenant_1500: Tenant) -> None:
    tn = tenant_1500
    v10_antes = await _problemas("V10_cuota_cuadra")
    async with db.sesion_tenant(tn.id) as s:
        await _pr(s, 100)
        await _ajuste(s, -300)
    assert await _problemas("V22_consumo_no_negativo") == 0
    assert await _problemas("V10_cuota_cuadra") == v10_antes
