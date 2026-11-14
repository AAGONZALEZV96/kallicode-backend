"""Chequeos V22-V26 de core.validar_integridad() (KC-DB-09-6).

Por cada una de las cinco condiciones del ticket se PROVOCA la situación (dentro de una transacción que se deshace,
con session_replication_role = replica para no montar todas las claves foráneas) y se comprueba que el chequeo
correspondiente la cuenta: baja de 0 a 1 al provocarla, y vuelve a la línea base al corregirla. También se comprueba
que la función tiene los 26 chequeos, que su COMMENT documenta V22-V26 y que tarda menos de 10 s.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest

pytestmark = pytest.mark.integracion

CHEQUEOS_NUEVOS = (
    "V22_consumo_no_negativo",
    "V23_renovacion_vencida",
    "V24_bloqueantes_sin_adjudicar",
    "V25_org_borrada_con_actividad",
    "V26_tabla_sin_retencion",
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


def _problemas(c: psycopg.Connection, chequeo: str) -> int:
    fila = c.execute(
        "SELECT problemas FROM core.validar_integridad() WHERE chequeo = %s", (chequeo,)
    ).fetchone()
    assert fila is not None, f"la función no devuelve {chequeo}"
    return int(fila[0])


def _sufijo() -> str:
    return uuid.uuid4().hex[:10]


def _organizacion(c: psycopg.Connection, tenant: str, borrada: str | None = None) -> None:
    c.execute(
        "INSERT INTO core.organizations (id, nombre, deleted_at) "
        "VALUES (%s, 'Org V22-V26', CASE WHEN %s::text IS NULL THEN NULL ELSE now() - %s::interval END)",
        (tenant, borrada, borrada),
    )


# --- la función ---------------------------------------------------------------------------------------
def test_la_funcion_tiene_26_chequeos_con_los_nuevos_y_sin_repetidos(
    admin: psycopg.Connection,
) -> None:
    nombres = [
        r[0] for r in admin.execute("SELECT chequeo FROM core.validar_integridad()").fetchall()
    ]
    assert len(nombres) == 26 and len(set(nombres)) == 26
    assert [n[:3] for n in nombres] == [f"V{i:02d}" for i in range(1, 27)]
    for esperado in CHEQUEOS_NUEVOS:
        assert esperado in nombres


def test_el_comment_documenta_v22_a_v26(admin: psycopg.Connection) -> None:
    fila = admin.execute(
        "SELECT obj_description('core.validar_integridad()'::regprocedure)"
    ).fetchone()
    assert fila is not None
    for v in ("V22", "V23", "V24", "V25", "V26"):
        assert v in str(fila[0]), f"el COMMENT de la función no menciona {v}"


def test_la_bateria_completa_tarda_menos_de_diez_segundos(admin: psycopg.Connection) -> None:
    t0 = time.perf_counter()
    admin.execute("SELECT * FROM core.validar_integridad()").fetchall()
    assert time.perf_counter() - t0 < 10


def test_v26_es_cero_en_el_esquema_vigente(admin: psycopg.Connection) -> None:
    """Todas las tablas particionadas existentes tienen su política declarada (catálogo, determinista)."""
    assert _problemas(admin, "V26_tabla_sin_retencion") == 0


# --- cada condición, provocada -------------------------------------------------------------------------
def test_v22_cuenta_un_ciclo_con_consumo_negativo(admin: psycopg.Connection) -> None:
    antes = _problemas(admin, "V22_consumo_no_negativo")
    t = f"org_v22_{_sufijo()}"
    admin.execute(
        "INSERT INTO core.usage_cycles (tenant_id, ciclo, loc_consumidas, loc_netas) VALUES (%s, '2026-11', -5, -5)",
        (t,),
    )
    assert _problemas(admin, "V22_consumo_no_negativo") == antes + 1
    admin.execute(
        "UPDATE core.usage_cycles SET loc_consumidas = 0, loc_netas = 0 WHERE tenant_id = %s", (t,)
    )
    assert _problemas(admin, "V22_consumo_no_negativo") == antes


def test_v23_cuenta_una_suscripcion_activa_con_renovacion_vencida(
    admin: psycopg.Connection,
) -> None:
    antes = _problemas(admin, "V23_renovacion_vencida")
    t = f"org_v23_{_sufijo()}"
    admin.execute(
        "INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el) "
        "VALUES (%s, %s, 'growth', current_date - 60, current_date - 1)",
        (f"sub_{t}", t),
    )
    assert _problemas(admin, "V23_renovacion_vencida") == antes + 1
    admin.execute(
        "UPDATE core.subscriptions SET renueva_el = current_date + 29 WHERE id = %s", (f"sub_{t}",)
    )
    assert _problemas(admin, "V23_renovacion_vencida") == antes


def test_v24_cuenta_un_bloqueante_abierto_de_mas_de_48_horas(admin: psycopg.Connection) -> None:
    antes = _problemas(admin, "V24_bloqueantes_sin_adjudicar")
    t = _sufijo()
    cols = "(id, tenant_id, ticket_id, origen, severidad, titulo, veredicto, bloquea, estado, creado_en)"
    # uno reciente (47 h): no cuenta; uno de 49 h: cuenta
    admin.execute(
        f"INSERT INTO core.security_findings {cols} VALUES "
        "(%s, 't', 'tk', 'sast', 'alta', 't', 'confirmado', true, 'abierto', now() - interval '47 hours')",
        (f"sf_r_{t}",),
    )
    assert _problemas(admin, "V24_bloqueantes_sin_adjudicar") == antes
    admin.execute(
        f"INSERT INTO core.security_findings {cols} VALUES "
        "(%s, 't', 'tk', 'sast', 'alta', 't', 'confirmado', true, 'abierto', now() - interval '49 hours')",
        (f"sf_v_{t}",),
    )
    assert _problemas(admin, "V24_bloqueantes_sin_adjudicar") == antes + 1
    # corregido: deja de contar
    admin.execute(
        "UPDATE core.security_findings SET estado = 'corregido' WHERE id = %s", (f"sf_v_{t}",)
    )
    assert _problemas(admin, "V24_bloqueantes_sin_adjudicar") == antes
    # un hallazgo no bloqueante de 49 h tampoco cuenta
    admin.execute(
        f"INSERT INTO core.security_findings {cols} VALUES "
        "(%s, 't', 'tk', 'sast', 'baja', 't', 'confirmado', false, 'abierto', now() - interval '49 hours')",
        (f"sf_n_{t}",),
    )
    assert _problemas(admin, "V24_bloqueantes_sin_adjudicar") == antes


def test_v25_cuenta_una_organizacion_borrada_con_actividad_posterior(
    admin: psycopg.Connection,
) -> None:
    antes = _problemas(admin, "V25_org_borrada_con_actividad")
    t = f"org_v25_{_sufijo()}"
    _organizacion(admin, t, borrada="1 day")
    # un ticket anterior al borrado no es actividad posterior
    admin.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, creado_en) "
        "VALUES (%s, %s, 'bug', 't', 'd', now() - interval '2 days')",
        (f"tk_a_{t}", t),
    )
    assert _problemas(admin, "V25_org_borrada_con_actividad") == antes
    # un ticket creado después del borrado sí
    admin.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, creado_en) "
        "VALUES (%s, %s, 'bug', 't', 'd', now())",
        (f"tk_b_{t}", t),
    )
    assert _problemas(admin, "V25_org_borrada_con_actividad") == antes + 1
    # una organización viva con actividad no cuenta
    v = f"org_v25v_{_sufijo()}"
    _organizacion(admin, v)
    admin.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) VALUES (%s, %s, 'bug', 't', 'd')",
        (f"tk_v_{v}", v),
    )
    assert _problemas(admin, "V25_org_borrada_con_actividad") == antes + 1


def test_v25_cuenta_tambien_el_borrado_legado_eliminado_en(admin: psycopg.Connection) -> None:
    antes = _problemas(admin, "V25_org_borrada_con_actividad")
    t = f"org_v25l_{_sufijo()}"
    admin.execute(
        "INSERT INTO core.organizations (id, nombre, eliminado_en) VALUES (%s, 'Org legado', now() - interval '1 day')",
        (t,),
    )
    admin.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) VALUES (%s, %s, 'bug', 't', 'd')",
        (f"tk_{t}", t),
    )
    assert _problemas(admin, "V25_org_borrada_con_actividad") == antes + 1


def test_v26_cuenta_una_tabla_particionada_sin_politica_y_se_corrige_declarandola(
    admin: psycopg.Connection,
) -> None:
    antes = _problemas(admin, "V26_tabla_sin_retencion")
    t = f"zz_part_{_sufijo()}"
    admin.execute(f"CREATE TABLE core.{t} (id integer, f date) PARTITION BY RANGE (f)")  # type: ignore[arg-type]
    assert _problemas(admin, "V26_tabla_sin_retencion") == antes + 1
    admin.execute(
        "INSERT INTO core.retencion_declaraciones (tabla, politica, justificacion) VALUES (%s, 'conservar', 'prueba')",
        (f"core.{t}",),
    )
    assert _problemas(admin, "V26_tabla_sin_retencion") == antes
    # o con una ventana en core.retencion_particiones
    admin.execute("DELETE FROM core.retencion_declaraciones WHERE tabla = %s", (f"core.{t}",))
    assert _problemas(admin, "V26_tabla_sin_retencion") == antes + 1
    admin.execute(
        "INSERT INTO core.retencion_particiones (tabla, retencion) VALUES (%s, interval '30 days')",
        (f"core.{t}",),
    )
    assert _problemas(admin, "V26_tabla_sin_retencion") == antes


def test_las_declaraciones_de_retencion_exigen_politica_y_justificacion(
    admin: psycopg.Connection,
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO core.retencion_declaraciones (tabla, politica, justificacion) VALUES ('core.x', 'borrar', 'j')"
        )
    admin.rollback()
    admin.execute("SET LOCAL session_replication_role = replica")
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "INSERT INTO core.retencion_declaraciones (tabla, politica, justificacion) VALUES ('core.x', 'conservar', '  ')"
        )


def test_la_aplicacion_y_housekeeping_pueden_ejecutar_la_bateria_completa(
    admin: psycopg.Connection,
) -> None:
    """La ejecutan housekeeping (kallicode_admin) y la aplicación (kallicode_app): V26 no debe exigir más permisos.
    (El worker no la ejecuta: V01, anterior a este ticket, ya no le deja leer core.organizations.)"""
    for rol in ("kallicode_app", "kallicode_admin"):
        admin.execute(f"SET LOCAL ROLE {rol}")  # type: ignore[arg-type]
        filas = admin.execute("SELECT chequeo FROM core.validar_integridad()").fetchall()
        assert len(filas) == 26, rol
        admin.execute("RESET ROLE")
