"""Append-only y congelación de las tablas probatorias (KC-DB-05-2).

Requiere db/migrations/025 aplicada. Las seis tablas append-only pierden
UPDATE/DELETE para la aplicación y además las protege un trigger (también frente
a un rol privilegiado). security_findings solo admite la transición de estado y
de veredicto, con justificación. Las correcciones se hacen por compensación.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion

APPEND_ONLY = (
    "gate_signatures",
    "llm_steps",
    "job_transitions",
    "qa_runs",
    "qa_results",
    "usage_loc",
)
SIETE = (*APPEND_ONLY, "security_findings")


@dataclass
class Probatorio:
    tenant: str
    ticket: str
    job: str
    spec: str
    run: str
    finding: str


async def _sembrar(admin: AsyncSession, t: str) -> Probatorio:
    """Una fila en cada una de las siete tablas, dentro de un tenant propio."""
    sfx = uuid.uuid4().hex[:8]
    user, ticket, job, spec = f"us_{sfx}", f"tk_{sfx}", f"jb_{sfx}", f"sp_{sfx}"
    run, finding = f"qr_{sfx}", f"sf_{sfx}"
    p = {"t": t, "u": user, "tk": ticket, "jb": job, "sp": spec, "qr": run, "sf": finding}
    await admin.execute(
        text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba probatorias')"),
        p,
    )
    await admin.execute(
        text("INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (:u, :t, :u, 'U')"), p
    )
    await admin.execute(
        text(
            "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) "
            "VALUES (:tk, :t, 'bug', 'T', 'D')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.jobs (id, tenant_id, ticket_id, prioridad) VALUES (:jb, :t, :tk, 'P3')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref, scope_paths) "
            "VALUES (:sp, :t, :tk, 1, 'blob/spec.md', ARRAY['src/'])"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.gate_signatures (id, tenant_id, ticket_id, gate, accion, actor_id, sello) "
            "VALUES (:sf || 'g', :t, :tk, 1, 'aprobado', :u, 'sello')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.llm_steps (id, tenant_id, job_id, ticket_id, etapa, paso, "
            "version_plantilla, modelo, tier, validacion, tokens_in, tokens_out, duracion_ms, "
            "entrada_ref, salida_ref) VALUES (:sf || 'l', :t, :jb, :tk, 'triage', 'triage.classify', "
            "'v1', 'm', 'flash', 'esquema_valido', 1, 1, 1, 'e', 's')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.job_transitions (tenant_id, job_id, de_estado, a_estado) "
            "VALUES (:t, :jb, 'pendiente_asignacion', 'asignado')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion) "
            "VALUES (:qr, :t, :tk, :sp, 1, CAST('{\"dirigidos\": 1}' AS jsonb))"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.qa_results (qa_run_id, tenant_id, criterio_id, resultado) "
            "VALUES (:qr, :t, 'AC-1', 'pasa')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.security_findings (id, tenant_id, ticket_id, origen, regla, archivo, "
            "linea, severidad, titulo, veredicto) VALUES (:sf, :t, :tk, 'sast', 'R-1', 'a.py', 10, "
            "'alta', 'Titulo', 'confirmado')"
        ),
        p,
    )
    await admin.execute(
        text(
            "INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, pr_url, anadidas, "
            "eliminadas, netas) VALUES (:t, :tk, '2026-10', 'pr', 'https://git/pr/' || :sf, 50, 0, 50)"
        ),
        p,
    )
    return Probatorio(t, ticket, job, spec, run, finding)


@pytest.fixture
async def probatorio() -> AsyncIterator[Probatorio]:
    t = f"org_pro_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        datos = await _sembrar(admin, t)
    try:
        yield datos
    finally:
        async with db.sesion_sistema() as admin:
            # usage_loc sobrevive al borrado del ticket (ON DELETE SET NULL): es append-only,
            # así que la limpieza de pruebas desactiva su trigger a propósito.
            await admin.execute(
                text("ALTER TABLE core.usage_loc DISABLE TRIGGER trg_probatoria_append_only")
            )
            await admin.execute(text("DELETE FROM core.usage_loc WHERE tenant_id = :t"), {"t": t})
            await admin.execute(
                text("ALTER TABLE core.usage_loc ENABLE TRIGGER trg_probatoria_append_only")
            )
            await admin.execute(
                text("DELETE FROM core.usage_cycles WHERE tenant_id = :t"), {"t": t}
            )
            await admin.execute(text("DELETE FROM core.tickets WHERE tenant_id = :t"), {"t": t})
            await admin.execute(
                text("DELETE FROM core.ticket_counters WHERE tenant_id = :t"), {"t": t}
            )
            await admin.execute(text("DELETE FROM core.users WHERE tenant_id = :t"), {"t": t})
            await admin.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": t})


# Una mutación prohibida por tabla: (tabla, sentencia, mensaje del trigger).
_MUTACIONES = [
    (
        "gate_signatures",
        "UPDATE core.gate_signatures SET accion = 'cambios_pedidos'",
        "append-only",
    ),
    ("llm_steps", "UPDATE core.llm_steps SET modelo = 'otro'", "append-only"),
    ("job_transitions", "DELETE FROM core.job_transitions", "append-only"),
    ("qa_runs", "UPDATE core.qa_runs SET regresion = CAST('{}' AS jsonb)", "append-only"),
    (
        "qa_results",
        "UPDATE core.qa_results SET resultado = 'falla', extracto_fallo = 'x'",
        "append-only",
    ),
    ("usage_loc", "UPDATE core.usage_loc SET netas = 0, anadidas = 0", "append-only"),
    ("security_findings", "UPDATE core.security_findings SET severidad = 'baja'", "solo se admite"),
]


def test_hay_una_mutacion_prohibida_por_cada_una_de_las_siete_tablas() -> None:
    assert sorted(m[0] for m in _MUTACIONES) == sorted(SIETE)


# --- criterio 1: la aplicación pierde UPDATE y DELETE ---------------------------
@pytest.mark.parametrize(("tabla", "sentencia", "mensaje"), _MUTACIONES)
async def test_la_aplicacion_no_puede_mutar_las_tablas_probatorias(
    probatorio: Probatorio, tabla: str, sentencia: str, mensaje: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db.sesion_tenant(probatorio.tenant) as s:
            await s.execute(text(sentencia))


@pytest.mark.parametrize("tabla", APPEND_ONLY)
async def test_kallicode_app_no_tiene_update_ni_delete(tabla: str) -> None:
    async with db.sesion_sistema() as admin:
        privilegios: dict[str, bool] = {
            p: (
                await admin.execute(
                    text("SELECT has_table_privilege('kallicode_app', :tb, :p)"),
                    {"tb": f"core.{tabla}", "p": p},
                )
            ).scalar_one()
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE")
        }
    assert privilegios == {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False}


async def test_kallicode_app_solo_actualiza_estado_y_veredicto_de_los_hallazgos() -> None:
    async with db.sesion_sistema() as admin:
        columnas: dict[str, bool] = {
            c: (
                await admin.execute(
                    text(
                        "SELECT has_column_privilege('kallicode_app', 'core.security_findings', :c, 'UPDATE')"
                    ),
                    {"c": c},
                )
            ).scalar_one()
            for c in (
                "estado",
                "veredicto",
                "justificacion",
                "regla",
                "archivo",
                "linea",
                "severidad",
                "titulo",
            )
        }
        borrar: bool = (
            await admin.execute(
                text(
                    "SELECT has_table_privilege('kallicode_app', 'core.security_findings', 'DELETE')"
                )
            )
        ).scalar_one()
    assert [c for c, ok in columnas.items() if ok] == ["estado", "veredicto", "justificacion"]
    assert borrar is False


# --- criterio 1 (cont.): el trigger protege también a nivel de motor ----------
@pytest.mark.parametrize(("tabla", "sentencia", "mensaje"), _MUTACIONES)
async def test_el_trigger_bloquea_la_mutacion_incluso_a_un_rol_privilegiado(
    probatorio: Probatorio, tabla: str, sentencia: str, mensaje: str
) -> None:
    with pytest.raises(DBAPIError, match=mensaje):
        async with db.sesion_sistema() as admin:
            await admin.execute(text(f"{sentencia} WHERE tenant_id = :t"), {"t": probatorio.tenant})


async def test_borrar_directamente_un_hallazgo_esta_prohibido(probatorio: Probatorio) -> None:
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text("DELETE FROM core.security_findings WHERE id = :i"), {"i": probatorio.finding}
            )


# --- criterio 2: security_findings admite solo la transición ------------------
async def _estado(tenant: str, finding: str) -> tuple[str, str, str | None]:
    async with db.sesion_tenant(tenant) as s:
        fila = (
            await s.execute(
                text(
                    "SELECT estado, veredicto, justificacion FROM core.security_findings WHERE id = :i"
                ),
                {"i": finding},
            )
        ).one()
    return fila[0], fila[1], fila[2]


async def test_la_transicion_con_justificacion_se_admite(probatorio: Probatorio) -> None:
    async with db.sesion_tenant(probatorio.tenant) as s:
        await s.execute(
            text(
                "UPDATE core.security_findings SET estado = 'descartado', veredicto = 'falso_positivo', "
                "justificacion = 'El valor viene de una constante, no de entrada del usuario' "
                "WHERE id = :i"
            ),
            {"i": probatorio.finding},
        )
    estado, veredicto, justificacion = await _estado(probatorio.tenant, probatorio.finding)
    assert (estado, veredicto) == ("descartado", "falso_positivo")
    assert justificacion is not None


@pytest.mark.parametrize("justificacion", ["NULL", "''", "'   '"])
async def test_la_transicion_sin_justificacion_se_rechaza(
    probatorio: Probatorio, justificacion: str
) -> None:
    with pytest.raises(DBAPIError, match="exige justificacion|violates check constraint"):
        async with db.sesion_tenant(probatorio.tenant) as s:
            await s.execute(
                text(
                    "UPDATE core.security_findings SET estado = 'corregido', "
                    f"justificacion = {justificacion} WHERE id = :i"
                ),
                {"i": probatorio.finding},
            )
    assert (await _estado(probatorio.tenant, probatorio.finding))[0] == "abierto"


async def test_no_se_puede_reutilizar_la_justificacion_anterior(probatorio: Probatorio) -> None:
    async with db.sesion_tenant(probatorio.tenant) as s:
        await s.execute(
            text(
                "UPDATE core.security_findings SET estado = 'corregido', justificacion = 'Parche aplicado' "
                "WHERE id = :i"
            ),
            {"i": probatorio.finding},
        )
    with pytest.raises(DBAPIError, match="no repetir la anterior"):
        async with db.sesion_tenant(probatorio.tenant) as s:
            await s.execute(
                text(
                    "UPDATE core.security_findings SET estado = 'abierto', justificacion = 'Parche aplicado' "
                    "WHERE id = :i"
                ),
                {"i": probatorio.finding},
            )


@pytest.mark.parametrize(
    "asignacion",
    [
        "regla = 'R-2'",
        "archivo = 'b.py'",
        "linea = 99",
        "severidad = 'baja'",
        "titulo = 'Otro titulo'",
        "origen = 'sca'",
        "bloquea = true",
    ],
)
async def test_cambiar_regla_archivo_linea_severidad_o_titulo_se_rechaza(
    probatorio: Probatorio, asignacion: str
) -> None:
    # Un rol privilegiado tampoco puede: lo rechaza el trigger, no solo los permisos.
    with pytest.raises(DBAPIError, match="solo se admite la transicion"):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text(f"UPDATE core.security_findings SET {asignacion} WHERE id = :i"),
                {"i": probatorio.finding},
            )
    # Y la aplicación ni siquiera tiene permiso sobre esas columnas.
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db.sesion_tenant(probatorio.tenant) as s:
            await s.execute(
                text(f"UPDATE core.security_findings SET {asignacion} WHERE id = :i"),
                {"i": probatorio.finding},
            )


# --- las cascadas referenciales siguen funcionando -----------------------------
async def test_borrar_un_ticket_arrastra_a_sus_hijos_y_deja_usage_loc_sin_ticket(
    probatorio: Probatorio,
) -> None:
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("DELETE FROM core.tickets WHERE id = :tk"), {"tk": probatorio.ticket}
        )
        restantes: dict[str, int] = {
            tabla: (
                await admin.execute(
                    text(f"SELECT count(*) FROM core.{tabla} WHERE tenant_id = :t"),
                    {"t": probatorio.tenant},
                )
            ).scalar_one()
            for tabla in SIETE
        }
        sin_ticket: int = (
            await admin.execute(
                text(
                    "SELECT count(*) FROM core.usage_loc WHERE tenant_id = :t AND ticket_id IS NULL"
                ),
                {"t": probatorio.tenant},
            )
        ).scalar_one()
    assert restantes == {**dict.fromkeys(SIETE, 0), "usage_loc": 1}
    assert sin_ticket == 1


# --- criterio 4: corrección por compensación -----------------------------------
async def test_usage_loc_se_corrige_con_una_fila_de_ajuste_sin_tocar_la_original(
    probatorio: Probatorio,
) -> None:
    async with db.sesion_tenant(probatorio.tenant) as s:
        await s.execute(
            text(
                "INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, netas, motivo_ajuste) "
                "VALUES (core.current_tenant(), :tk, '2026-10', 'ajuste', -50, "
                "'Compensa el PR contado dos veces por un reintento del webhook')"
            ),
            {"tk": probatorio.ticket},
        )
    async with db.sesion_tenant(probatorio.tenant) as s:
        filas = (
            await s.execute(
                text("SELECT tipo, netas, motivo_ajuste FROM core.usage_loc ORDER BY id")
            )
        ).all()
    assert [(f[0], f[1]) for f in filas] == [("pr", 50), ("ajuste", -50)]  # la original intacta
    assert filas[1][2] is not None
    assert sum(f[1] for f in filas) == 0  # el consumo neto queda corregido


async def test_el_ajuste_exige_motivo(probatorio: Probatorio) -> None:
    with pytest.raises(DBAPIError, match="loc_ajuste_chk"):
        async with db.sesion_tenant(probatorio.tenant) as s:
            await s.execute(
                text(
                    "INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, netas) "
                    "VALUES (core.current_tenant(), :tk, '2026-10', 'ajuste', -5)"
                ),
                {"tk": probatorio.ticket},
            )


async def test_qa_runs_se_corrige_con_una_corrida_nueva_sin_reescribir_la_anterior(
    probatorio: Probatorio,
) -> None:
    async with db.sesion_tenant(probatorio.tenant) as s:
        await s.execute(
            text(
                "INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion) "
                "VALUES (:nuevo, core.current_tenant(), :tk, :sp, 2, "
                'CAST(\'{"dirigidos": 3, "dirigidos_ok": 3}\' AS jsonb))'
            ),
            {"nuevo": f"{probatorio.run}b", "tk": probatorio.ticket, "sp": probatorio.spec},
        )
        await s.execute(
            text(
                "INSERT INTO core.qa_results (qa_run_id, tenant_id, criterio_id, resultado) "
                "VALUES (:nuevo, core.current_tenant(), 'AC-1', 'pasa')"
            ),
            {"nuevo": f"{probatorio.run}b"},
        )
    async with db.sesion_tenant(probatorio.tenant) as s:
        corridas = (
            await s.execute(
                text("SELECT corrida, regresion->>'dirigidos' FROM core.qa_runs ORDER BY corrida")
            )
        ).all()
        vigente: int = (
            await s.execute(
                text("SELECT max(corrida) FROM core.qa_runs WHERE ticket_id = :tk"),
                {"tk": probatorio.ticket},
            )
        ).scalar_one()
    assert [(c[0], c[1]) for c in corridas] == [
        (1, "1"),
        (2, "3"),
    ]  # la corrida 1 queda como estaba
    assert vigente == 2
