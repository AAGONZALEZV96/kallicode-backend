"""Refresco, purga y presupuesto documental contra PostgreSQL real (KC-CM-08-5 / KC-CM-08-6).

Requiere `make up && make db` con db/migrations/045_refresco_presupuesto_docs.sql aplicada. Corre como
kallicode_app (RLS forzada), igual que en producción. Las pruebas equivalentes sin base están en
test/test_refresco_presupuesto_docs.py.

Cubre el criterio "eliminar un documento purga sus chunks y todos sus enlaces al grafo en la misma
transacción": la purga se prueba feliz y con un fallo a mitad de camino (no queda nada a medias).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from kallicode_codemapping.budget import ControlPresupuestoDocs
from kallicode_codemapping.fase_documental import registrar_resultado_documental
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.ingest_docs import (
    IngestaDocError,
    procesar_documentos_pendientes,
    refrescar_documentos,
)

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT = "org_test_refresco_docs"
USUARIO = "usr_test_refresco_docs"
SNAPSHOT = "snap_it_refresco"
DOC = "doc_it_refresco"
BLOB = f"{TENANT}/docs/{DOC}/manual.md"
CONTENIDO = (
    "# Manual\n\n## Factura\n\n"
    + "La Factura se emite al cerrar el cobro. " * 8
    + "\n\n## Cobro\n\n"
    + "El Cobro se concilia con el banco cada noche. " * 8
    + "\n"
).encode()


def _migracion_lista() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE (table_schema, table_name, column_name) IN
                          (('core', 'onboarding_documents', 'eliminado_en'),
                           ('core', 'usage_tokens', 'origen'))"""
            ).fetchone()
            if not (fila and fila[0] == 2):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _migracion_lista(), reason="Postgres no disponible o 045 sin aplicar (make up && make db)"
)


class TEIFalso:
    def __init__(self) -> None:
        self.llamadas = 0

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.llamadas += 1
        return [
            [(hashlib.sha256(t.encode()).digest()[i % 32] + 1) / 256.0 for i in range(1024)]
            for t in texts
        ]


class LectorFalso:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def leer(self, blob_ref: str) -> bytes:
        if blob_ref not in self.blobs:
            raise IngestaDocError("BLOB_NO_DISPONIBLE", blob_ref)
        return self.blobs[blob_ref]


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    conn.execute("DROP TRIGGER IF EXISTS kc_it_falla_purga ON core.onboarding_documents")
    conn.execute("DROP FUNCTION IF EXISTS core.kc_it_falla_purga()")
    conn.execute("DELETE FROM core.usage_tokens WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM cm.doc_chunk_links WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM vec.doc_chunks WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.onboarding_documents WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM cm.symbols WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM cm.files WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM cm.repo_snapshots WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.users WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.organizations WHERE id = %s", (TENANT,))


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'prueba refresco')", (TENANT,)
        )
        conn.execute(
            "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
            (USUARIO, TENANT, "refresco-docs@prueba.dev"),
        )
        conn.execute(
            """INSERT INTO core.onboarding_documents
                   (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref, subido_por)
               VALUES (%s, %s, 'manual.md', 'text/markdown', %s, %s, %s)""",
            (DOC, TENANT, len(CONTENIDO), BLOB, USUARIO),
        )
        conn.execute(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado) "
            "VALUES (%s, %s, 'sha', 'main', 'completo')",
            (SNAPSHOT, TENANT),
        )
        conn.execute(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES ('file_it_refresco', %s, 'src/billing.py', 'python', 'x', %s)",
            (TENANT, SNAPSHOT),
        )
        for i, nombre in enumerate(("Factura", "Cobro")):
            conn.execute(
                "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line,"
                " signature) VALUES (%s, %s, 'file_it_refresco', 'function', %s, %s, %s, %s)",
                (f"sym_it_ref_{nombre}", TENANT, nombre, i + 1, i + 1, f"def {nombre}()"),
            )
        yield conn
        _limpiar(conn)


def _contar(conn: psycopg.Connection[Any], sql: str) -> int:
    fila = conn.execute(sql, (TENANT,)).fetchone()
    return int(fila[0]) if fila else 0


def _ingerir(lector: LectorFalso) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        resultados = procesar_documentos_pendientes(loader, lector, TEIFalso(), TENANT)  # type: ignore[arg-type]
    assert [r.estado for r in resultados] == ["procesado"], [r.motivo for r in resultados]


CHUNKS = "SELECT count(*) FROM vec.doc_chunks WHERE tenant_id = %s"
ENLACES = "SELECT count(*) FROM cm.doc_chunk_links WHERE tenant_id = %s"


