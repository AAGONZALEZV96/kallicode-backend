"""Convención tenant-first de los índices (KC-DB-06-2, cierra DB-F26).

Todo índice explícito (los que respaldan una PRIMARY KEY o UNIQUE se justifican por su restricción)
sobre una tabla que tiene `tenant_id` debe empezar por `tenant_id`, para que el filtro implícito de
RLS se resuelva dentro del índice y no como un filtro posterior a la búsqueda. Las excepciones están
documentadas, con su plan de ejecución, en docs/plan-indices.md (sección 8.2).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest
from kallicode_core import db
from psycopg import sql
from sqlalchemy import text

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
PLAN = RAIZ / "docs" / "plan-indices.md"
MIGRACION = RAIZ / "db" / "migrations" / "032_indices_tenant_first.sql"
ESQUEMAS = ("core", "vec", "audit", "cm")
_CELDAS = re.compile(r"(?<!\\)\|")

# Índices reescritos con tenant_id primero (KC-DB-06-2) y los que reemplazan.
NUEVOS = (
    "core.gates_tenant_ticket_ix",
    "core.gates_tenant_actor_ix",
    "core.job_transitions_tenant_job_ix",
    "core.llm_steps_tenant_job_ix",
    "core.llm_steps_tenant_ticket_etapa_ix",
    "core.notifications_tenant_user_ix",
    "core.notifications_tenant_noleidas_ix",
    "core.refresh_tokens_tenant_user_activos_ix",
    "core.qa_runs_tenant_ticket_ix",
    "core.specs_tenant_ticket_ix",
    "core.usage_tokens_tenant_ticket_etapa_ix",
)
RETIRADOS = (
    "core.gates_ticket_ix",
    "core.gates_actor_ix",
    "core.job_transitions_job_ix",
    "core.llm_steps_job_ix",
    "core.llm_steps_ticket_etapa_ix",
    "core.notifications_user_ix",
    "core.notifications_noleidas_ix",
    "core.refresh_tokens_user_ix",
    "core.qa_runs_ticket_ix",
    "core.specs_ticket_ix",
    "core.usage_tokens_ticket_ix",
    "core.attachments_ticket_ix",
    "core.findings_ticket_ix",
    "core.usage_loc_ticket_ix",
    "core.watchers_user_ix",
    "core.tickets_reporter_ix",
    "core.tickets_duplicado_ix",
)

# Las diez consultas afectadas (las mismas de la comparativa 8.3 del plan), con su tabla y los
# índices tenant-first aceptables: el reescrito o el de FK que ya existía con las mismas columnas.
T, U = "'tk_conv'", "'us_conv'"
DIEZ = [
    (
        "C1",
        "gate_signatures",
        {"gates_tenant_ticket_ix", "idx_gate_signatures_tenant_ticket_id"},
        "SELECT g.gate, g.accion, u.nombre AS actor, g.creado_en AS fecha, g.comentario, g.sello "
        f"FROM core.gate_signatures g JOIN core.users u ON u.id = g.actor_id WHERE g.ticket_id = {T} "
        "ORDER BY g.gate, g.creado_en",
    ),
    (
        "C2",
        "llm_steps",
        {"llm_steps_tenant_ticket_etapa_ix", "idx_llm_steps_tenant_ticket_id"},
        f"SELECT etapa, paso, modelo, tier, confianza, validacion, duracion_ms, id FROM core.llm_steps "
        f"WHERE ticket_id = {T} ORDER BY creado_en",
    ),
    (
        "C3",
        "notifications",
        {"notifications_tenant_user_ix", "idx_notifications_tenant_user_id"},
        "SELECT id, tipo, titulo, cuerpo, ticket_id, creada_en, leida FROM core.notifications "
        f"WHERE user_id = {U} ORDER BY creada_en DESC LIMIT 50 OFFSET 0",
    ),
    (
        "C4",
        "notifications",
        {"notifications_tenant_noleidas_ix"},
        f"SELECT count(*) AS n FROM core.notifications WHERE user_id = {U} AND NOT leida",
    ),
    (
        "C5",
        "notifications",
        {"notifications_tenant_noleidas_ix"},
        f"UPDATE core.notifications SET leida = true WHERE user_id = {U} AND NOT leida RETURNING id",
    ),
    (
        "C6",
        "qa_runs",
        {"qa_runs_tenant_ticket_ix", "idx_qa_runs_tenant_ticket_id"},
        f"SELECT * FROM core.qa_runs WHERE ticket_id = {T} ORDER BY corrida DESC LIMIT 1",
    ),
    (
        "C7",
        "specs",
        {"specs_tenant_ticket_ix", "idx_specs_tenant_ticket_id"},
        f"SELECT * FROM core.specs WHERE ticket_id = {T} ORDER BY version DESC LIMIT 1",
    ),
    (
        "C8",
        "usage_tokens",
        {"usage_tokens_tenant_ticket_etapa_ix", "idx_usage_tokens_tenant_ticket_id"},
        "SELECT etapa AS funcion, modelo, sum(invocaciones)::int AS invocaciones, sum(tokens_in)::bigint "
        "AS tokens_in, sum(tokens_out)::bigint AS tokens_out FROM core.usage_tokens "
        f"WHERE ticket_id = {T} GROUP BY etapa, modelo ORDER BY etapa",
    ),
    (
        "C9",
        "usage_loc",
        {"idx_usage_loc_tenant_ticket_id"},
        "SELECT coalesce(sum(anadidas),0)::int AS anadidas, coalesce(sum(netas),0)::int AS netas "
        f"FROM core.usage_loc WHERE ticket_id = {T} AND tipo = 'pr'",
    ),
    (
        "C10",
        "refresh_tokens",
        {"idx_refresh_tokens_tenant_user_id"},
        f"UPDATE core.refresh_tokens SET revocado = true WHERE user_id = {U}",
    ),
]


def _filas(seccion: str) -> list[list[str]]:
    return [
        [c.strip() for c in _CELDAS.split(linea.strip())[1:-1]]
        for linea in seccion.splitlines()
        if linea.startswith("| ")
    ]


def _excepciones_documentadas() -> dict[str, tuple[str, str]]:
    texto = PLAN.read_text(encoding="utf-8")
    ini = texto.index("<!-- excepciones-tenant-first:inicio -->")
    fin = texto.index("<!-- excepciones-tenant-first:fin -->")
    res = {}
    for celdas in _filas(texto[ini:fin]):
        if celdas and celdas[0].startswith("`"):
            res[celdas[0].strip("`")] = (celdas[1], celdas[2].strip("`"))
    return res


async def _incumplidores() -> set[str]:
    """Índices explícitos sobre tablas con tenant_id cuya primera columna no es tenant_id."""
    async with db.sesion_sistema() as admin:
        filas = await admin.execute(
            text(
                """
                SELECT n.nspname || '.' || c.relname
                  FROM pg_index i
                  JOIN pg_class c ON c.oid = i.indexrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = ANY(:esquemas)
                   AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
                   AND EXISTS (SELECT 1 FROM pg_attribute a
                                WHERE a.attrelid = i.indrelid AND a.attname = 'tenant_id'
                                  AND NOT a.attisdropped)
                   AND coalesce((SELECT a.attname FROM pg_attribute a
                                  WHERE a.attrelid = i.indrelid
                                    AND a.attnum = (i.indkey::int2[])[0]), '<expresion>') <> 'tenant_id'
                """
            ),
            {"esquemas": list(ESQUEMAS)},
        )
        return {r[0] for r in filas.all()}


# --- criterio 1: cero índices que incumplan la convención, salvo excepción documentada ------
async def test_ningun_indice_incumple_tenant_first_salvo_excepcion_documentada() -> None:
    incumplidores = await _incumplidores()
    documentadas = set(_excepciones_documentadas())
    sin_documentar = sorted(incumplidores - documentadas)
    assert not sin_documentar, (
        "Índices sobre tablas con tenant_id que no empiezan por tenant_id y no son una excepción "
        "documentada (reescríbelos con tenant_id primero o documenta la excepción con su plan en "
        f"docs/plan-indices.md, sección 8.2): {sin_documentar}"
    )


async def test_las_excepciones_documentadas_siguen_siendo_excepciones() -> None:
    incumplidores = await _incumplidores()
    obsoletas = sorted(set(_excepciones_documentadas()) - incumplidores)
    assert not obsoletas, f"excepciones documentadas que ya no incumplen la convención: {obsoletas}"


def test_cada_excepcion_tiene_motivo_y_plan_de_ejecucion() -> None:
    excepciones = _excepciones_documentadas()
    assert excepciones, "no se encontró la tabla de excepciones del plan"
    incompletas = []
    for indice, (motivo, plan) in excepciones.items():
        if len(motivo) < 30:
            incompletas.append((indice, "motivo demasiado corto"))
        if "Index" not in plan and "Scan" not in plan:
            incompletas.append((indice, "el plan no muestra un recorrido por índice"))
    assert not incompletas, incompletas


# --- criterio 2: CREATE INDEX CONCURRENTLY y después DROP INDEX CONCURRENTLY -----------------
def test_la_migracion_crea_y_despues_retira_sin_ventana_sin_indice() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    assert sql.lstrip().startswith("-- kc:sin-transaccion"), (
        "debe ir sin transacción (CONCURRENTLY)"
    )
    sentencias = [
        re.sub(r"\s+", " ", s).strip() for s in re.sub(r"--[^\n]*", "", sql).split(";") if s.strip()
    ]
    creaciones = [i for i, s in enumerate(sentencias) if s.upper().startswith("CREATE INDEX")]
    retiradas = [i for i, s in enumerate(sentencias) if s.upper().startswith("DROP INDEX")]
    assert creaciones and retiradas
    assert all("CONCURRENTLY" in sentencias[i].upper() for i in creaciones + retiradas), (
        "toda creación y retirada debe ser CONCURRENTLY"
    )
    assert all("IF NOT EXISTS" in sentencias[i].upper() for i in creaciones), "re-ejecutable"
    assert all("IF EXISTS" in sentencias[i].upper() for i in retiradas), "re-ejecutable"
    assert max(creaciones) < min(retiradas), (
        "primero deben crearse todos los índices nuevos y solo después retirarse los viejos"
    )
    assert len(creaciones) == len(NUEVOS)


async def test_los_indices_nuevos_existen_y_empiezan_por_tenant_id_y_los_viejos_ya_no() -> None:
    async with db.sesion_sistema() as admin:
        fila = await admin.execute(
            text(
                """
                SELECT n.nspname || '.' || c.relname,
                       i.indisvalid,
                       (SELECT a.attname FROM pg_attribute a
                         WHERE a.attrelid = i.indrelid AND a.attnum = (i.indkey::int2[])[0])
                  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = ANY(:esquemas)
                """
            ),
            {"esquemas": list(ESQUEMAS)},
        )
        existentes = {r[0]: (r[1], r[2]) for r in fila.all()}
    faltan = [i for i in NUEVOS if i not in existentes]
    assert not faltan, f"faltan los índices tenant-first: {faltan}"
    assert {i: existentes[i] for i in NUEVOS if existentes[i] != (True, "tenant_id")} == {}
    siguen = [i for i in RETIRADOS if i in existentes]
    assert not siguen, f"índices que debían retirarse y siguen existiendo: {siguen}"


async def test_no_hay_indices_invalidos() -> None:
    """Un CREATE INDEX CONCURRENTLY que falla a mitad deja un índice INVÁLIDO que el IF NOT EXISTS
    de una nueva ejecución daría por bueno."""
    async with db.sesion_sistema() as admin:
        invalidos = [
            r[0]
            for r in (
                await admin.execute(
                    text(
                        "SELECT n.nspname || '.' || c.relname FROM pg_index i "
                        "JOIN pg_class c ON c.oid = i.indexrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE NOT i.indisvalid AND n.nspname = ANY(:esquemas)"
                    ),
                    {"esquemas": list(ESQUEMAS)},
                )
            ).all()
        ]
    assert not invalidos, f"índices inválidos (bórralos con DROP INDEX CONCURRENTLY): {invalidos}"


# --- criterio 4: las diez consultas, con RLS activo, usan el índice y no un filtro posterior ----
def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _detalles_del_nodo(plan: list[str], tabla: str) -> list[str]:
    """Detalle de los nodos que recorren EXACTAMENTE `tabla` (no un índice de nombre parecido)."""
    detalle: list[str] = []
    for i, cabecera in enumerate(plan):
        if re.search(rf" on {re.escape(tabla)}( |$)", cabecera) and "Scan" in cabecera:
            sangria = len(cabecera) - len(cabecera.lstrip())
            for sig in plan[i + 1 :]:
                if len(sig) - len(sig.lstrip()) > sangria and not sig.lstrip().startswith("->"):
                    detalle.append(sig.strip())
                else:
                    break
    return detalle


def _explain_con_rls_sin_competidores(tabla: str, aceptables: set[str], consulta: str) -> list[str]:
    """EXPLAIN de `consulta` como kallicode_app (RLS activo) con los índices competidores retirados.

    Con las tablas casi vacías del CI el planificador empata entre varios índices (por ejemplo,
    una restricción única `(ticket_id, corrida)` que no empieza por tenant_id) y su elección sería
    arbitraria. Para que la prueba sea determinista, en una transacción que SIEMPRE se revierte se
    retiran los índices y restricciones únicas de la tabla que no empiezan por tenant_id (salvo la
    PRIMARY KEY y los aceptables). Lo que se comprueba es que el índice tenant-first SIRVE a la
    consulta: su condición lleva tenant_id y el filtro de RLS no se aplica después. La elección del
    planificador con los índices competidores presentes y volumen real se documenta en la comparativa
    de docs/plan-indices.md (sección 8.3).
    """
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS (superusuario) no definida")
    try:
        conn = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname, k.conname
                  FROM pg_index i
                  JOIN pg_class c ON c.oid = i.indexrelid
                  LEFT JOIN pg_constraint k ON k.conindid = i.indexrelid
                 WHERE i.indrelid = %s::regclass
                   AND NOT i.indisprimary
                   AND c.relname <> ALL(%s)
                   AND coalesce((SELECT a.attname FROM pg_attribute a
                                  WHERE a.attrelid = i.indrelid
                                    AND a.attnum = (i.indkey::int2[])[0]), '<expresion>') <> 'tenant_id'
                """,
                (f"core.{tabla}", sorted(aceptables)),
            )
            for indice, restriccion in cur.fetchall():
                cur.execute("SAVEPOINT retirar")
                try:
                    if restriccion:
                        cur.execute(
                            sql.SQL("ALTER TABLE {}.{} DROP CONSTRAINT {}").format(
                                sql.Identifier("core"),
                                sql.Identifier(tabla),
                                sql.Identifier(restriccion),
                            )
                        )
                    else:
                        cur.execute(
                            sql.SQL("DROP INDEX {}.{}").format(
                                sql.Identifier("core"), sql.Identifier(indice)
                            )
                        )
                except psycopg.Error:
                    cur.execute(
                        "ROLLBACK TO SAVEPOINT retirar"
                    )  # p. ej. restricción referenciada por una FK
            cur.execute("SET LOCAL ROLE kallicode_app")
            cur.execute("SELECT set_config('app.tenant_id', 'org_conv_indices', true)")
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute("EXPLAIN " + consulta)
            plan = [fila[0] for fila in cur.fetchall()]
            # Tablas particionadas (KC-DB-07-1/07-2): el plan nombra el índice de cada partición
            # (usage_tokens_p20261001_..._idx); se sustituye por el índice declarado en el padre,
            # que es el que documenta el plan de índices y el que esta prueba espera.
            raices: dict[str, str] = {}
            for linea in plan:
                for nombre in re.findall(r"(?:using|on) (\S+)", linea):
                    if nombre not in raices:
                        cur.execute(
                            "SELECT pg_partition_root(to_regclass(%s))::text", (f"core.{nombre}",)
                        )
                        raiz = cur.fetchone()
                        raices[nombre] = raiz[0].split(".")[-1] if raiz and raiz[0] else nombre
            return [
                re.sub(
                    r"(?:using|on) (\S+)",
                    lambda m: m.group(0).replace(m.group(1), raices.get(m.group(1), m.group(1))),
                    linea,
                )
                for linea in plan
            ]
    finally:
        conn.rollback()  # nada de lo anterior persiste
        conn.close()


