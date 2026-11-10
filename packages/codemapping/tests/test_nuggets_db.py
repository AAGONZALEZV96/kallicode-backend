"""cm.nuggets con curación humana obligatoria contra PostgreSQL 16 + pgvector reales (KC-CM-08-2).

Requiere `make up && make db` con db/migrations/041_nuggets_curacion.sql aplicada. Todo lo que
intenta un atajo corre como kallicode_app (sin BYPASSRLS), igual que la aplicación; el usuario
administrador solo prepara y limpia los datos.

Criterios:
- Ningún nugget llega a 'aprobado' sin una resolución humana registrada: se intentan la inserción
  directa, el UPDATE directo, una resolución ajena y la reapertura, y todas fallan.
- Cada nugget cita una evidencia resoluble: FK compuesta a vec.doc_chunks (inexistente, de otro
  tenant) y cascada cuando la evidencia desaparece.
- El límite de longitud declarado (280) lo impone también la base.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import nuggets as ng
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from psycopg import errors

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT = "org_test_nuggets"
OTRO_TENANT = "org_test_nuggets_b"
CURADORA = "usr_test_nuggets_curadora"
OTRA_PERSONA = "usr_test_nuggets_otra"
AJENO = "usr_test_nuggets_ajeno"
DOC = "doc_test_nuggets"
DOC_OTRO = "doc_test_nuggets_b"
CHUNK_REGLAS = "chunk_test_nuggets_reglas"
CHUNK_ADR = "chunk_test_nuggets_adr"
CHUNK_OTRO = "chunk_test_nuggets_otro"
TEXTO_REGLAS = "Una factura en estado pagada no puede anularse; solo admite una nota de crédito."
TEXTO_ADR = "Los importes se almacenan en céntimos como enteros en todas las tablas de cobros."
VECTOR = "[" + ",".join(["0.001"] * 1024) + "]"


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = 'cm' AND table_name = 'nuggets' AND column_name = 'cita'"""
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 041 sin aplicar (make up && make db)"
)


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in (TENANT, OTRO_TENANT):
        conn.execute("DELETE FROM cm.nuggets WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM cm.curation_queue WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM vec.doc_chunks WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.onboarding_documents WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.users WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        for t in (TENANT, OTRO_TENANT):
            conn.execute(
                "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, f"prueba {t}")
            )
        for usuario, tenant in ((CURADORA, TENANT), (OTRA_PERSONA, TENANT), (AJENO, OTRO_TENANT)):
            conn.execute(
                "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
                (usuario, tenant, f"{usuario}@prueba.dev"),
            )
        for doc, tenant, subido_por in ((DOC, TENANT, CURADORA), (DOC_OTRO, OTRO_TENANT, AJENO)):
            conn.execute(
                """INSERT INTO core.onboarding_documents
                       (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref,
                        subido_por, estado, chunks_count, procesado_en)
                   VALUES (%s, %s, 'reglas.md', 'text/markdown', 10, %s, %s, 'procesado', 1, now())""",
                (doc, tenant, f"{tenant}/docs/{doc}/reglas.md", subido_por),
            )
        for chunk, tenant, doc, idx, seccion, texto in (
            (CHUNK_REGLAS, TENANT, DOC, 0, "Facturación > Anulaciones", TEXTO_REGLAS),
            (CHUNK_ADR, TENANT, DOC, 1, "ADR-021 > Decisión", TEXTO_ADR),
            (CHUNK_OTRO, OTRO_TENANT, DOC_OTRO, 0, "Otro", TEXTO_REGLAS),
        ):
            conn.execute(
                """INSERT INTO vec.doc_chunks
                       (id, tenant_id, document_id, chunk_index, content, embedding, seccion,
                        texto_hash)
                   VALUES (%s, %s, %s, %s, %s, %s::vector, %s, 'h')""",
                (chunk, tenant, doc, idx, texto, VECTOR, seccion),
            )
        yield conn
        _limpiar(conn)


@contextmanager
def _app(tenant: str = TENANT) -> Iterator[psycopg.Connection[Any]]:
    """Sesión de la aplicación (kallicode_app, RLS forzado) fijada a un tenant."""
    with psycopg.connect(APP_DSN, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        yield conn


def _proponer(admin: psycopg.Connection[Any]) -> list[ng.NuggetPropuesto]:
    """Camino real: chunks del repositorio -> validación de la salida -> guardar como propuesto."""
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        chunks = loader.obtener_chunks_para_nuggets([DOC])
        respuesta = {
            "nuggets": [
                {
                    "rule": "Una factura pagada no puede anularse.",
                    "kind": "regla_negocio",
                    "evidence": {"chunk_id": CHUNK_REGLAS, "quote": "no puede anularse"},
                    "scope_refs": ["Invoice"],
                },
                {
                    "rule": "Los importes se guardan en céntimos enteros.",
                    "kind": "restriccion_tecnica",
                    "evidence": {"chunk_id": CHUNK_ADR, "quote": "en céntimos como enteros"},
                    "scope_refs": [],
                },
            ]
        }
        resultado = ng.validar_salida_nuggets(respuesta, chunks, TENANT)
        assert len(resultado.propuestos) == 2, resultado.descartados
        assert loader.guardar_nuggets_propuestos(resultado.propuestos) == 2
    return resultado.propuestos


def _fila(admin: psycopg.Connection[Any], nugget_id: str) -> dict[str, Any]:
    cur = admin.execute(
        "SELECT estado, resuelto_por, resuelto_en, regla, motivo_resolucion "
        "FROM cm.nuggets WHERE tenant_id = %s AND id = %s",
        (TENANT, nugget_id),
    )
    fila = cur.fetchone()
    assert fila is not None
    return dict(
        zip(("estado", "resuelto_por", "resuelto_en", "regla", "motivo"), fila, strict=True)
    )


def _revisiones(admin: psycopg.Connection[Any], nugget_id: str) -> list[tuple[Any, ...]]:
    return admin.execute(
        """SELECT estado, revisado_por, revisado_en FROM cm.curation_queue
            WHERE tenant_id = %s AND tipo = 'nugget' AND payload ->> 'nugget_id' = %s
            ORDER BY creado_en""",
        (TENANT, nugget_id),
    ).fetchall()


def _resolver(conn: psycopg.Connection[Any], nugget_id: str, usuario: str, decision: str,
              motivo: str | None = None, regla: str | None = None) -> None:  # fmt: skip
    conn.execute(
        "SELECT * FROM cm.resolver_nugget(%s, %s, %s, %s, %s)",
        (nugget_id, usuario, decision, motivo, regla),
    )


# ---------------------------------------------------------------------------
# Propuesta: nace 'propuesto' con su revisión pendiente
# ---------------------------------------------------------------------------
def test_propuesta_queda_pendiente_con_seccion_de_origen_y_es_idempotente(
    admin: psycopg.Connection[Any],
) -> None:
    propuestos = _proponer(admin)
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        assert loader.guardar_nuggets_propuestos(propuestos) == 0  # reprocesar no duplica

    filas = admin.execute(
        """SELECT n.id, n.estado, n.resuelto_por, n.seccion, n.cita, n.tipo, n.scope_refs
             FROM cm.nuggets n WHERE n.tenant_id = %s ORDER BY n.evidencia_ref DESC""",
        (TENANT,),
    ).fetchall()
    assert [(f[1], f[2], f[3], f[5]) for f in filas] == [
        ("propuesto", None, "Facturación > Anulaciones", "regla_negocio"),
        ("propuesto", None, "ADR-021 > Decisión", "restriccion_tecnica"),
    ]
    assert filas[0][6] == ["Invoice"]
    for nugget_id, *_ in filas:
        assert _revisiones(admin, nugget_id) == [("pendiente", None, None)]


# ---------------------------------------------------------------------------
# Criterio 1: ningún atajo llega a 'aprobado'
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("con_resolutor", [False, True], ids=["sin-resolutor", "resolutor-falso"])
def test_insercion_directa_como_aprobado_falla(
    admin: psycopg.Connection[Any], con_resolutor: bool
) -> None:
    resuelto_por, resuelto_en = (CURADORA, datetime.now(UTC)) if con_resolutor else (None, None)
    with _app() as app, pytest.raises(errors.CheckViolation, match="nace propuesto"):
        app.execute(
            """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, evidencia_ref, estado,
                                       resuelto_por, resuelto_en)
               VALUES ('ng_atajo', %s, 'Regla colada', 'regla_negocio', %s, 'aprobado',
                       %s::text, %s::timestamptz)""",
            (TENANT, CHUNK_REGLAS, resuelto_por, resuelto_en),
        )
    assert admin.execute("SELECT count(*) FROM cm.nuggets WHERE id = 'ng_atajo'").fetchone() == (0,)


@pytest.mark.parametrize(
    "asignacion",
    [
        "estado = 'aprobado'",
        "estado = 'aprobado', resuelto_por = 'usr_test_nuggets_curadora', resuelto_en = now()",
    ],
    ids=["solo-estado", "con-resolutor-inventado"],
)
def test_update_directo_a_aprobado_sin_resolucion_registrada_falla(
    admin: psycopg.Connection[Any], asignacion: str
) -> None:
    nugget = _proponer(admin)[0]
    with _app() as app, pytest.raises(errors.CheckViolation):
        app.execute(
            f"UPDATE cm.nuggets SET {asignacion} WHERE tenant_id = %s AND id = %s",
            (TENANT, nugget.id),
        )
    assert _fila(admin, nugget.id)["estado"] == "propuesto"


def test_resolucion_registrada_por_otra_persona_no_sirve_de_atajo(
    admin: psycopg.Connection[Any],
) -> None:
    """La cola dice que aprobó la curadora; el nugget no puede atribuírselo a otra persona."""
    nugget = _proponer(admin)[0]
    with _app() as app:
        app.execute(
            """UPDATE cm.curation_queue
                  SET estado = 'aprobado', revisado_por = %s, revisado_en = '2026-11-08T10:00Z'
                WHERE tenant_id = %s AND tipo = 'nugget' AND payload ->> 'nugget_id' = %s""",
            (CURADORA, TENANT, nugget.id),
        )
        for resuelto_por, resuelto_en in (
            (OTRA_PERSONA, "2026-11-08T10:00Z"),  # otra persona
            (CURADORA, "2026-11-08T11:00Z"),  # otro instante: no es ESA resolución
        ):
            with pytest.raises(errors.CheckViolation, match="sin resolución humana"):
                app.execute(
                    """UPDATE cm.nuggets SET estado = 'aprobado', resuelto_por = %s,
                              resuelto_en = %s WHERE tenant_id = %s AND id = %s""",
                    (resuelto_por, resuelto_en, TENANT, nugget.id),
                )
    assert _fila(admin, nugget.id)["estado"] == "propuesto"


def test_revision_de_nugget_resuelta_sin_revisor_falla(admin: psycopg.Connection[Any]) -> None:
    nugget = _proponer(admin)[0]
    with _app() as app, pytest.raises(errors.CheckViolation, match="curation_nugget_revision_chk"):
        app.execute(
            """UPDATE cm.curation_queue SET estado = 'aprobado'
                WHERE tenant_id = %s AND tipo = 'nugget' AND payload ->> 'nugget_id' = %s""",
            (TENANT, nugget.id),
        )


def test_resolver_nugget_aprueba_y_deja_la_resolucion_registrada(
    admin: psycopg.Connection[Any],
) -> None:
    nugget = _proponer(admin)[0]
    with _app() as app:
        _resolver(app, nugget.id, CURADORA, "aprobado", "Coincide con el manual",
                  regla="Una factura pagada no se anula: se emite nota de crédito.")  # fmt: skip

    fila = _fila(admin, nugget.id)
    assert fila["estado"] == "aprobado"
    assert fila["resuelto_por"] == CURADORA
    assert fila["motivo"] == "Coincide con el manual"
    assert fila["regla"] == "Una factura pagada no se anula: se emite nota de crédito."
    assert _revisiones(admin, nugget.id) == [("aprobado", CURADORA, fila["resuelto_en"])]
    payload = admin.execute(
        "SELECT payload FROM cm.curation_queue WHERE tenant_id = %s AND payload ->> 'nugget_id' = %s",
        (TENANT, nugget.id),
    ).fetchone()
    assert payload is not None and payload[0]["regla_final"] == fila["regla"]


def test_resuelto_es_inmutable_y_no_se_resuelve_dos_veces(admin: psycopg.Connection[Any]) -> None:
    aprobado, rechazado = _proponer(admin)
    with _app() as app:
        _resolver(app, aprobado.id, CURADORA, "aprobado")
        _resolver(app, rechazado.id, CURADORA, "rechazado", "Regla de otro producto")
        for asignacion in (
            "regla = 'Regla cambiada tras aprobar'",
            "estado = 'propuesto', resuelto_por = NULL, resuelto_en = NULL",
        ):
            with pytest.raises(errors.CheckViolation, match="ya fue resuelto"):
                app.execute(
                    f"UPDATE cm.nuggets SET {asignacion} WHERE tenant_id = %s AND id = %s",
                    (TENANT, aprobado.id),
                )
        with pytest.raises(errors.NoDataFound, match="revisión pendiente"):
            _resolver(app, rechazado.id, OTRA_PERSONA, "aprobado")

    assert _fila(admin, aprobado.id)["estado"] == "aprobado"
    assert _fila(admin, rechazado.id)["estado"] == "rechazado"


@pytest.mark.parametrize(
    ("usuario", "decision", "error"),
    [
        (CURADORA, "propuesto", errors.InvalidParameterValue),
        (CURADORA, "talvez", errors.InvalidParameterValue),
        ("", "aprobado", errors.InvalidParameterValue),
        (AJENO, "aprobado", errors.ForeignKeyViolation),  # usuario de otro tenant
        ("usr_inexistente", "aprobado", errors.ForeignKeyViolation),
    ],
    ids=["propuesto", "decision-invalida", "sin-usuario", "usuario-de-otro-tenant", "inexistente"],
)
def test_resolver_exige_decision_valida_y_usuario_del_tenant(
    admin: psycopg.Connection[Any], usuario: str, decision: str, error: type[Exception]
) -> None:
    nugget = _proponer(admin)[0]
    with _app() as app, pytest.raises(error):
        _resolver(app, nugget.id, usuario, decision)
    assert _fila(admin, nugget.id)["estado"] == "propuesto"
    assert _revisiones(admin, nugget.id) == [("pendiente", None, None)]


def test_otro_tenant_no_ve_ni_resuelve_los_nuggets(admin: psycopg.Connection[Any]) -> None:
    nugget = _proponer(admin)[0]
    with _app(OTRO_TENANT) as app:
        assert app.execute("SELECT count(*) FROM cm.nuggets").fetchone() == (0,)
        with pytest.raises(errors.NoDataFound):
            _resolver(app, nugget.id, AJENO, "aprobado")
    assert _fila(admin, nugget.id)["estado"] == "propuesto"


# ---------------------------------------------------------------------------
# Criterio 2: evidencia resoluble (integridad referencial sobre evidencia_ref)
# ---------------------------------------------------------------------------
def test_todo_nugget_resuelve_a_su_chunk_y_seccion(admin: psycopg.Connection[Any]) -> None:
    _proponer(admin)
    huerfanos = admin.execute(
        """SELECT count(*) FROM cm.nuggets n
             LEFT JOIN vec.doc_chunks c ON c.tenant_id = n.tenant_id AND c.id = n.evidencia_ref
            WHERE n.tenant_id = %s
              AND (c.id IS NULL OR c.seccion <> n.seccion OR position(n.cita IN c.content) = 0)""",
        (TENANT,),
    ).fetchone()
    assert huerfanos == (0,)


@pytest.mark.parametrize(
    "evidencia", ["chunk_inexistente", CHUNK_OTRO], ids=["inexistente", "de-otro-tenant"]
)
def test_nugget_con_evidencia_no_resoluble_se_rechaza(
    admin: psycopg.Connection[Any], evidencia: str
) -> None:
    with _app() as app, pytest.raises(errors.ForeignKeyViolation, match="evidencia_ref"):
        app.execute(
            """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, evidencia_ref)
               VALUES ('ng_sin_evidencia', %s, 'Regla sin respaldo', 'regla_negocio', %s)""",
            (TENANT, evidencia),
        )


def test_sin_evidencia_el_nugget_cae_y_su_revision_pendiente_tambien(
    admin: psycopg.Connection[Any],
) -> None:
    reglas, adr = _proponer(admin)
    with _app() as app:
        _resolver(app, adr.id, CURADORA, "aprobado")

    admin.execute("DELETE FROM vec.doc_chunks WHERE tenant_id = %s", (TENANT,))

    assert admin.execute(
        "SELECT count(*) FROM cm.nuggets WHERE tenant_id = %s", (TENANT,)
    ).fetchone() == (0,)
    assert _revisiones(admin, reglas.id) == []  # la pendiente ya no tiene qué resolver
    assert [r[0] for r in _revisiones(admin, adr.id)] == ["aprobado"]  # historial


# ---------------------------------------------------------------------------
# Criterio 4: límite de longitud declarado
# ---------------------------------------------------------------------------
def test_limite_de_longitud_lo_impone_la_base(admin: psycopg.Connection[Any]) -> None:
    assert admin.execute("SELECT cm.nugget_regla_max_chars()").fetchone() == (ng.REGLA_MAX_CHARS,)
    nugget = _proponer(admin)[0]
    with _app() as app:
        for regla in ("x" * (ng.REGLA_MAX_CHARS + 1), "   "):
            with pytest.raises(errors.CheckViolation, match="nuggets_regla_len_chk"):
                app.execute(
                    """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, evidencia_ref)
                       VALUES ('ng_largo', %s, %s, 'regla_negocio', %s)""",
                    (TENANT, regla, CHUNK_REGLAS),
                )
        app.execute(
            """INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, evidencia_ref)
               VALUES ('ng_justo', %s, %s, 'regla_negocio', %s)""",
            (TENANT, "x" * ng.REGLA_MAX_CHARS, CHUNK_REGLAS),
        )
        # Ni siquiera el curador puede aprobar una redacción que se pase del límite.
        with pytest.raises(errors.CheckViolation, match="nuggets_regla_len_chk"):
            _resolver(app, nugget.id, CURADORA, "aprobado", regla="y" * (ng.REGLA_MAX_CHARS + 1))
    assert _fila(admin, nugget.id)["estado"] == "propuesto"
