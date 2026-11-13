"""Capa de incidentes contra PostgreSQL 16 + pgvector reales (KC-CM-09-2).

Requiere `make up && make db` (migración 047). El registro y las consultas corren como
kallicode_app con el tenant fijado por GraphLoader.fase(); el administrador solo siembra datos,
ajusta fechas y, en la prueba de aislamiento, consulta saltándose RLS para demostrar el filtro
explícito de cada tabla.

Criterios:
- cm.incidents (y las tablas nuevas) con RLS activa y forzada y PK (tenant_id, id).
- Un ticket cerrado con incidente crea su fila y sus enlaces a los nodos afectados desde su
  expediente (título, QA en falla, impacto); registrarlo de nuevo no duplica.
- La consulta por área devuelve los incidentes de los nodos a <= 2 saltos, ordenados por
  distancia, similitud y recencia, y recorta por incidentes enteros.
- Solo incidentes del tenant activo, también sin RLS (superusuario).
- Tenant sin incidentes: {{incidents}} = "" y la plantilla se rellena sin error.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import cm_queries, incidentes
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_core import cm_incidentes
from kallicode_core.cm_incidentes import ConsultaIncidentesIn, RegistroIncidenteIn

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT_A = "org_test_inc_a"
TENANT_B = "org_test_inc_b"
TENANT_VACIO = "org_test_inc_vacio"
TODOS = (TENANT_A, TENANT_B, TENANT_VACIO)
SNAPSHOT = "snap_inc"
AHORA = datetime.now(UTC)
DIM = cm_incidentes.DIMENSION_EMBEDDING

INVOICE = "src/billing/invoice.py"
ORDER = "src/orders/order.py"
LOGIN = "src/auth/login.py"

# Grafo (igual en A y B):
#   cron -> reporte -> crear_pedido -> emitir -> calc_iva     (CALLS)
#   emitir USES_TABLE dbt_facturas ; login USES_TABLE dbt_usuarios
# Desde el área INVOICE (fb, emitir, calc_iva): crear_pedido y dbt_facturas a 1 salto, reporte a 2
# y cron a 3.
FICHEROS = [
    ("fb", INVOICE, ["emitir", "calc_iva"]),
    ("fo", ORDER, ["crear_pedido", "reporte", "cron"]),
    ("fx", LOGIN, ["login"]),
]
TABLAS = [("dbt_facturas", "facturas"), ("dbt_usuarios", "usuarios")]
ARISTAS = [
    ("CALLS", "cron", "reporte"),
    ("CALLS", "reporte", "crear_pedido"),
    ("CALLS", "crear_pedido", "emitir"),
    ("CALLS", "emitir", "calc_iva"),
    ("USES_TABLE", "emitir", "dbt_facturas"),
    ("USES_TABLE", "login", "dbt_usuarios"),
]


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute("SELECT to_regclass('cm.incident_nodes') IS NOT NULL").fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 047 sin aplicar (make up && make db)"
)


def _vector(i: int) -> list[float]:
    """Vector unitario e_i: similitud 1 consigo mismo y 0 con los demás."""
    v = [0.0] * DIM
    v[i] = 1.0
    return v


class _Embebedor:
    """Embebe por palabra clave del texto, para controlar la similitud."""

    def __init__(self, por_clave: dict[str, int]) -> None:
        self.por_clave = por_clave
        self.llamadas = 0

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.llamadas += 1
        resultado = []
        for t in texts:
            indice = next((i for clave, i in self.por_clave.items() if clave in t), DIM - 1)
            resultado.append(_vector(indice))
        return resultado


EMBEBEDOR = _Embebedor({"IVA": 0, "Pedido": 1})


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        for tabla in (
            "vec.incident_embeddings",
            "cm.incident_nodes",
            "cm.incidents",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.db_tables",
            "cm.nodes",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
            "core.qa_results",
            "core.qa_runs",
            "core.specs",
            "core.tickets",
            "core.ticket_counters",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _sembrar_grafo(conn: psycopg.Connection[Any], tenant: str) -> None:
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
        "VALUES (%s, %s, 'sha', 'main')",
        (SNAPSHOT, tenant),
    )
    kinds: dict[str, str] = {}
    for fid, path, simbolos in FICHEROS:
        conn.execute(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES (%s, %s, %s, 'python', 'h', %s)",
            (fid, tenant, path, SNAPSHOT),
        )
        kinds[fid] = "file"
        for i, sid in enumerate(simbolos):
            conn.execute(
                "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, "
                "end_line, signature) VALUES (%s, %s, %s, 'function', %s, %s, %s, 'def')",
                (sid, tenant, fid, sid, i + 1, i + 1),
            )
            kinds[sid] = "symbol"
    for tid, nombre in TABLAS:
        conn.execute(
            "INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre) VALUES (%s, %s, 'public', %s)",
            (tid, tenant, nombre),
        )
        kinds[tid] = "db_table"
    for tipo, src, dst in ARISTAS:
        conn.execute(
            "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (tenant, tipo, kinds[src], src, kinds[dst], dst),
        )


def _ticket_con_expediente(conn: psycopg.Connection[Any], tenant: str) -> str:
    """Ticket KC-0042 con dos corridas de QA: AC-1 falla en ambas (cuenta la última)."""
    ticket = f"tk_{tenant}"
    conn.execute(
        """INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion, impacto,
                                     pr_url, etapa, cerrado_en)
           VALUES (%s, %s, 'KC-0042', 'bug', 'La factura duplica el IVA al reemitir',
                   'Al reemitir una factura el IVA se suma dos veces.',
                   '{"modulos": ["src/billing"], "tablas": ["FACTURAS"]}',
                   'https://git.example/pr/42', 'produccion', now())""",
        (ticket, tenant),
    )
    spec = f"sp_{tenant}"
    conn.execute(
        "INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref, scope_paths) "
        "VALUES (%s, %s, %s, 1, 'blob://spec', ARRAY['src/billing'])",
        (spec, tenant, ticket),
    )
    for corrida, extracto in ((1, "total 121 != 100"), (2, "total 110 != 100")):
        run = f"qr_{tenant}_{corrida}"
        conn.execute(
            "INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion) "
            "VALUES (%s, %s, %s, %s, %s, '{}')",
            (run, tenant, ticket, spec, corrida),
        )
        conn.execute(
            "INSERT INTO core.qa_results (qa_run_id, tenant_id, criterio_id, resultado, "
            "extracto_fallo) VALUES (%s, %s, 'AC-1', 'falla', %s), (%s, %s, 'AC-2', 'pasa', NULL)",
            (run, tenant, extracto, run, tenant),
        )
    return ticket


def _registrar(tenant: str, nodos: Sequence[str], resumen: str, dias: int) -> str:
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        r = incidentes.registrar_incidente(
            loader,
            RegistroIncidenteIn(origen="manual", resumen=resumen, nodos=list(nodos)),
            embebedor=EMBEBEDOR,
        )
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE cm.incidents SET creado_en = %s WHERE tenant_id = %s AND id = %s",
            (AHORA - timedelta(days=dias), tenant, r.id),
        )
    return r.id


@pytest.fixture(scope="module")
def datos() -> Iterator[dict[str, str]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        for tenant in TODOS:
            _sembrar_grafo(conn, tenant)
        ticket = _ticket_con_expediente(conn, TENANT_A)
    ids = {
        "semilla": _registrar(TENANT_A, ["calc_iva"], "Redondeo del IVA en facturas", 30),
        "d1_pedido": _registrar(TENANT_A, ["crear_pedido"], "Pedido sin factura", 10),
        "d1_tabla": _registrar(TENANT_A, ["dbt_facturas"], "Bloqueo en facturas", 1),
        "d2": _registrar(TENANT_A, ["reporte"], "Reporte lento", 5),
        "d3": _registrar(TENANT_A, ["cron"], "Cron duplicado", 2),
        "otro": _registrar(TENANT_A, ["login"], "Login caído", 1),
        "b": _registrar(TENANT_B, ["calc_iva"], "[B] Redondeo del IVA de otra org", 0),
        "ticket": ticket,
    }
    yield ids
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)


def _consultar(tenant: str, dsn: str = APP_DSN, **kw: Any) -> cm_incidentes.ConsultaIncidentesOut:
    with GraphLoader(dsn=dsn, tenant_id=tenant) as loader:
        return cm_queries.resolver_incidentes(loader, ConsultaIncidentesIn(**kw))


# ---------------------------------------------------------------------------
# Esquema: RLS forzada y PK compuesta
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tabla", "pk"),
    [
        ("cm.incidents", ["tenant_id", "id"]),
        ("cm.incident_nodes", ["tenant_id", "incident_id", "nodo_ref"]),
        ("vec.incident_embeddings", ["tenant_id", "incident_id"]),
    ],
)
def test_rls_activa_forzada_y_pk_compuesta(tabla: str, pk: list[str]) -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        fila = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (tabla,),
        ).fetchone()
        columnas = [
            r[0]
            for r in conn.execute(
                """SELECT a.attname
                     FROM pg_constraint c
                     JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
                    WHERE c.conrelid = %s::regclass AND c.contype = 'p'
                    ORDER BY array_position(c.conkey, a.attnum)""",
                (tabla,),
            ).fetchall()
        ]
        politicas = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname || '.' || tablename = %s",
            (tabla,),
        ).fetchone()
    assert fila == (True, True)
    assert columnas == pk
    assert politicas and politicas[0] >= 1


def test_la_app_no_escribe_incidentes_de_otro_tenant(datos: dict[str, str]) -> None:
    with psycopg.connect(APP_DSN) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT_A,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO cm.incidents (id, tenant_id, resumen) VALUES ('inc_x', %s, 'x')",
                (TENANT_B,),
            )


# ---------------------------------------------------------------------------
# Registro desde el expediente
# ---------------------------------------------------------------------------
def test_ticket_cerrado_con_incidente_crea_fila_y_enlaces(datos: dict[str, str]) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_A) as loader:
        r = incidentes.registrar_desde_expediente(loader, datos["ticket"], embebedor=EMBEBEDOR)
        again = incidentes.registrar_desde_expediente(loader, datos["ticket"], embebedor=EMBEBEDOR)

    assert r.creado and r.embebido
    assert sorted(r.nodos_enlazados) == ["calc_iva", "dbt_facturas", "emitir", "fb"]
    assert (again.id, again.creado, again.embebido) == (r.id, False, False)  # idempotente

    with psycopg.connect(ADMIN_DSN) as conn:
        filas = conn.execute(
            """SELECT id, origen, resumen, causa, correccion, evidencia_ref, nodos_afectados
                 FROM cm.incidents WHERE tenant_id = %s AND ticket_id = %s""",
            (TENANT_A, datos["ticket"]),
        ).fetchall()
        enlaces = conn.execute(
            "SELECT nodo_ref FROM cm.incident_nodes WHERE tenant_id = %s AND incident_id = %s "
            "ORDER BY nodo_ref",
            (TENANT_A, r.id),
        ).fetchall()
        embeddings = conn.execute(
            "SELECT count(*) FROM vec.incident_embeddings WHERE tenant_id = %s AND incident_id = %s",
            (TENANT_A, r.id),
        ).fetchone()
    assert len(filas) == 1
    _, origen, resumen, causa, correccion, evidencia, foto = filas[0]
    assert (origen, resumen) == ("ticket", "La factura duplica el IVA al reemitir")
    assert causa == "AC-1: total 110 != 100"  # último extracto del criterio en falla
    assert (correccion, evidencia) == ("https://git.example/pr/42", "ticket:KC-0042")
    assert sorted(foto) == ["calc_iva", "dbt_facturas", "emitir", "fb"]
    assert [e[0] for e in enlaces] == ["calc_iva", "dbt_facturas", "emitir", "fb"]
    assert embeddings == (1,)

    # Y aparece para un área a un salto (crear_pedido llama a emitir), con su número de ticket.
    lineas = _consultar(TENANT_A, ficheros=[ORDER]).texto.splitlines()
    assert any(linea.startswith("- [KC-0042] La factura duplica el IVA") for linea in lineas)


def test_ticket_de_otro_tenant_no_se_encuentra(datos: dict[str, str]) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_B) as loader, pytest.raises(LookupError):
        incidentes.registro_desde_expediente(loader, datos["ticket"])


# ---------------------------------------------------------------------------
# Consulta por área: alcance, orden y recorte
# ---------------------------------------------------------------------------
def _ids(r: cm_incidentes.ConsultaIncidentesOut) -> list[str]:
    return [i.id for i in r.incidentes]


def test_area_a_dos_saltos_ordenada_por_distancia_y_recencia(datos: dict[str, str]) -> None:
    r = _consultar(TENANT_A, ficheros=[INVOICE])
    manuales = [i for i in r.incidentes if i.origen == "manual"]
    assert [i.id for i in manuales] == [
        datos["semilla"],  # 0 saltos
        datos["d1_tabla"],  # 1 salto, hace 1 día
        datos["d1_pedido"],  # 1 salto, hace 10 días
        datos["d2"],  # 2 saltos
    ]
    assert [i.distancia for i in manuales] == [0, 1, 1, 2]
    assert datos["d3"] not in _ids(r) and datos["otro"] not in _ids(r)  # 3 saltos / sin relación
    assert all(i.similitud is None for i in r.incidentes)  # sin embedding de consulta


def test_la_similitud_ordena_dentro_de_la_misma_distancia(datos: dict[str, str]) -> None:
    r = _consultar(TENANT_A, ficheros=[INVOICE], embedding=_vector(1))  # "Pedido"
    manuales = [i for i in r.incidentes if i.origen == "manual"]
    assert [i.id for i in manuales] == [
        datos["semilla"],
        datos["d1_pedido"],  # similitud 1 gana a la recencia
        datos["d1_tabla"],
        datos["d2"],
    ]
    assert manuales[1].similitud == pytest.approx(1.0)
    assert manuales[2].similitud == pytest.approx(0.0)


def test_saltos_acota_el_alcance(datos: dict[str, str]) -> None:
    assert _ids(_consultar(TENANT_A, nodos=["calc_iva"], saltos=0)) == [datos["semilla"]]


def test_recorte_por_incidentes_enteros(datos: dict[str, str]) -> None:
    completo = _consultar(TENANT_A, ficheros=[INVOICE])
    primera = completo.texto.splitlines()[0]
    r = _consultar(TENANT_A, ficheros=[INVOICE], presupuesto_caracteres=len(primera) + 3)
    assert r.texto == primera and len(r.incidentes) == 1
    assert r.omitidos == len(completo.incidentes) - 1


# ---------------------------------------------------------------------------
# Aislamiento y tenant vacío
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dsn", [APP_DSN, ADMIN_DSN], ids=["con-rls", "sin-rls-superusuario"])
def test_solo_incidentes_del_tenant_activo(datos: dict[str, str], dsn: str) -> None:
    a = _consultar(TENANT_A, dsn, ficheros=[INVOICE])
    b = _consultar(TENANT_B, dsn, ficheros=[INVOICE])
    assert datos["b"] not in _ids(a) and "[B]" not in a.texto
    assert _ids(b) == [datos["b"]] and b.texto.startswith("- [B] Redondeo")


def test_tenant_sin_incidentes_resuelve_vacio_y_la_plantilla_se_rellena(
    datos: dict[str, str],
) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_VACIO) as loader:
        valores = cm_queries.contexto_incidentes(
            loader, ficheros=[INVOICE], texto="IVA", embebedor=EMBEBEDOR
        )
        sin_area = cm_queries.contexto_incidentes(loader)
        inexistente = cm_queries.contexto_incidentes(loader, ficheros=["no/existe.py"])
    assert valores == sin_area == inexistente == {"incidents": ""}
    plantilla = "Historial de incidentes del área:\n{{incidents}}\nFin."
    rellena = plantilla.replace("{{incidents}}", valores["incidents"])
    assert rellena == "Historial de incidentes del área:\n\nFin."


def test_sin_tenant_en_la_sesion_no_devuelve_nada(datos: dict[str, str]) -> None:
    consulta = ConsultaIncidentesIn(ficheros=[INVOICE])
    with psycopg.connect(APP_DSN) as conn:
        filas = conn.execute(
            cm_queries.CONSULTAS_INCIDENTES["incidentes_area"],
            cm_incidentes.parametros_sql(consulta),
        ).fetchall()
    assert filas == []