@pytest.mark.parametrize(("cid", "tabla", "aceptables", "consulta"), DIEZ, ids=[d[0] for d in DIEZ])
def test_la_consulta_usa_un_indice_tenant_first_y_no_un_filtro_posterior(
    cid: str, tabla: str, aceptables: set[str], consulta: str
) -> None:
    plan = _explain_con_rls_sin_competidores(tabla, aceptables, consulta)
    texto = "\n".join(plan)

    usados = set(
        re.findall(
            r"(?:Index(?: Only)? Scan(?: Backward)?|Bitmap Index Scan) (?:using|on) (\S+)", texto
        )
    )
    assert usados & aceptables, (
        f"{cid}: usa {sorted(usados)}; se esperaba alguno de {sorted(aceptables)}\n{texto}"
    )

    condiciones = [linea.strip() for linea in plan if linea.strip().startswith("Index Cond:")]
    assert any("tenant_id" in c for c in condiciones), (
        f"{cid}: la condición de tenant no está en la condición del índice\n{texto}"
    )
    post = [
        d for d in _detalles_del_nodo(plan, tabla) if d.startswith("Filter:") and "tenant_id" in d
    ]
    assert not post, (
        f"{cid}: el filtro de tenant se aplica DESPUÉS de leer {tabla}: {post}\n{texto}"
    )