def test_eliminar_un_documento_purga_chunks_y_enlaces_en_una_transaccion(
    admin: psycopg.Connection[Any],
) -> None:
    lector = LectorFalso({BLOB: CONTENIDO})
    _ingerir(lector)
    assert _contar(admin, CHUNKS) >= 2 and _contar(admin, ENLACES) >= 1

    admin.execute(
        "UPDATE core.onboarding_documents SET eliminado_en = now() WHERE tenant_id = %s", (TENANT,)
    )
    tei = TEIFalso()
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        resumen = refrescar_documentos(
            loader,
            lector,
            tei,
            TENANT,
            control=ControlPresupuestoDocs(tokens_max=10**6),  # type: ignore[arg-type]
        )

    assert resumen.purgados == 1 and tei.llamadas == 0
    assert _contar(admin, CHUNKS) == 0 and _contar(admin, ENLACES) == 0
    hash_doc = admin.execute(
        "SELECT hash_contenido FROM core.onboarding_documents WHERE id = %s", (DOC,)
    ).fetchone()
    assert hash_doc is not None and hash_doc[0] is None, "el hash queda libre para volver a subirse"


def test_la_purga_es_atomica_si_algo_falla_no_queda_nada_a_medias(
    admin: psycopg.Connection[Any],
) -> None:
    lector = LectorFalso({BLOB: CONTENIDO})
    _ingerir(lector)
    chunks, enlaces = _contar(admin, CHUNKS), _contar(admin, ENLACES)
    admin.execute(
        "UPDATE core.onboarding_documents SET eliminado_en = now() WHERE tenant_id = %s", (TENANT,)
    )
    # El UPDATE del documento (segundo paso de la purga) revienta: el DELETE de chunks debe revertirse.
    admin.execute(
        """CREATE FUNCTION core.kc_it_falla_purga() RETURNS trigger LANGUAGE plpgsql AS $$
           BEGIN
               IF NEW.chunks_count = 0 AND OLD.chunks_count > 0 THEN
                   RAISE EXCEPTION 'falla simulada de la purga';
               END IF;
               RETURN NEW;
           END $$"""
    )
    admin.execute(
        """CREATE TRIGGER kc_it_falla_purga BEFORE UPDATE ON core.onboarding_documents
           FOR EACH ROW EXECUTE FUNCTION core.kc_it_falla_purga()"""
    )

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader, pytest.raises(psycopg.Error):
        loader.purgar_documento(DOC)

    assert (_contar(admin, CHUNKS), _contar(admin, ENLACES)) == (chunks, enlaces)


def test_mismo_hash_no_vuelve_a_embeber_y_las_stats_quedan_en_el_snapshot(
    admin: psycopg.Connection[Any],
) -> None:
    lector = LectorFalso({BLOB: CONTENIDO})
    _ingerir(lector)

    tei = TEIFalso()
    control = ControlPresupuestoDocs(tokens_max=10**6)
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        resumen = refrescar_documentos(loader, lector, tei, TENANT, control=control)  # type: ignore[arg-type]
        registrar_resultado_documental(loader, SNAPSHOT, control, resumen)

    assert tei.llamadas == 0
    fila = admin.execute(
        "SELECT stats -> 'documentos' FROM cm.repo_snapshots WHERE id = %s", (SNAPSHOT,)
    ).fetchone()
    assert fila is not None
    docs = fila[0]
    assert (docs["procesados"], docs["omitidos"], docs["fallidos"]) == (0, 1, 0)
    assert docs["presupuesto"]["presupuesto_documental_excedido"] is False


def test_consumo_documental_se_registra_aparte_sin_ticket(admin: psycopg.Connection[Any]) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        assert loader.registrar_consumo_documental(SNAPSHOT, 1234, 2, 80) is True

    fila = admin.execute(
        """SELECT ticket_id, origen, etapa, tokens_in, tokens_out, invocaciones, snapshot_id
             FROM core.usage_tokens WHERE tenant_id = %s""",
        (TENANT,),
    ).fetchone()
    assert fila == (None, "codemapping_docs", "ingesta_docs", 1234, 0, 2, SNAPSHOT)


def test_el_pipeline_sigue_exigiendo_ticket(admin: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            """INSERT INTO core.usage_tokens
                   (tenant_id, ticket_id, etapa, modelo, tier, tokens_in, tokens_out, duracion_ms)
               VALUES (%s, NULL, 'analisis', 'm', 'flash', 1, 1, 1)""",
            (TENANT,),
        )
