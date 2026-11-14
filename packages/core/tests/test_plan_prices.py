"""KC-DB-09-3: catálogo de precios vigentes (core.plan_prices, migración 053_catalogo_precios.sql).

- EXCLUDE USING gist: no hay dos tarifas solapadas para el mismo plan, concepto, tier y moneda;
- core.precio_vigente devuelve la tarifa de la fecha pedida (la de un ciclo pasado), no la actual;
- tres tarifas sucesivas: cada ciclo del tenant se valora con la vigente en su fecha de inicio;
- consulta histórica por fecha de consumo y tier LLM (precio por token);
- catálogo global sin tenant_id, RLS activa y forzada con una única política de solo lectura,
  y kallicode_app puede leer pero no escribir;
- cada CHECK nuevo rechaza un valor inválido.

Requiere PostgreSQL real con las migraciones aplicadas; sin BD se omite.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg import errors


def _dsn() -> str:
    url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test"
    )
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _conectar(autocommit: bool = True) -> psycopg.Connection:
    try:
        return psycopg.connect(_dsn(), autocommit=autocommit)
    except psycopg.Error as exc:
        pytest.skip(f"Base de datos no accesible en este entorno: {exc}")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    c = _conectar()
    yield c
    c.close()


@pytest.fixture
def plan(conn: psycopg.Connection) -> Iterator[str]:
    """Plan propio de la prueba: sus tarifas no chocan con las de otros tests ni con datos reales."""
    codigo = f"pp_{uuid.uuid4().hex[:10]}"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.plans (codigo, nombre, lineas, loc_mes) VALUES (%s, 'Plan precios', 2, 5000)",
            (codigo,),
        )
    yield codigo
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.plan_prices WHERE plan_codigo = %s", (codigo,))
        cur.execute("DELETE FROM core.plans WHERE codigo = %s", (codigo,))


def _tarifa(
    conn: psycopg.Connection,
    plan: str,
    importe: str,
    desde: date,
    hasta: date | None = None,
    *,
    concepto: str = "suscripcion",
    tier: str | None = None,
    moneda: str = "EUR",
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.plan_prices
                   (plan_codigo, concepto, tier, moneda, importe, vigente_desde, vigente_hasta)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (plan, concepto, tier, moneda, Decimal(importe), desde, hasta),
        )


def _precio(
    conn: psycopg.Connection,
    plan: str,
    fecha: date,
    *,
    concepto: str = "suscripcion",
    tier: str | None = None,
    moneda: str = "EUR",
) -> Decimal | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT core.precio_vigente(%s, %s, %s, %s, %s)",
            (plan, moneda, fecha, concepto, tier),
        )
        fila = cur.fetchone()
    assert fila is not None
    return fila[0]


def _tres_tarifas(conn: psycopg.Connection, plan: str) -> None:
    """Tres tarifas sucesivas de suscripción: 100 → 120 → 150 (la última abierta)."""
    _tarifa(conn, plan, "100.00", date(2026, 1, 1), date(2026, 4, 1))
    _tarifa(conn, plan, "120.00", date(2026, 4, 1), date(2026, 9, 15))
    _tarifa(conn, plan, "150.00", date(2026, 9, 15))


# --------------------------------------------------------------- EXCLUDE anti-solape
def test_exclude_rechaza_vigencias_solapadas(conn: psycopg.Connection, plan: str) -> None:
    _tarifa(conn, plan, "100.00", date(2026, 1, 1), date(2026, 6, 1))
    with pytest.raises(errors.ExclusionViolation):
        _tarifa(conn, plan, "110.00", date(2026, 5, 1))  # abierta, pisa mayo
    with pytest.raises(errors.ExclusionViolation):
        _tarifa(conn, plan, "90.00", date(2025, 12, 1), date(2026, 1, 2))  # pisa el 1-ene


def test_exclude_admite_tarifas_contiguas_y_otras_claves(
    conn: psycopg.Connection, plan: str
) -> None:
    _tarifa(conn, plan, "100.00", date(2026, 1, 1), date(2026, 6, 1))
    # Contigua: el fin es exclusivo, así que empezar el 1-jun no solapa.
    _tarifa(conn, plan, "120.00", date(2026, 6, 1))
    # Mismo periodo en otra moneda, o en tokens de otros tiers: claves distintas.
    _tarifa(conn, plan, "110.00", date(2026, 1, 1), moneda="USD")
    _tarifa(conn, plan, "0.30", date(2026, 1, 1), concepto="tokens_in", tier="flash")
    _tarifa(conn, plan, "3.00", date(2026, 1, 1), concepto="tokens_in", tier="pro")
    _tarifa(conn, plan, "2.50", date(2026, 1, 1), concepto="tokens_out", tier="flash")
    # Pero dos del mismo tier sí chocan.
    with pytest.raises(errors.ExclusionViolation):
        _tarifa(conn, plan, "0.35", date(2026, 3, 1), concepto="tokens_in", tier="flash")


# --------------------------------------------------------------- tarifa vigente
def test_precio_vigente_devuelve_la_tarifa_de_la_fecha_no_la_actual(
    conn: psycopg.Connection, plan: str
) -> None:
    _tres_tarifas(conn, plan)
    assert _precio(conn, plan, date(2026, 1, 1)) == Decimal("100.00")
    assert _precio(conn, plan, date(2026, 3, 31)) == Decimal("100.00")
    assert _precio(conn, plan, date(2026, 4, 1)) == Decimal("120.00")  # día exacto del cambio
    assert _precio(conn, plan, date(2026, 9, 14)) == Decimal("120.00")
    assert _precio(conn, plan, date(2026, 9, 15)) == Decimal("150.00")
    assert _precio(conn, plan, date(2030, 1, 1)) == Decimal("150.00")  # abierta
    assert _precio(conn, plan, date(2025, 12, 31)) is None  # antes de la primera
    assert _precio(conn, plan, date(2026, 5, 1), moneda="USD") is None  # sin tarifa en USD


def test_tres_tarifas_cada_ciclo_se_valora_con_la_vigente_en_su_fecha(
    conn: psycopg.Connection, plan: str
) -> None:
    """Ciclos de aniversario de un tenant contratado el día 20: cada uno con la tarifa de su inicio."""
    _tres_tarifas(conn, plan)
    tenant = f"org_pp_{uuid.uuid4().hex[:10]}"
    inicia = date(2026, 1, 20)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Tenant precios')", (tenant,)
        )
        try:
            cur.execute(
                """
                INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el)
                VALUES (%s, %s, %s, 'activa', %s, %s)
                """,
                (f"sub_{uuid.uuid4().hex[:10]}", tenant, plan, inicia, date(2026, 11, 20)),
            )
            ciclos = ["2026-02", "2026-03", "2026-04", "2026-09", "2026-10"]
            for ciclo in ciclos:
                cur.execute(
                    "INSERT INTO core.usage_cycles (tenant_id, ciclo) VALUES (%s, %s)",
                    (tenant, ciclo),
                )
            # Fecha de inicio de cada ciclo = aniversario de inicia_el en ese mes (migración 051).
            cur.execute(
                """
                SELECT uc.ciclo,
                       core.precio_vigente(
                           s.plan_codigo, 'EUR',
                           core.aniversario_en(extract(day FROM s.inicia_el)::int,
                                               to_date(uc.ciclo || '-01', 'YYYY-MM-DD')))
                  FROM core.usage_cycles uc
                  JOIN core.subscriptions s ON s.tenant_id = uc.tenant_id
                 WHERE uc.tenant_id = %s
                 ORDER BY uc.ciclo
                """,
                (tenant,),
            )
            valorados = cur.fetchall()
        finally:
            cur.execute("SELECT core.suprimir_tenant(%s, 'test', 'test_plan_prices')", (tenant,))

    assert valorados == [
        ("2026-02", Decimal("100.00")),  # 20-feb → primera tarifa
        ("2026-03", Decimal("100.00")),
        ("2026-04", Decimal("120.00")),  # 20-abr → segunda
        ("2026-09", Decimal("150.00")),  # 20-sep → tercera (cambió el 15-sep)
        ("2026-10", Decimal("150.00")),
    ]


def test_consulta_historica_por_fecha_de_consumo_y_tier(
    conn: psycopg.Connection, plan: str
) -> None:
    """Coste de consumos LLM pasados: cada uno con el precio por token de su tier y su fecha."""
    _tarifa(
        conn, plan, "0.30", date(2026, 1, 1), date(2026, 7, 1), concepto="tokens_in", tier="flash"
    )
    _tarifa(conn, plan, "0.25", date(2026, 7, 1), concepto="tokens_in", tier="flash")
    _tarifa(conn, plan, "3.00", date(2026, 1, 1), concepto="tokens_in", tier="pro")
    _tarifa(conn, plan, "2.40", date(2026, 1, 1), concepto="tokens_out", tier="flash")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.dia, c.tier, c.concepto,
                   core.precio_vigente(%s, 'EUR', c.dia, c.concepto, c.tier) * c.tokens / 1000000
              FROM (VALUES (date '2026-03-10', 'flash', 'tokens_in',  2000000),
                           (date '2026-08-02', 'flash', 'tokens_in',  2000000),
                           (date '2026-08-02', 'pro',   'tokens_in',  1000000),
                           (date '2026-08-02', 'flash', 'tokens_out', 500000),
                           (date '2026-08-02', 'fable', 'tokens_in',  1000000))
                   AS c(dia, tier, concepto, tokens)
             ORDER BY c.dia, c.tier, c.concepto
            """,
            (plan,),
        )
        costes = [(d, t, c, None if v is None else v.normalize()) for d, t, c, v in cur.fetchall()]

    assert costes == [
        (date(2026, 3, 10), "flash", "tokens_in", Decimal("0.6")),  # 0.30 €/M antes de julio
        (date(2026, 8, 2), "fable", "tokens_in", None),  # tier sin tarifa
        (date(2026, 8, 2), "flash", "tokens_in", Decimal("0.5")),  # 0.25 €/M desde julio
        (date(2026, 8, 2), "flash", "tokens_out", Decimal("1.2")),
        (date(2026, 8, 2), "pro", "tokens_in", Decimal(3)),
    ]


