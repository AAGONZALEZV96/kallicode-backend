"""CHECKs de dominio y cotas de longitud (KC-DB-09-2, DB-F25).

Por cada restricción que añade la migración 052 se intenta escribir un valor inválido y se espera que
PostgreSQL lo rechace con CheckViolation **nombrando esa restricción**; y se comprueba que el valor límite
válido sí entra (para que el rechazo no sea de otra restricción). Se usa un superusuario con
session_replication_role = replica (sin claves foráneas ni triggers: solo cuentan los CHECK) dentro de una
transacción que se deshace. También se comprueba la nomenclatura, que las restricciones estén validadas, que la
lista de etapas terminales coincida con core.pipeline_stages y que la migración liste las filas antiguas que
no cumplen y deje la restricción sin validar en lugar de fallar.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
MIGRACION = RAIZ / "db" / "migrations" / "052_checks_dominio.sql"
AHORA = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)

# Filas mínimas válidas por tabla (solo columnas sin valor por defecto).
BASE: dict[str, dict[str, Any]] = {
    "specs": {
        "id": "sp_t",
        "tenant_id": "t_x",
        "ticket_id": "tk_t",
        "version": 1,
        "spec_md_ref": "r",
        "scope_paths": ["a"],
    },
    "qa_runs": {
        "id": "qr_t",
        "tenant_id": "t_x",
        "ticket_id": "tk_t",
        "spec_id": "sp_t",
        "corrida": 1,
        "regresion": Jsonb({}),
    },
    "production_lines": {"tenant_id": "t_x", "numero": 1},
    "ticket_counters": {"tenant_id": "t_x"},
    "subscriptions": {
        "id": "sub_t",
        "tenant_id": "t_x",
        "plan_codigo": "p",
        "inicia_el": dt.date(2026, 1, 10),
        "renueva_el": dt.date(2026, 2, 10),
    },
    "tickets": {"id": "tk_t", "tenant_id": "t_x", "tipo": "bug", "titulo": "t", "descripcion": "d"},
    "spec_alternatives": {
        "spec_id": "sp_t",
        "tenant_id": "t_x",
        "alt_id": "a1",
        "titulo": "t",
        "descripcion": "d",
        "riesgo": "bajo",
    },
    "security_findings": {
        "id": "sf_t",
        "tenant_id": "t_x",
        "ticket_id": "tk_t",
        "origen": "sast",
        "severidad": "alta",
        "titulo": "t",
        "veredicto": "confirmado",
    },
    "notifications": {
        "id": "nt_t",
        "tenant_id": "t_x",
        "user_id": "u_t",
        "tipo": "sistema",
        "titulo": "t",
    },
    "gate_signatures": {
        "id": "gs_t",
        "tenant_id": "t_x",
        "ticket_id": "tk_t",
        "gate": 1,
        "accion": "aprobado",
        "actor_id": "u_t",
        "sello": "s",
    },
    "upgrade_requests": {
        "id": "up_t",
        "tenant_id": "t_x",
        "plan_deseado": "p",
        "solicitado_por": "u_t",
    },
    "test_quarantine": {
        "id": "tq_t",
        "tenant_id": "t_x",
        "test_node_id": "n",
        "motivo": "m",
        "cuarentenado_por": "u_t",
    },
}

TERMINALES = ("produccion", "cancelado", "cerrado_duplicado")

# (restricción, tabla, columnas que se cambian en la fila válida para hacerla inválida, columnas del caso válido límite)
INVALIDOS: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = [
    ("spec_version_pos_chk", "specs", {"version": 0}, {"version": 1}),
    ("spec_version_pos_chk", "specs", {"version": -3}, {"version": 1}),
    ("qa_corrida_pos_chk", "qa_runs", {"corrida": 0}, {"corrida": 1}),
    ("qa_corrida_pos_chk", "qa_runs", {"corrida": -1}, {"corrida": 1}),
    ("lines_numero_pos_chk", "production_lines", {"numero": 0}, {"numero": 1}),
    ("lines_numero_pos_chk", "production_lines", {"numero": -2}, {"numero": 1}),
    ("tc_ultimo_pos_chk", "ticket_counters", {"ultimo": -1}, {"ultimo": 0}),
    # fechas de suscripción: finaliza_el no puede ser anterior al inicio (sub_fechas_chk ya cubría renueva_el)
    (
        "sub_finaliza_chk",
        "subscriptions",
        {"finaliza_el": dt.date(2026, 1, 9)},
        {"finaliza_el": dt.date(2026, 1, 10)},
    ),
    (
        "sub_fechas_chk",
        "subscriptions",
        {"renueva_el": dt.date(2026, 1, 10)},
        {"renueva_el": dt.date(2026, 1, 11)},
    ),
    # coherencia de etapas del ticket
    (
        "tk_cancel_por_chk",
        "tickets",
        {"etapa": "cancelado", "motivo_cancelacion": "m", "cerrado_en": AHORA},
        {
            "etapa": "cancelado",
            "motivo_cancelacion": "m",
            "cerrado_en": AHORA,
            "cancelado_por": "u_t",
        },
    ),
    (
        "tk_cancel_chk",
        "tickets",
        {"etapa": "cancelado", "cancelado_por": "u_t", "cerrado_en": AHORA},
        {
            "etapa": "cancelado",
            "cancelado_por": "u_t",
            "cerrado_en": AHORA,
            "motivo_cancelacion": "m",
        },
    ),
    *[
        (
            "tk_cerrado_chk",
            "tickets",
            {"etapa": e, **extra},
            {"etapa": e, "cerrado_en": AHORA, **extra},
        )
        for e, extra in (
            ("produccion", {}),
            ("cancelado", {"motivo_cancelacion": "m", "cancelado_por": "u_t"}),
            ("cerrado_duplicado", {}),
        )
    ],
    # cotas de longitud: 200 para títulos, 20.000 para descripciones, comentarios y motivos
    ("tk_titulo_len_chk", "tickets", {"titulo": "x" * 201}, {"titulo": "x" * 200}),
    (
        "tk_descripcion_len_chk",
        "tickets",
        {"descripcion": "x" * 20001},
        {"descripcion": "x" * 20000},
    ),
    (
        "tk_motivo_cancel_len_chk",
        "tickets",
        {"motivo_cancelacion": "x" * 20001},
        {"motivo_cancelacion": "x" * 20000},
    ),
    ("alt_titulo_len_chk", "spec_alternatives", {"titulo": "x" * 201}, {"titulo": "x" * 200}),
    (
        "alt_descripcion_len_chk",
        "spec_alternatives",
        {"descripcion": "x" * 20001},
        {"descripcion": "x" * 20000},
    ),
    ("sf_titulo_len_chk", "security_findings", {"titulo": "x" * 201}, {"titulo": "x" * 200}),
    ("nt_titulo_len_chk", "notifications", {"titulo": "x" * 201}, {"titulo": "x" * 200}),
    (
        "gs_comentario_len_chk",
        "gate_signatures",
        {"comentario": "x" * 20001},
        {"comentario": "x" * 20000},
    ),
    (
        "upg_comentario_len_chk",
        "upgrade_requests",
        {"comentario": "x" * 20001},
        {"comentario": "x" * 20000},
    ),
    ("tq_motivo_len_chk", "test_quarantine", {"motivo": "x" * 20001}, {"motivo": "x" * 20000}),
]

# restricciones nuevas de la migración 052 y tabla donde viven (para nomenclatura y validación)
NUEVAS = sorted(
    {(c, t) for c, t, _, _ in INVALIDOS if c not in {"sub_fechas_chk", "tk_cancel_chk"}}
)


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SET LOCAL session_replication_role = replica")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _insertar(c: psycopg.Connection, tabla: str, cambios: dict[str, Any]) -> None:
    valores = {**BASE[tabla], **cambios}
    c.execute(
        sql.SQL("INSERT INTO core.{} ({}) VALUES ({})").format(
            sql.Identifier(tabla),
            sql.SQL(", ").join(sql.Identifier(k) for k in valores),
            sql.SQL(", ").join(sql.Placeholder() for _ in valores),
        ),
        list(valores.values()),
    )


def _id(caso: tuple[str, str, dict[str, Any], dict[str, Any]]) -> str:
    restriccion, tabla, malo, _ = caso
    return f"{restriccion}[{tabla}:{','.join(f'{k}={str(v)[:12]}' for k, v in malo.items())}]"


@pytest.mark.parametrize("caso", INVALIDOS, ids=[_id(c) for c in INVALIDOS])
def test_un_valor_invalido_es_rechazado_por_su_restriccion(
    admin: psycopg.Connection, caso: tuple[str, str, dict[str, Any], dict[str, Any]]
) -> None:
    restriccion, tabla, malo, bueno = caso
    admin.execute("SAVEPOINT ok")
    _insertar(admin, tabla, bueno)  # el caso límite válido entra
    admin.execute("ROLLBACK TO SAVEPOINT ok")
    with pytest.raises(errors.CheckViolation) as exc:
        _insertar(admin, tabla, malo)
    assert exc.value.diag.constraint_name == restriccion, exc.value


@pytest.mark.parametrize(("restriccion", "tabla"), NUEVAS)
def test_las_restricciones_nuevas_existen_validadas_y_con_nombre_conforme(
    admin: psycopg.Connection, restriccion: str, tabla: str
) -> None:
    fila = admin.execute(
        "SELECT convalidated FROM pg_constraint WHERE conrelid = %s::regclass AND conname = %s AND contype = 'c'",
        (f"core.{tabla}", restriccion),
    ).fetchone()
    assert fila is not None, f"falta {restriccion} en core.{tabla}"
    assert fila[0] is True, f"{restriccion} quedó sin validar"
    assert re.fullmatch(
        r"[a-z]+(_[a-z]+)*_(pos|len)_chk|tk_cancel_por_chk|tk_cerrado_chk|sub_finaliza_chk",
        restriccion,
    ), f"{restriccion} no sigue la convención <prefijo>_<columna>_(pos|len)_chk"


def test_no_queda_ninguna_restriccion_de_dominio_sin_validar(admin: psycopg.Connection) -> None:
    assert admin.execute("SELECT count(*) FROM core.v_checks_dominio_sin_validar").fetchone() == (
        0,
    )


def test_las_etapas_terminales_del_check_coinciden_con_pipeline_stages(
    admin: psycopg.Connection,
) -> None:
    filas = admin.execute(
        "SELECT codigo FROM core.pipeline_stages WHERE es_terminal ORDER BY codigo"
    ).fetchall()
    assert tuple(sorted(r[0] for r in filas)) == tuple(sorted(TERMINALES))


def test_una_cota_de_longitud_alcanza_a_las_particiones_de_notificaciones(
    admin: psycopg.Connection,
) -> None:
    hijas = admin.execute(
        "SELECT count(*) FROM pg_constraint WHERE conname = 'nt_titulo_len_chk' AND NOT conislocal"
    ).fetchone()
    assert hijas is not None and hijas[0] >= 1


# --- datos antiguos: la migración los lista y deja la restricción sin validar ---------------------------------
def test_la_migracion_lista_las_filas_que_no_cumplen_y_no_falla(admin: psycopg.Connection) -> None:
    """Con una fila antigua inválida, la migración avisa con su identificador y deja la restricción NOT VALID."""
    avisos: list[str] = []
    admin.add_notice_handler(lambda d: avisos.append(d.message_primary or ""))
    admin.execute("ALTER TABLE core.tickets DROP CONSTRAINT tk_titulo_len_chk")
    _insertar(admin, "tickets", {"id": "tk_antiguo", "titulo": "x" * 500})
    admin.execute(
        MIGRACION.read_text(encoding="utf-8")
        .replace("SET LOCAL ROLE kallicode_admin;", "")
        .encode()
    )  # type: ignore[arg-type]
    assert any("tk_titulo_len_chk" in a and "tk_antiguo" in a for a in avisos), avisos
    fila = admin.execute(
        "SELECT convalidated FROM pg_constraint WHERE conrelid = 'core.tickets'::regclass AND conname = 'tk_titulo_len_chk'"
    ).fetchone()
    assert fila == (False,), "debía quedar sin validar"
    pendientes = admin.execute(
        "SELECT restriccion FROM core.v_checks_dominio_sin_validar WHERE tabla = 'core.tickets'"
    ).fetchall()
    assert ("tk_titulo_len_chk",) in pendientes
    # y rige para lo nuevo aunque no esté validada
    admin.execute("SAVEPOINT s")
    with pytest.raises(errors.CheckViolation):
        _insertar(admin, "tickets", {"id": "tk_nuevo", "titulo": "x" * 201})
    admin.execute("ROLLBACK TO SAVEPOINT s")
