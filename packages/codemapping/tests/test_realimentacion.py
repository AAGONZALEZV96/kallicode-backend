"""Realimentación del grafo desde tickets entregados contra PostgreSQL 16 + pgvector (KC-CM-10-4).

Requiere `make up && make db` (migración 055). La realimentación corre como kallicode_app con el
tenant fijado por GraphLoader.fase(); el administrador solo siembra, inspecciona y limpia. El LLM
es un ejecutor inyectado y determinista: lo que se prueba es lo que el paso hace con su salida.

Criterios del ticket:
1. Un ticket entregado produce al menos una entrada de curación trazable a su expediente.
2. Nada entra al grafo sin curación: los nuggets quedan 'propuesto' con revisión pendiente y el
   incidente no llega a cm.incidents (ni a {{incidents}}) hasta que un curador lo aprueba.
3. Idempotente por ticket: reprocesar el cierre no duplica candidatos (ni con otra redacción del
   modelo, ni forzando el reproceso).
4. El coste se contabiliza en core.usage_tokens y se respeta el presupuesto del tenant.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import cm_queries, realimentacion
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.realimentacion import RespuestaPaso, TicketNoEntregadoError
from kallicode_core import cm_incidentes
from kallicode_core.cm_consulta import ConsultaNuggetsIn
from kallicode_core.cm_incidentes import ConsultaIncidentesIn
from kallicode_core.llm.cliente import LLMEscaladoHumano
from psycopg import errors

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT = "org_test_realim_a"
OTRO = "org_test_realim_b"
TODOS = (TENANT, OTRO)
CURADORA = "usr_test_realim_curadora"
SNAPSHOT = "snap_realim"
TICKET = "tk_test_realim_a"
TICKET_EN_BUILD = "tk_test_realim_build"
TICKET_OTRO = "tk_test_realim_b"
GATE = "gs_test_realim_a"
INVOICE = "src/billing/invoice.py"

FALLO_QA = "IVA esperado 19.00 obtenido 21.00"
COMENTARIO_GATE = "La nota de crédito debe conservar la tasa de IVA de la factura original."
REGLA = "Una nota de crédito conserva la tasa de IVA de la factura original."
DIM = cm_incidentes.DIMENSION_EMBEDDING


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                "SELECT to_regclass('cm.realimentacion_tickets') IS NOT NULL"
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 055 sin aplicar (make up && make db)"
)


# ---------------------------------------------------------------------------
# LLM determinista
# ---------------------------------------------------------------------------
def _salida_base(payload: dict[str, Any]) -> dict[str, Any]:
    """La respuesta "buena" más tres propuestas que el paso debe descartar."""
    items = {i["item_id"] for i in payload["items"]}
    gate = next(i for i in items if i.startswith("gate2:"))
    return {
        "nuggets": [
            {
                "rule": REGLA,
                "kind": "regla_negocio",
                "evidence": {"item_id": gate, "quote": "conservar la tasa de IVA de la factura"},
                "scope_refs": [INVOICE],
            },
            {
                "rule": "Regla sin respaldo.",
                "kind": "regla_negocio",
                "evidence": None,
                "scope_refs": [],
            },
            {
                "rule": "Regla con evidencia inventada.",
                "kind": "restriccion_tecnica",
                "evidence": {"item_id": "qa:AC-9", "quote": "no existe"},
                "scope_refs": [],
            },
            {
                "rule": "Regla con cita falsa.",
                "kind": "restriccion_tecnica",
                "evidence": {"item_id": "qa:AC-1", "quote": "esto no aparece en el fallo"},
                "scope_refs": [],
            },
        ],
        "incident": {
            "summary": "La nota de crédito recalculaba el IVA con la tasa vigente",
            "cause": "Se aplicaba la tasa vigente en lugar de la de la factura original",
            "fix": None,
            "evidence": [{"item_id": "qa:AC-1", "quote": FALLO_QA}],
        },
    }


def _otra_redaccion(payload: dict[str, Any]) -> dict[str, Any]:
    salida = _salida_base(payload)
    salida["nuggets"][0]["rule"] = "La nota de crédito usa el IVA de la factura de origen."
    salida["incident"]["summary"] = "Otra redacción del mismo incidente"
    return salida


class _LLM:
    def __init__(self, salida: Callable[[dict[str, Any]], dict[str, Any]] = _salida_base) -> None:
        self.salida = salida
        self.payloads: list[dict[str, Any]] = []

    @property
    def llamadas(self) -> int:
        return len(self.payloads)

    def __call__(self, paso: str, payload: dict[str, Any]) -> RespuestaPaso:
        assert paso == realimentacion.PASO_REALIMENTACION
        self.payloads.append(payload)
        return RespuestaPaso(
            salida=self.salida(payload),
            tokens_in=1_234,
            tokens_out=321,
            duracion_ms=50,
            modelo="deepseek-v4-flash",
            tier="flash",
        )


class _Escalado:
    def __call__(self, paso: str, payload: dict[str, Any]) -> RespuestaPaso:
        raise LLMEscaladoHumano(paso, "esquema", [])


class _Embebedor:
    def __init__(self) -> None:
        self.llamadas = 0

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.llamadas += 1
        return [[1.0] + [0.0] * (DIM - 1) for _ in texts]


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        for tabla in (
            "cm.realimentacion_tickets",
            "cm.nuggets",
            "cm.curation_queue",
            "vec.incident_embeddings",
            "cm.incident_nodes",
            "cm.incidents",
            "core.usage_tokens",
            "cm.symbols",
            "cm.files",
            "cm.nodes",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
            # Las tablas probatorias (qa_*, security_findings, gate_signatures) son append-only:
            # caen en cascada al borrar el ticket.
            "core.tickets",
            "core.ticket_counters",
            "core.users",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _ticket(conn: psycopg.Connection[Any], tenant: str, ticket: str, numero: str,
            etapa: str) -> None:  # fmt: skip
    conn.execute(
        """INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion, impacto,
                                     pr_url, etapa, cerrado_en)
           VALUES (%s, %s, %s, 'bug', 'La nota de crédito pierde el IVA original',
                   'Al emitir una nota de crédito desde una factura pagada se recalcula el IVA con
                    la tasa vigente y no con la de la factura original.',
                   '{"modulos": ["src/billing"]}', 'https://git.example/pr/77', %s,
                   CASE WHEN %s = 'produccion' THEN now() END)""",
        (ticket, tenant, numero, etapa, etapa),
    )


def _sembrar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
    conn.execute(
        "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'Curadora')",
        (CURADORA, TENANT, f"{CURADORA}@prueba.dev"),
    )
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
        "VALUES (%s, %s, 'sha', 'main')",
        (SNAPSHOT, TENANT),
    )
    conn.execute(
        "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
        "VALUES ('rf_inv', %s, %s, 'python', 'h', %s)",
        (TENANT, INVOICE, SNAPSHOT),
    )
    conn.execute(
        "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
        "signature) VALUES ('rf_emitir_nc', %s, 'rf_inv', 'function', 'emitir_nc', 1, 9, 'def')",
        (TENANT,),
    )

    _ticket(conn, TENANT, TICKET, "KC-0077", "produccion")
    _ticket(conn, TENANT, TICKET_EN_BUILD, "KC-0078", "build")
    _ticket(conn, OTRO, TICKET_OTRO, "KC-0001", "produccion")

    spec = "sp_test_realim_a"
    conn.execute(
        "INSERT INTO core.specs (id, tenant_id, ticket_id, version, estado, spec_md_ref, "
        "scope_paths) VALUES (%s, %s, %s, 1, 'aprobada', 'blob://spec', ARRAY[%s])",
        (spec, TENANT, TICKET, INVOICE),
    )
    conn.execute(
        "INSERT INTO core.spec_criteria (spec_id, tenant_id, criterio_id, given_txt, when_txt, "
        "then_txt) VALUES (%s, %s, 'AC-1', 'una factura pagada con IVA del 19', "
        "'se emite su nota de crédito', 'la nota aplica el IVA del 19')",
        (spec, TENANT),
    )
    run = "qr_test_realim_a"
    conn.execute(
        "INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion) "
        "VALUES (%s, %s, %s, %s, 1, '{}')",
        (run, TENANT, TICKET, spec),
    )
    conn.execute(
        "INSERT INTO core.qa_results (qa_run_id, tenant_id, criterio_id, resultado, "
        "extracto_fallo) VALUES (%s, %s, 'AC-1', 'falla', %s)",
        (run, TENANT, FALLO_QA),
    )
    conn.execute(
        """INSERT INTO core.security_findings (id, tenant_id, ticket_id, origen, regla, archivo,
                                               severidad, titulo, veredicto, estado)
           VALUES ('sf_test_realim_a', %s, %s, 'sast', 'py.sql-injection', %s, 'alta',
                   'Consulta SQL construida por concatenación', 'confirmado', 'corregido')""",
        (TENANT, TICKET, INVOICE),
    )
    conn.execute(
        "INSERT INTO core.gate_signatures (id, tenant_id, ticket_id, gate, accion, actor_id, "
        "comentario, sello) VALUES (%s, %s, %s, 2, 'cambios_pedidos', %s, %s, 'sello')",
        (GATE, TENANT, TICKET, CURADORA, COMENTARIO_GATE),
    )


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        _sembrar(conn)
        yield conn
        _limpiar(conn)


@contextmanager
def _app(tenant: str = TENANT) -> Iterator[psycopg.Connection[Any]]:
    """Sesión de la aplicación (kallicode_app, RLS forzado) fijada a un tenant."""
    with psycopg.connect(APP_DSN, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        yield conn


def _realimentar(llm: Any, ticket: str = TICKET, tenant: str = TENANT, **kw: Any) -> Any:
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        return realimentacion.realimentar_ticket(loader, ticket, ejecutar=llm, **kw)


def _nuggets(admin: psycopg.Connection[Any]) -> list[dict[str, Any]]:
    cur = admin.execute(
        """SELECT id, estado, origen, ticket_id, evidencia_ref, seccion, cita, regla
             FROM cm.nuggets WHERE tenant_id = %s ORDER BY id""",
        (TENANT,),
    )
    columnas = [c.name for c in cur.description or []]
    return [dict(zip(columnas, f, strict=True)) for f in cur.fetchall()]


def _cola(admin: psycopg.Connection[Any], tipo: str) -> list[dict[str, Any]]:
    cur = admin.execute(
        "SELECT id, estado, payload FROM cm.curation_queue WHERE tenant_id = %s AND tipo = %s "
        "ORDER BY creado_en, id",
        (TENANT, tipo),
    )
    return [{"id": f[0], "estado": f[1], "payload": f[2]} for f in cur.fetchall()]


def _consumo(admin: psycopg.Connection[Any]) -> list[tuple[Any, ...]]:
    return admin.execute(
        "SELECT ticket_id, origen, etapa, tier, tokens_in, tokens_out FROM core.usage_tokens "
        "WHERE tenant_id = %s AND origen = 'codemapping_feedback' ORDER BY creado_en",
        (TENANT,),
    ).fetchall()


# ---------------------------------------------------------------------------
# 1. Entradas de curación trazables al expediente
# ---------------------------------------------------------------------------
def test_ticket_entregado_deja_entradas_de_curacion_trazables(
    admin: psycopg.Connection[Any],
) -> None:
    llm = _LLM()
    r = _realimentar(llm)

    assert (r.estado, r.omitido, r.nuggets_propuestos, r.incidente_propuesto) == (
        "completado",
        False,
        1,
        True,
    )
    assert r.motivos_descarte == {
        realimentacion.SIN_EVIDENCIA: 1,
        realimentacion.EVIDENCIA_NO_RESOLUBLE: 1,
        realimentacion.CITA_NO_ENCONTRADA: 1,
    }

    # El expediente que vio el LLM: título, QA, seguridad, gate, spec y descripción, delimitados.
    items = llm.payloads[0]["items"]
    assert [i["item_id"].split(":")[0] for i in items] == [
        "ticket", "qa", "sf", "gate2", "spec", "ticket",
    ]  # fmt: skip
    delim = llm.payloads[0]["delimiter"]
    assert all(i["text"].startswith(f"<{delim}>") for i in items)
    assert llm.payloads[0]["incident_allowed"] is True

    (nugget,) = _nuggets(admin)
    assert nugget["origen"] == "ticket"
    assert nugget["ticket_id"] == TICKET
    assert nugget["evidencia_ref"] is None
    assert nugget["seccion"] == f"gate2:{GATE}"
    assert nugget["cita"] in COMENTARIO_GATE
    assert nugget["regla"] == REGLA

    (revision,) = _cola(admin, "nugget")
    assert revision["payload"]["ticket_id"] == TICKET
    assert revision["payload"]["origen"] == "ticket"
    assert revision["payload"]["nugget_id"] == nugget["id"]

    (candidato,) = _cola(admin, "incidente")
    p = candidato["payload"]
    assert p["ticket_id"] == TICKET
    assert p["evidencia_ref"] == "ticket:KC-0077"
    assert p["evidencias"] == ["qa:AC-1"]
    assert p["citas"] == [FALLO_QA]
    assert p["correccion"] == "https://git.example/pr/77"  # sin fix del LLM: el PR
    assert {"rf_inv", "rf_emitir_nc"} <= set(p["nodos"])


# ---------------------------------------------------------------------------
# 2. Nada entra al grafo sin curación
# ---------------------------------------------------------------------------
def test_nada_entra_al_grafo_sin_curacion(admin: psycopg.Connection[Any]) -> None:
    _realimentar(_LLM())

    assert {n["estado"] for n in _nuggets(admin)} == {"propuesto"}
    assert {c["estado"] for c in _cola(admin, "nugget") + _cola(admin, "incidente")} == {
        "pendiente"
    }
    assert admin.execute(
        "SELECT count(*) FROM cm.incidents WHERE tenant_id = %s", (TENANT,)
    ).fetchone() == (0,)

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        incidentes = cm_queries.resolver_incidentes(
            loader, ConsultaIncidentesIn(ficheros=[INVOICE])
        )
        nuggets = cm_queries.resolver_nuggets(
            loader, ConsultaNuggetsIn(variable="nuggets", ficheros=[INVOICE])
        )
    assert incidentes.incidentes == [] and incidentes.texto == ""
    assert nuggets.nuggets == []


def test_un_nugget_de_ticket_no_puede_nacer_aprobado(admin: psycopg.Connection[Any]) -> None:
    with _app() as app, pytest.raises(errors.CheckViolation):
        app.execute(
            """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, origen, ticket_id, estado)
               VALUES ('ng_atajo', %s, 'Atajo', 'regla_negocio', 'ticket', %s, 'aprobado')""",
            (TENANT, TICKET),
        )


@pytest.mark.parametrize(
    ("columnas", "valores"),
    [
        # ticket sin ticket_id
        ("origen", "'ticket'"),
        # documento sin chunk
        ("origen, ticket_id", f"'documento', '{TICKET}'"),
    ],
)
def test_el_origen_del_nugget_es_coherente(
    admin: psycopg.Connection[Any], columnas: str, valores: str
) -> None:
    with _app() as app, pytest.raises(errors.CheckViolation):
        app.execute(
            f"INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, {columnas}) "
            f"VALUES ('ng_mal', %s, 'Regla', 'regla_negocio', {valores})",
            (TENANT,),
        )


def test_aprobar_el_candidato_materializa_el_incidente(admin: psycopg.Connection[Any]) -> None:
    _realimentar(_LLM())
    (candidato,) = _cola(admin, "incidente")
    embebedor = _Embebedor()

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        incidente_id = realimentacion.resolver_candidato_incidente(
            loader,
            candidato["id"],
            CURADORA,
            "aprobado",
            correccion="Se toma la tasa de IVA de la factura original.",
            embebedor=embebedor,
        )
        resuelto = cm_queries.resolver_incidentes(loader, ConsultaIncidentesIn(ficheros=[INVOICE]))

    assert incidente_id is not None
    fila = admin.execute(
        "SELECT ticket_id, origen, correccion, evidencia_ref FROM cm.incidents "
        "WHERE tenant_id = %s AND id = %s",
        (TENANT, incidente_id),
    ).fetchone()
    assert fila == (
        TICKET,
        "ticket",
        "Se toma la tasa de IVA de la factura original.",
        "ticket:KC-0077",
    )
    enlaces = admin.execute(
        "SELECT nodo_ref FROM cm.incident_nodes WHERE tenant_id = %s AND incident_id = %s",
        (TENANT, incidente_id),
    ).fetchall()
    assert {"rf_inv", "rf_emitir_nc"} <= {e[0] for e in enlaces}
    assert embebedor.llamadas == 1
    assert [i.id for i in resuelto.incidentes] == [incidente_id]
    (revision,) = _cola(admin, "incidente")
    assert revision["estado"] == "aprobado"
    assert revision["payload"]["incidente_id"] == incidente_id

    # Ya resuelto: no hay nada pendiente que resolver otra vez.
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader, pytest.raises(errors.NoDataFound):
        realimentacion.resolver_candidato_incidente(loader, candidato["id"], CURADORA, "aprobado")


def test_rechazar_el_candidato_no_crea_incidente(admin: psycopg.Connection[Any]) -> None:
    _realimentar(_LLM())
    (candidato,) = _cola(admin, "incidente")
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        assert (
            realimentacion.resolver_candidato_incidente(
                loader, candidato["id"], CURADORA, "rechazado", motivo="No es un fallo real"
            )
            is None
        )
    assert admin.execute(
        "SELECT count(*) FROM cm.incidents WHERE tenant_id = %s", (TENANT,)
    ).fetchone() == (0,)


def test_un_nugget_de_ticket_aprobado_llega_a_nuggets_con_su_ticket(
    admin: psycopg.Connection[Any],
) -> None:
    _realimentar(_LLM())
    (nugget,) = _nuggets(admin)
    with _app() as app:
        app.execute(
            "SELECT * FROM cm.resolver_nugget(%s, %s, 'aprobado')", (nugget["id"], CURADORA)
        )
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        # scope_refs nombra el fichero: aplica al área de ese fichero.
        r = cm_queries.resolver_nuggets(
            loader, ConsultaNuggetsIn(variable="nuggets", ficheros=[INVOICE])
        )
    (aplicable,) = r.nuggets
    assert aplicable.evidencia_ref == f"ticket:{TICKET}"
    assert aplicable.seccion == f"gate2:{GATE}"


# ---------------------------------------------------------------------------
# 3. Idempotencia por ticket
# ---------------------------------------------------------------------------
def test_reprocesar_el_cierre_no_duplica_candidatos(admin: psycopg.Connection[Any]) -> None:
    llm = _LLM()
    primero = _realimentar(llm)
    antes = (_nuggets(admin), _cola(admin, "nugget"), _cola(admin, "incidente"))

    # Otra entrega del mismo mensaje, con un modelo que ahora redactaría distinto.
    otra = _LLM(_otra_redaccion)
    segundo = _realimentar(otra)

    assert segundo.omitido is True
    assert otra.llamadas == 0  # ni coste
    assert (segundo.estado, segundo.nuggets_propuestos, segundo.incidente_propuesto) == (
        primero.estado,
        primero.nuggets_propuestos,
        primero.incidente_propuesto,
    )
    assert (_nuggets(admin), _cola(admin, "nugget"), _cola(admin, "incidente")) == antes
    assert len(_consumo(admin)) == 1


def test_un_reproceso_forzado_tampoco_duplica(admin: psycopg.Connection[Any]) -> None:
    _realimentar(_LLM())
    antes = (_nuggets(admin), _cola(admin, "nugget"), _cola(admin, "incidente"))

    # Forzar el reproceso: el cerrojo se da por fallido y el LLM repite la misma respuesta.
    admin.execute(
        "UPDATE cm.realimentacion_tickets SET estado = 'fallido', completado_en = NULL "
        "WHERE tenant_id = %s",
        (TENANT,),
    )
    r = _realimentar(_LLM())

    assert (r.estado, r.omitido, r.nuggets_propuestos, r.incidente_propuesto) == (
        "completado",
        False,
        0,
        False,
    )
    assert (_nuggets(admin), _cola(admin, "nugget"), _cola(admin, "incidente")) == antes
    intentos = admin.execute(
        "SELECT intentos FROM cm.realimentacion_tickets WHERE tenant_id = %s", (TENANT,)
    ).fetchone()
    assert intentos == (2,)


def test_un_candidato_a_incidente_por_ticket(admin: psycopg.Connection[Any]) -> None:
    payload = json.dumps({"ticket_id": TICKET, "resumen": "Fallo"})
    with _app() as app:
        app.execute(
            "INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload) "
            "VALUES ('cq_uno', %s, 'incidente', %s::jsonb)",
            (TENANT, payload),
        )
        with pytest.raises(errors.UniqueViolation):
            app.execute(
                "INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload) "
                "VALUES ('cq_dos', %s, 'incidente', %s::jsonb)",
                (TENANT, payload),
            )


def test_un_candidato_a_incidente_exige_ticket_y_resumen(admin: psycopg.Connection[Any]) -> None:
    with _app() as app, pytest.raises(errors.CheckViolation):
        app.execute(
            "INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload) "
            "VALUES ('cq_mal', %s, 'incidente', %s::jsonb)",
            (TENANT, json.dumps({"ticket_id": TICKET})),
        )


# ---------------------------------------------------------------------------
# 4. Coste y presupuesto
# ---------------------------------------------------------------------------
def test_el_coste_del_paso_se_contabiliza(admin: psycopg.Connection[Any]) -> None:
    r = _realimentar(_LLM())

    assert _consumo(admin) == [
        (TICKET, "codemapping_feedback", "realimentacion", "flash", 1_234, 321)
    ]
    assert (r.tokens_in, r.tokens_out) == (1_234, 321)
    fila = admin.execute(
        "SELECT estado, tokens_in, tokens_out, completado_en IS NOT NULL "
        "FROM cm.realimentacion_tickets WHERE tenant_id = %s AND ticket_id = %s",
        (TENANT, TICKET),
    ).fetchone()
    assert fila == ("completado", 1_234, 321, True)


def test_sin_presupuesto_del_tenant_no_se_llama_al_llm(admin: psycopg.Connection[Any]) -> None:
    # El tenant ya gastó casi todo el mes en el pipeline.
    admin.execute(
        """INSERT INTO core.usage_tokens (tenant_id, ticket_id, etapa, modelo, tier, tokens_in,
                                          tokens_out, duracion_ms, origen)
           VALUES (%s, %s, 'build', 'deepseek-v4-pro', 'pro', 1999000, 500, 10, 'pipeline')""",
        (TENANT, TICKET),
    )
    llm = _LLM()
    r = _realimentar(llm, presupuesto_tokens_tenant_mes=2_000_000)

    assert r.estado == "sin_presupuesto"
    assert r.motivo and r.motivo.startswith(realimentacion.PRESUPUESTO_TENANT)
    assert llm.llamadas == 0
    assert _nuggets(admin) == [] and _cola(admin, "incidente") == []
    assert _consumo(admin) == []

    # Con presupuesto (p. ej. en el ciclo siguiente) el mismo ticket se procesa.
    r = _realimentar(llm, presupuesto_tokens_tenant_mes=10_000_000)
    assert (r.estado, r.omitido, llm.llamadas) == ("completado", False, 1)


def test_el_presupuesto_por_ticket_acota_el_expediente(admin: psycopg.Connection[Any]) -> None:
    llm = _LLM()
    r = _realimentar(llm, presupuesto_tokens_ticket=realimentacion.TOKENS_SALIDA_RESERVA + 1)
    assert r.estado == "sin_presupuesto"
    assert r.motivo and r.motivo.startswith(realimentacion.PRESUPUESTO_TICKET)
    assert llm.llamadas == 0


# ---------------------------------------------------------------------------
# Bordes
# ---------------------------------------------------------------------------
def test_un_ticket_no_entregado_no_se_realimenta(admin: psycopg.Connection[Any]) -> None:
    llm = _LLM()
    with pytest.raises(TicketNoEntregadoError):
        _realimentar(llm, ticket=TICKET_EN_BUILD)
    assert llm.llamadas == 0
    assert admin.execute(
        "SELECT count(*) FROM cm.realimentacion_tickets WHERE tenant_id = %s", (TENANT,)
    ).fetchone() == (0,)


def test_llm_sin_respuesta_valida_queda_fallido_y_se_reintenta(
    admin: psycopg.Connection[Any],
) -> None:
    r = _realimentar(_Escalado())
    assert r.estado == "fallido"
    assert r.motivo and r.motivo.startswith(realimentacion.LLM_ESCALADO)
    assert _nuggets(admin) == []

    r = _realimentar(_LLM())
    assert (r.estado, r.nuggets_propuestos) == ("completado", 1)


def test_aislamiento_entre_tenants(admin: psycopg.Connection[Any]) -> None:
    _realimentar(_LLM())

    # El otro tenant no ve el ticket ni el cerrojo, aunque conozca sus ids.
    with pytest.raises(LookupError):
        _realimentar(_LLM(), ticket=TICKET, tenant=OTRO)
    with _app(OTRO) as app:
        assert app.execute("SELECT count(*) FROM cm.realimentacion_tickets").fetchone() == (0,)


def test_rls_forzada_en_el_cerrojo(admin: psycopg.Connection[Any]) -> None:
    fila = admin.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = 'cm.realimentacion_tickets'::regclass"
    ).fetchone()
    assert fila == (True, True)


def test_procesar_mensaje_ignora_un_ticket_no_entregado(admin: psycopg.Connection[Any]) -> None:
    assert (
        realimentacion.procesar_mensaje(
            {"tenant_id": TENANT, "ticket_id": TICKET_EN_BUILD}, APP_DSN
        )
        is None
    )