# --------------------------------------------------------------- catálogo global y RLS
def test_catalogo_global_sin_tenant_y_con_rls_de_solo_lectura(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM pg_attribute
             WHERE attrelid = 'core.plan_prices'::regclass AND attname = 'tenant_id'
               AND NOT attisdropped
            """
        )
        assert cur.fetchone() == (0,)

        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'core.plan_prices'::regclass"
        )
        assert cur.fetchone() == (True, True)

        cur.execute(
            "SELECT polname, polcmd, pg_get_expr(polqual, polrelid) FROM pg_policy "
            "WHERE polrelid = 'core.plan_prices'::regclass"
        )
        assert cur.fetchall() == [("plan_prices_solo_lectura", "r", "true")]

        cur.execute(
            """
            SELECT p, has_table_privilege('kallicode_app', 'core.plan_prices', p)
              FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) AS p
            """
        )
        assert dict(cur.fetchall()) == {
            "SELECT": True,
            "INSERT": False,
            "UPDATE": False,
            "DELETE": False,
            "TRUNCATE": False,
        }


def _conn_app() -> psycopg.Connection:
    # KC_DATABASE_URL es la URL de la app (kallicode_app en el CI); si no hay, SET ROLE desde el admin.
    app_url = os.getenv("KC_DATABASE_URL_APP") or os.getenv("KC_DATABASE_URL")
    try:
        if app_url:
            return psycopg.connect(_dsn_de(app_url), autocommit=False)
        c = psycopg.connect(_dsn(), autocommit=False)
        with c.cursor() as cur:
            cur.execute("SET ROLE kallicode_app")
        return c
    except psycopg.Error as exc:
        pytest.skip(f"Base de datos no accesible con el rol de la app: {exc}")


def _dsn_de(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def test_kallicode_app_lee_pero_no_escribe(conn: psycopg.Connection, plan: str) -> None:
    _tarifa(conn, plan, "100.00", date(2026, 1, 1))
    app = _conn_app()
    try:
        with app.cursor() as cur:
            # Lectura sin app.tenant_id: el catálogo es global.
            cur.execute("SELECT importe FROM core.plan_prices WHERE plan_codigo = %s", (plan,))
            assert cur.fetchall() == [(Decimal("100.000000"),)]
            cur.execute("SELECT core.precio_vigente(%s, 'EUR', %s)", (plan, date(2026, 2, 1)))
            assert cur.fetchone() == (Decimal("100.000000"),)
        app.rollback()

        sentencias = [
            (
                "INSERT INTO core.plan_prices (plan_codigo, concepto, moneda, importe, vigente_desde) "
                "VALUES (%s, 'suscripcion', 'USD', 1, '2026-01-01')",
                (plan,),
            ),
            ("UPDATE core.plan_prices SET importe = 0 WHERE plan_codigo = %s", (plan,)),
            ("DELETE FROM core.plan_prices WHERE plan_codigo = %s", (plan,)),
        ]
        for sentencia, params in sentencias:
            with app.cursor() as cur, pytest.raises(errors.InsufficientPrivilege):
                cur.execute(sentencia, params)
            app.rollback()
    finally:
        app.close()


# --------------------------------------------------------------- CHECKs
@pytest.mark.parametrize(
    ("campos", "restriccion"),
    [
        ({"importe": "-1"}, "pp_importe_chk"),
        ({"moneda": "eur"}, "pp_moneda_chk"),
        ({"moneda": "EURO"}, "pp_moneda_chk"),
        ({"hasta": date(2026, 1, 1)}, "pp_vigencia_chk"),  # hasta == desde
        # Con tier, para que solo falle pp_concepto_chk (los CHECK se evalúan por orden de nombre).
        ({"concepto": "licencia", "tier": "pro"}, "pp_concepto_chk"),
        ({"tier": "vision", "concepto": "tokens_in"}, "pp_tier_chk"),
        ({"tier": "pro"}, "pp_coherencia_chk"),  # suscripción con tier
        ({"concepto": "tokens_out"}, "pp_coherencia_chk"),  # tokens sin tier
    ],
)
def test_checks_rechazan_valores_invalidos(
    conn: psycopg.Connection, plan: str, campos: dict, restriccion: str
) -> None:
    valores = {
        "importe": "10",
        "desde": date(2026, 1, 1),
        "hasta": None,
        "concepto": "suscripcion",
        "tier": None,
        "moneda": "EUR",
    } | campos
    with pytest.raises(errors.CheckViolation) as exc:
        _tarifa(
            conn,
            plan,
            valores["importe"],
            valores["desde"],
            valores["hasta"],
            concepto=valores["concepto"],
            tier=valores["tier"],
            moneda=valores["moneda"],
        )
    assert exc.value.diag.constraint_name == restriccion
