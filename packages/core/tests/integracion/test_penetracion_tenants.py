"""Prueba de penetración de datos: seis vías de acceso a otro tenant (KC-DB-12-3), criterio 2.

Con la sesión de la aplicación fijada al tenant A (y también con el worker, donde aplica), se intenta llegar a los datos del
tenant B por seis vías, y TODAS deben fallar:

    1. consulta directa          SELECT / UPDATE / DELETE / INSERT sobre tablas con datos del tenant B
    2. partición hija            consulta directa a la partición física (no hereda la política del padre)
    3. función                   funciones ejecutables por la aplicación que reciben un tenant o lo manejan
    4. vista                     vistas legibles por la aplicación (una vista dueña de admin se salta la RLS)
    5. clave ajena               una fila del tenant A que apunta a una fila del tenant B
    6. búsqueda vectorial        búsqueda por similitud donde lo más cercano es del tenant B

Cada prueba incluye un CONTROL POSITIVO: comprueba que los datos del tenant B existen y que su dueño sí los ve, para que
«no ve nada» signifique «RLS funciona» y no «la tabla está vacía». Los datos se crean con la sesión administrativa y se
borran al terminar (aunque falle la prueba).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

DIM = 1024
ESQUEMAS = ("core", "audit", "vec", "cm")


def _normalizar(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _dsn(variable: str) -> str:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")
    return _normalizar(url)


def _vec(*pos: int) -> str:
    """Vector de 1024 dimensiones con un 1 en las posiciones dadas (texto de pgvector)."""
    v = [0.0] * DIM
    for p in pos:
        v[p] = 1.0
    return "[" + ",".join(str(x) for x in v) + "]"


@dataclass
class Datos:
    a: str
    b: str
    usuario_a: str
    usuario_b: str
    nodo_b: str
    nodo_a: str
    consulta: str  # el vector que es idéntico a los del tenant B


@contextmanager
def como(rol: str, tenant: str | None) -> Iterator[psycopg.Connection]:
    """Sesión de la aplicación (o del worker) con el tenant fijado como lo hace el código: set_config local."""
    c = psycopg.connect(
        _dsn("KC_DATABASE_URL" if rol == "app" else "KC_DATABASE_URL_WORKER"), autocommit=False
    )
    try:
        if tenant is not None:
            c.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        yield c
    finally:
        c.rollback()
        c.close()


def _n(c: psycopg.Connection, sql: str, *params: Any) -> int:
    fila = c.execute(sql, params).fetchone()  # type: ignore[call-overload]
    assert fila is not None
    return int(fila[0])


@pytest.fixture(scope="module")
def datos() -> Iterator[Datos]:
    try:
        adm = psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    if adm.execute("SELECT to_regproc('core.informe_aislamiento')").fetchone() == (None,):
        adm.close()
        pytest.skip("migración 069 sin aplicar")
    s = uuid.uuid4().hex[:8]
    d = Datos(
        f"org_pen_a_{s}",
        f"org_pen_b_{s}",
        f"u_pen_a_{s}",
        f"u_pen_b_{s}",
        f"nd_b_{s}",
        f"nd_a_{s}",
        _vec(0),
    )
    try:
        for t, u in ((d.a, d.usuario_a), (d.b, d.usuario_b)):
            adm.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
            adm.execute(
                "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
                (u, t, f"{u}@pen.dev"),
            )
            adm.execute(
                "INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, status_code) VALUES (%s, %s, 'ep.pen', '{}', 200)",
                (f"k_{t}", t),
            )
        # datos que se insertan sin claves foráneas (replica): vectores y grafo mínimo
        adm.execute("SET session_replication_role = replica")
        for i in range(
            20
        ):  # el tenant B tiene 20 vectores IDÉNTICOS a la consulta: lo más cercano es suyo
            adm.execute(
                "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash) "
                "VALUES (%s, %s, %s, 'symbol', 'n', 'd', %s::vector, 'h')",
                (f"de_b_{s}_{i}", d.b, f"nd_b_{s}_{i}", d.consulta),
            )
            adm.execute(
                "INSERT INTO vec.doc_chunks (id, tenant_id, document_id, chunk_index, content, embedding, texto_hash) VALUES (%s, %s, 'doc_b', %s, 'x', %s::vector, 'h')",
                (f"ch_b_{s}_{i}", d.b, i, d.consulta),
            )
            adm.execute(
                "INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding, texto_hash) VALUES (%s, %s, %s::vector, 'h')",
                (f"tk_b_{s}_{i}", d.b, d.consulta),
            )
        for i in range(1, 6):  # el tenant A tiene 5, lejos de la consulta
            adm.execute(
                "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash) "
                "VALUES (%s, %s, %s, 'symbol', 'n', 'd', %s::vector, 'h')",
                (f"de_a_{s}_{i}", d.a, f"nd_a_{s}_{i}", _vec(i)),
            )
            adm.execute(
                "INSERT INTO vec.doc_chunks (id, tenant_id, document_id, chunk_index, content, embedding, texto_hash) VALUES (%s, %s, 'doc_a', %s, 'x', %s::vector, 'h')",
                (f"ch_a_{s}_{i}", d.a, i, _vec(i)),
            )
            adm.execute(
                "INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding, texto_hash) VALUES (%s, %s, %s::vector, 'h')",
                (f"tk_a_{s}_{i}", d.a, _vec(i)),
            )
        # grafo del tenant B: s_b2 llama a nd_b (para que cm.impacto(nd_b) devuelva a s_b2)
        for nid, kind in ((d.nodo_b, "symbol"), (f"s_b2_{s}", "symbol")):
            adm.execute(
                "INSERT INTO cm.nodes (tenant_id, id, kind) VALUES (%s, %s, %s)", (d.b, nid, kind)
            )
        adm.execute(
            "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id) VALUES (%s, 'CALLS', 'symbol', %s, 'symbol', %s)",
            (d.b, f"s_b2_{s}", d.nodo_b),
        )
        adm.execute("SET session_replication_role = origin")
        yield d
    finally:
        adm.execute("SET session_replication_role = replica")
        for t in (d.a, d.b):
            for tabla in (
                "vec.definition_embeddings",
                "vec.doc_chunks",
                "vec.ticket_embeddings",
                "cm.edges",
                "cm.nodes",
                "core.notifications",
                "core.idempotency_keys",
                "core.users",
            ):
                adm.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))  # type: ignore[call-overload]
            adm.execute("DELETE FROM core.organizations WHERE id = %s", (t,))
        adm.execute("SET session_replication_role = origin")
        adm.close()


# ---------------------------------------------------------------------------------------------------------
# Vía 1 · consulta directa
# ---------------------------------------------------------------------------------------------------------
def test_via1_la_consulta_directa_no_alcanza_al_otro_tenant(datos: Datos) -> None:
    # control positivo: los datos del tenant B existen y su dueño los ve
    with como("app", datos.b) as c:
        assert _n(c, "SELECT count(*) FROM core.users WHERE id = %s", datos.usuario_b) == 1
    with como("app", datos.a) as c:
        assert _n(c, "SELECT count(*) FROM core.users WHERE id = %s", datos.usuario_a) == 1
        # …y desde el tenant A, nada del B, ni por filtro explícito ni sin filtro
        assert _n(c, "SELECT count(*) FROM core.users WHERE tenant_id = %s", datos.b) == 0
        assert _n(c, "SELECT count(*) FROM core.users WHERE id = %s", datos.usuario_b) == 0
        assert {
            f[0]
            for f in c.execute(
                "SELECT DISTINCT tenant_id FROM core.users WHERE tenant_id IN (%s, %s)",
                (datos.a, datos.b),
            ).fetchall()
        } == {datos.a}
        assert _n(c, "SELECT count(*) FROM core.organizations WHERE id = %s", datos.b) == 0
        # no puede modificar ni borrar lo del otro tenant (0 filas afectadas)
        assert (
            c.execute(
                "UPDATE core.users SET nombre = 'hackeado' WHERE tenant_id = %s", (datos.b,)
            ).rowcount
            == 0
        )
        assert c.execute("DELETE FROM core.users WHERE tenant_id = %s", (datos.b,)).rowcount == 0
    with (
        como("app", datos.a) as c,
        pytest.raises(errors.InsufficientPrivilege),
    ):  # ni crear filas a nombre del otro tenant
        c.execute(
            "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES ('x_pen', %s, 'x@pen.dev', 'X')",
            (datos.b,),
        )
    # ni moviendo una fila propia al otro tenant
    with como("app", datos.a) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute("UPDATE core.users SET tenant_id = %s WHERE id = %s", (datos.b, datos.usuario_a))
    # control: el dato del tenant B sigue intacto
    with como("app", datos.b) as c:
        assert (
            _n(c, "SELECT count(*) FROM core.users WHERE id = %s AND nombre = 'U'", datos.usuario_b)
            == 1
        )


def test_via1_sin_tenant_en_la_sesion_no_se_ve_nada(datos: Datos) -> None:
    with como("app", None) as c:
        assert _n(c, "SELECT count(*) FROM core.users") == 0
        assert _n(c, "SELECT count(*) FROM core.idempotency_keys") == 0
    with como("app", "") as c:  # ni con el tenant vacío
        assert _n(c, "SELECT count(*) FROM core.users") == 0


def test_via1_el_worker_ve_todos_los_tenants_solo_en_las_colas_y_nada_en_el_resto(
    datos: Datos,
) -> None:
    with como(
        "worker", None
    ) as c:  # sin tenant: la política worker_colas lo deja ver todo, a propósito, solo ahí
        assert (
            _n(
                c,
                "SELECT count(*) FROM core.idempotency_keys WHERE tenant_id IN (%s, %s)",
                datos.a,
                datos.b,
            )
            == 2
        )
        for tabla in (
            "core.users",
            "core.tickets",
            "core.subscriptions",
        ):  # fuera de las colas: sin privilegios
            with pytest.raises(errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {tabla}")  # type: ignore[call-overload]
            c.rollback()


# ---------------------------------------------------------------------------------------------------------
# Vía 2 · partición hija
# ---------------------------------------------------------------------------------------------------------
def test_via2_la_particion_hija_no_se_puede_consultar_directamente(datos: Datos) -> None:
    adm = psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=True)
    hijas = [
        r[0]
        for r in adm.execute(
            "SELECT c.oid::regclass::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relispartition AND c.relkind IN ('r', 'p') AND n.nspname IN ('core', 'audit', 'vec', 'cm') ORDER BY 1"
        ).fetchall()
    ]
    # control positivo: la partición DEFAULT de los embeddings contiene datos del tenant B (la fuga real que se corrigió)
    assert adm.execute(
        "SELECT count(*) FROM vec.definition_embeddings_default WHERE tenant_id = %s", (datos.b,)
    ).fetchone() == (20,)
    adm.close()
    assert len(hijas) > 20 and "vec.definition_embeddings_default" in hijas
    for rol in ("app", "worker"):
        with como(rol, datos.a) as c:
            for hija in hijas:
                with pytest.raises(errors.InsufficientPrivilege):
                    c.execute(f"SELECT 1 FROM {hija} LIMIT 1")  # type: ignore[call-overload]
                c.rollback()
                c.execute("SELECT set_config('app.tenant_id', %s, true)", (datos.a,))
    # y a través del padre solo ve lo suyo (control: sí hay acceso por la vía correcta)
    with como("app", datos.a) as c:
        assert {
            f[0]
            for f in c.execute(
                "SELECT DISTINCT tenant_id FROM vec.definition_embeddings WHERE tenant_id IN (%s, %s)",
                (datos.a, datos.b),
            ).fetchall()
        } == {datos.a}


# ---------------------------------------------------------------------------------------------------------
# Vía 3 · función
# ---------------------------------------------------------------------------------------------------------
def test_via3_cm_impacto_no_devuelve_el_grafo_de_otro_tenant(datos: Datos) -> None:
    with como("app", datos.b) as c:  # control: el dueño sí obtiene el impacto
        filas = c.execute(
            "SELECT id FROM cm.impacto(p_nodos => ARRAY[%s], p_k => 3, p_direccion => 'entrante', p_ruta => 'recursiva')",
            (datos.nodo_b,),
        ).fetchall()
        assert len(filas) >= 1
    with como("app", datos.a) as c:
        assert (
            c.execute(
                "SELECT id FROM cm.impacto(p_nodos => ARRAY[%s], p_k => 3, p_direccion => 'entrante', p_ruta => 'recursiva')",
                (datos.nodo_b,),
            ).fetchall()
            == []
        )


def test_via3_una_funcion_con_el_tenant_como_parametro_no_cambia_de_tenant(datos: Datos) -> None:
    """core.obtener_ciclo_cuota_actual(<tenant>) lee la suscripción del tenant que se le pasa: con la sesión de A y el id de B,
    no debe ver la suscripción de B (el resultado es el de un tenant sin suscripción)."""
    with como("app", datos.a) as c:
        de_b = c.execute(
            "SELECT core.obtener_ciclo_cuota_actual(%s, DATE '2026-03-31')", (datos.b,)
        ).fetchone()
        inexistente = c.execute(
            "SELECT core.obtener_ciclo_cuota_actual('org_que_no_existe', DATE '2026-03-31')"
        ).fetchone()
        assert de_b == inexistente


def test_via3_las_funciones_security_definer_visibles_son_exactamente_las_revisadas(
    datos: Datos,
) -> None:
    """Una función SECURITY DEFINER corre como kallicode_admin (BYPASSRLS): es la vía más peligrosa. Si aparece una nueva
    ejecutable por la aplicación o el worker, esta prueba falla hasta que alguien la revise y la añada aquí."""
    revisadas = {
        "audit.chain_heads_guard()", "audit.seal_event()",  # funciones de disparador: solo se ejecutan como disparador
        "cm.analizar_tablas(text[])", "core.contar_tablas_sin_retencion()", "core.version_esquema()", "vec.estado_hnsw()",
        "vec.vigencia_global(text,numeric)", "core.purgar_idempotency_keys(timestamp with time zone,interval)",
    }  # fmt: skip
    with psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=True) as adm:
        reales = {
            r[0]
            for r in adm.execute(
                "SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.prosecdef AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND ("
                " has_function_privilege('kallicode_app', p.oid, 'EXECUTE') OR has_function_privilege('kallicode_worker', p.oid, 'EXECUTE'))"
            ).fetchall()
        }
    assert reales <= revisadas, (
        f"funciones SECURITY DEFINER nuevas sin revisar: {sorted(reales - revisadas)}"
    )


def test_via3_las_funciones_security_definer_no_devuelven_datos_de_tenants(datos: Datos) -> None:
    with como("app", datos.a) as c:
        for sql in (
            "SELECT * FROM vec.vigencia_global('symbol', 0.99)",
            "SELECT * FROM vec.estado_hnsw()",
            "SELECT * FROM core.version_esquema()",
        ):
            cur = c.execute(sql)  # type: ignore[call-overload]
            columnas = [d.name for d in cur.description]
            # «tenants» y «tenants_bajo_umbral» son conteos agregados; lo que no puede haber es un identificador de tenant
            assert not {"tenant_id", "tenant", "org_id", "organizacion"} & set(columnas), (
                f"{sql}: columnas {columnas}"
            )
            texto = str(cur.fetchall())
            assert datos.b not in texto and datos.a not in texto


# ---------------------------------------------------------------------------------------------------------
# Vía 4 · vista
# ---------------------------------------------------------------------------------------------------------
def test_via4_ninguna_vista_legible_por_la_aplicacion_se_salta_la_rls(datos: Datos) -> None:
    """Una vista corre con los privilegios de su dueño (kallicode_admin, BYPASSRLS) salvo que sea security_invoker: si lee una
    tabla con tenant_id, enseñaría a todos los tenants. Las vistas legibles deben leer solo catálogos sin tenant_id."""
    with psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=True) as adm:
        vistas = adm.execute(
            "SELECT c.oid, c.oid::regclass::text, coalesce(c.reloptions::text, '') FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('v', 'm') AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND ("
            " has_table_privilege('kallicode_app', c.oid, 'SELECT') OR has_table_privilege('kallicode_worker', c.oid, 'SELECT'))"
        ).fetchall()
        assert vistas, "debería haber al menos la vista del catálogo del grafo (control positivo)"
        for oid, nombre, opciones in vistas:
            invoker = "security_invoker=true" in opciones
            lee_tenant = adm.execute(
                "SELECT count(*) FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid JOIN pg_class t ON t.oid = d.refobjid "
                "WHERE r.ev_class = %s AND d.refobjid <> %s AND t.relkind IN ('r', 'p', 'v', 'm') "
                "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = t.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)",
                (oid, oid),
            ).fetchone()[0]
            assert invoker or lee_tenant == 0, (
                f"{nombre}: dueña de admin (salta la RLS) y lee {lee_tenant} tabla(s) con tenant_id"
            )
    with como("app", datos.a) as c:
        a = c.execute("SELECT count(*) FROM cm.v_catalogo_grafo").fetchone()
    with como("app", datos.b) as c:
        assert (
            c.execute("SELECT count(*) FROM cm.v_catalogo_grafo").fetchone() == a
        )  # el catálogo es el mismo para todos


# ---------------------------------------------------------------------------------------------------------
# Vía 5 · clave ajena
# ---------------------------------------------------------------------------------------------------------
def test_via5_no_se_puede_apuntar_a_una_fila_de_otro_tenant_con_una_clave_ajena(
    datos: Datos,
) -> None:
    # control: con su propio usuario, la fila se crea
    with como("app", datos.a) as c:
        c.execute(
            "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo) VALUES (%s, %s, %s, 'sistema', 'ok')",
            (f"nt_a_{datos.a}", datos.a, datos.usuario_a),
        )
    # apuntando al usuario del tenant B: la clave ajena es COMPUESTA con tenant_id, así que (A, usuario de B) no existe
    with como("app", datos.a) as c, pytest.raises(errors.ForeignKeyViolation):
        c.execute(
            "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo) VALUES (%s, %s, %s, 'sistema', 'x')",
            (f"nt_x_{datos.a}", datos.a, datos.usuario_b),
        )
    with como("app", datos.a) as c:
        assert (
            _n(c, "SELECT count(*) FROM core.notifications WHERE id = %s", f"nt_x_{datos.a}") == 0
        )


def test_via5_toda_clave_ajena_entre_tablas_con_tenant_id_incluye_el_tenant_id() -> None:
    """Prueba de catálogo: sin ninguna excepción, ninguna clave ajena entre tablas con tenant_id puede ser 'simple'."""
    with psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=True) as adm:
        simples = adm.execute(
            "SELECT c.conrelid::regclass::text, c.conname, c.confrelid::regclass::text FROM pg_constraint c "
            "JOIN pg_class o ON o.oid = c.conrelid JOIN pg_namespace n ON n.oid = o.relnamespace "
            "WHERE c.contype = 'f' AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND NOT o.relispartition "
            "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attname = 'tenant_id' AND NOT a.attisdropped) "
            "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.confrelid AND a.attname = 'tenant_id' AND NOT a.attisdropped) "
            "AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) AND a.attname = 'tenant_id')"
        ).fetchall()
        compuestas = adm.execute(
            "SELECT count(*) FROM pg_constraint c JOIN pg_class o ON o.oid = c.conrelid JOIN pg_namespace n ON n.oid = o.relnamespace "
            "WHERE c.contype = 'f' AND n.nspname IN ('core', 'audit', 'vec', 'cm') AND NOT o.relispartition "
            "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) AND a.attname = 'tenant_id') "
            "AND cardinality(c.conkey) > 1"
        ).fetchone()[0]
    assert compuestas >= 20, (
        "control positivo: debe haber muchas claves ajenas compuestas con tenant_id"
    )
    assert simples == [], f"claves ajenas simples entre tablas con tenant_id: {simples}"


# ---------------------------------------------------------------------------------------------------------
# Vía 6 · búsqueda vectorial
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tabla", "columna"),
    [
        ("vec.definition_embeddings", "id"),
        ("vec.doc_chunks", "id"),
        ("vec.ticket_embeddings", "ticket_id"),
    ],
)
@pytest.mark.parametrize("forzar_indice", [False, True])
def test_via6_la_busqueda_vectorial_no_devuelve_vectores_de_otro_tenant(
    datos: Datos, tabla: str, columna: str, forzar_indice: bool
) -> None:
    sql = f"SELECT {columna}, tenant_id FROM {tabla} ORDER BY embedding <=> %s::vector LIMIT 10"
    # control positivo: el tenant B, que es el dueño de los vectores idénticos a la consulta, los encuentra
    with como("app", datos.b) as c:
        assert {f[1] for f in c.execute(sql, (datos.consulta,)).fetchall()} == {datos.b}
    with como("app", datos.a) as c:
        if forzar_indice:
            c.execute("SET LOCAL enable_seqscan = off")  # fuerza el recorrido del índice HNSW
        filas = c.execute(sql, (datos.consulta,)).fetchall()
        assert filas, "el tenant A tiene vectores propios: la búsqueda debe devolver los suyos"
        assert {f[1] for f in filas} == {datos.a}, (
            f"{tabla}: la búsqueda de A devolvió {sorted({f[1] for f in filas})}"
        )
