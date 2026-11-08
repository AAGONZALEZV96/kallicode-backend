"""Ingesta documental contra PostgreSQL 16 + pgvector reales (KC-CM-08-1).

Requiere `make up && make db` (con db/migrations/040_ingesta_docs.sql aplicada). La ingesta corre
como kallicode_app (sin BYPASSRLS), igual que en producción, así que las políticas RLS de
vec.doc_chunks, cm.doc_chunk_links y core.onboarding_documents están activas.

Las pruebas unitarias equivalentes, sin base, están en test/test_ingest_docs.py.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from kallicode_codemapping.embeddings import id_doc_chunk
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn, vector_literal
from kallicode_codemapping.ingest_docs import (
    IngestaDocError,
    extraer_texto,
    procesar_documento,
    procesar_documentos_pendientes,
    trocear_documento_markdown,
)

# En CI, la base efímera de .github/workflows/ci.yml; en local, la de docker-compose.
ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
# Misma base, sesión con ROLE kallicode_app: RLS forzado como en producción.
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT = "org_test_ingesta_docs"
OTRO_TENANT = "org_test_ingesta_docs_b"
USUARIO = "usr_test_ingesta_docs"
DOCS = Path(__file__).resolve().parent / "fixtures" / "docs"

CT_PDF = "application/pdf"
CT_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FIXTURES = [
    ("doc_it_manual", "manual_facturacion.md", "text/markdown"),
    ("doc_it_spec", "spec_cobros.pdf", CT_PDF),
    ("doc_it_adr", "ADR-021-conciliacion.docx", CT_DOCX),
    ("doc_it_politicas", "politicas_negocio_escaneado.pdf", CT_PDF),
    ("doc_it_legado", "reglas_negocio_legado.txt", "text/plain"),
]
SIMBOLOS = [
    "BillingService", "calculate_total", "format_currency", "mark_as_paid", "Invoice",
    "InvoiceService", "Totals", "AuditTrail", "Factura", "Impuesto",
]  # fmt: skip


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                """SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = 'core' AND table_name = 'onboarding_documents'
                      AND column_name = 'motivo_fallo'"""
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 040 sin aplicar (make up && make db)"
)


class TEIFalso:
    def __init__(self) -> None:
        self.textos: list[str] = []

    @staticmethod
    def vector(texto: str) -> list[float]:
        semilla = hashlib.sha256(texto.encode("utf-8")).digest()
        return [(semilla[i % 32] + 1) / 256.0 for i in range(1024)]

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.textos.extend(texts)
        return [self.vector(t) for t in texts]


class LectorFalso:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def leer(self, blob_ref: str) -> bytes:
        if blob_ref not in self.blobs:
            raise IngestaDocError("BLOB_NO_DISPONIBLE", blob_ref)
        return self.blobs[blob_ref]


def _blob_ref(doc_id: str, archivo: str) -> str:
    return f"{TENANT}/docs/{doc_id}/{archivo}"


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in (TENANT, OTRO_TENANT):
        conn.execute("DELETE FROM cm.doc_chunk_links WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM vec.doc_chunks WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM vec.definition_embeddings WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.onboarding_documents WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM cm.symbols WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM cm.files WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM cm.repo_snapshots WHERE tenant_id = %s", (t,))
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
        conn.execute(
            "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U')",
            (USUARIO, TENANT, "ingesta-docs@prueba.dev"),
        )
        for doc_id, archivo, ct in FIXTURES:
            conn.execute(
                """INSERT INTO core.onboarding_documents
                       (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref, subido_por)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    doc_id,
                    TENANT,
                    archivo,
                    ct,
                    (DOCS / archivo).stat().st_size,
                    _blob_ref(doc_id, archivo),
                    USUARIO,
                ),
            )
        conn.execute(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
            "VALUES ('snap_it_docs', %s, 'sha', 'main')",
            (TENANT,),
        )
        conn.execute(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES ('file_it_docs', %s, 'src/billing.py', 'python', 'x', 'snap_it_docs')",
            (TENANT,),
        )
        for i, nombre in enumerate(SIMBOLOS):
            conn.execute(
                "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line,"
                " signature) VALUES (%s, %s, 'file_it_docs', 'function', %s, %s, %s, %s)",
                (f"sym_it_{nombre}", TENANT, nombre, i + 1, i + 1, f"def {nombre}()"),
            )
        yield conn
        _limpiar(conn)


@pytest.fixture
def lector() -> LectorFalso:
    return LectorFalso({_blob_ref(d, a): (DOCS / a).read_bytes() for d, a, _ in FIXTURES})


def _contar(conn: psycopg.Connection[Any], sql: str, *params: Any) -> int:
    fila = conn.execute(sql, params).fetchone()
    return int(fila[0]) if fila else 0


def test_cinco_fixtures_se_ingieren_embeben_y_enlazan(
    admin: psycopg.Connection[Any], lector: LectorFalso
) -> None:
    # Un nodo semántico cuyo embedding coincide con el chunk "Notas de crédito" del manual.
    texto_manual = extraer_texto((DOCS / FIXTURES[0][1]).read_bytes(), "text/markdown", "m.md")
    chunk_nc = next(
        c
        for c in trocear_documento_markdown(FIXTURES[0][0], texto_manual, tipo_doc="manual")
        if c.seccion == "Notas de crédito"
    )
    admin.execute(
        """INSERT INTO vec.definition_embeddings
               (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash)
           VALUES ('de_it_nc', %s, 'sym_it_Invoice', 'symbol', 'Invoice', 'd', %s::vector, 'h')""",
        (TENANT, vector_literal(TEIFalso.vector(chunk_nc.texto))),
    )

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        resultados = procesar_documentos_pendientes(loader, lector, TEIFalso(), TENANT)  # type: ignore[arg-type]

    assert [r.estado for r in resultados] == ["procesado"] * 5, [r.motivo for r in resultados]
    filas = admin.execute(
        """SELECT d.id, d.estado, d.motivo_fallo, d.chunks_count, d.tipo_documento,
                  count(c.id), count(DISTINCT c.chunk_index),
                  bool_and(vector_dims(c.embedding) = 1024)
             FROM core.onboarding_documents d
             JOIN vec.doc_chunks c ON c.tenant_id = d.tenant_id AND c.document_id = d.id
            WHERE d.tenant_id = %s GROUP BY d.id ORDER BY d.id""",
        (TENANT,),
    ).fetchall()
    assert len(filas) == 5
    for doc_id, estado, motivo, n, tipo, chunks, distintos, dims_ok in filas:
        assert (estado, motivo) == ("procesado", None), doc_id
        assert n == chunks == distintos >= 2
        assert dims_ok
        assert tipo in ("manual", "spec", "adr", "negocio")
        enlaces = _contar(
            admin,
            """SELECT count(*) FROM cm.doc_chunk_links l
                 JOIN vec.doc_chunks c ON c.tenant_id = l.tenant_id AND c.id = l.chunk_id
                WHERE l.tenant_id = %s AND c.document_id = %s""",
            TENANT,
            doc_id,
        )
        assert enlaces > 0, f"{doc_id} sin enlaces a nodos"

    metodo, score = admin.execute(
        "SELECT metodo, score FROM cm.doc_chunk_links WHERE tenant_id = %s AND chunk_id = %s"
        " AND nodo_ref = 'sym_it_Invoice'",
        (TENANT, id_doc_chunk(TENANT, chunk_nc.documento_id, chunk_nc.chunk_idx)),
    ).fetchone() or (None, None)
    assert metodo == "semantico" and score == pytest.approx(1.0, abs=1e-4)


def test_reprocesar_no_duplica_chunks(admin: psycopg.Connection[Any], lector: LectorFalso) -> None:
    tei = TEIFalso()
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        procesar_documentos_pendientes(loader, lector, tei, TENANT)  # type: ignore[arg-type]
        antes = admin.execute(
            "SELECT id, document_id, chunk_index, texto_hash FROM vec.doc_chunks"
            " WHERE tenant_id = %s ORDER BY 2, 3",
            (TENANT,),
        ).fetchall()
        enlaces_antes = _contar(
            admin, "SELECT count(*) FROM cm.doc_chunk_links WHERE tenant_id = %s", TENANT
        )
        embebidos = len(tei.textos)

        for documento in loader.obtener_documentos(("procesado",)):
            r = procesar_documento(
                documento,
                repositorio=loader,
                lector=lector,
                tei=tei,  # type: ignore[arg-type]
                tenant_id=TENANT,
            )
            assert r.estado == "procesado" and r.chunks_embebidos == 0

    despues = admin.execute(
        "SELECT id, document_id, chunk_index, texto_hash FROM vec.doc_chunks"
        " WHERE tenant_id = %s ORDER BY 2, 3",
        (TENANT,),
    ).fetchall()
    assert despues == antes
    assert len(tei.textos) == embebidos
    assert (
        _contar(admin, "SELECT count(*) FROM cm.doc_chunk_links WHERE tenant_id = %s", TENANT)
        == enlaces_antes
    )
    assert (
        _contar(
            admin,
            "SELECT count(*) FROM (SELECT 1 FROM vec.doc_chunks WHERE tenant_id = %s"
            " GROUP BY document_id, chunk_index HAVING count(*) > 1) d",
            TENANT,
        )
        == 0
    )

    # La base lo impide aunque la aplicación se equivoque: UNIQUE dc_chunk_uq.
    doc_id, idx = antes[0][1], antes[0][2]
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin.execute(
            "INSERT INTO vec.doc_chunks (id, tenant_id, document_id, chunk_index, content,"
            " embedding, texto_hash) VALUES ('dup', %s, %s, %s, 'x', %s::vector, 'h')",
            (TENANT, doc_id, idx, vector_literal([0.1] * 1024)),
        )


def test_estado_terminal_con_motivo(admin: psycopg.Connection[Any], lector: LectorFalso) -> None:
    lector.blobs[_blob_ref("doc_it_spec", "spec_cobros.pdf")] = b"%PDF-1.4 roto"
    del lector.blobs[_blob_ref("doc_it_adr", "ADR-021-conciliacion.docx")]

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        procesar_documentos_pendientes(loader, lector, TEIFalso(), TENANT)  # type: ignore[arg-type]

    estados = dict(
        admin.execute(
            "SELECT id, estado || ':' || coalesce(split_part(motivo_fallo, ':', 1), '')"
            " FROM core.onboarding_documents WHERE tenant_id = %s",
            (TENANT,),
        ).fetchall()
    )
    assert estados == {
        "doc_it_manual": "procesado:",
        "doc_it_spec": "fallido:FORMATO_INVALIDO",
        "doc_it_adr": "fallido:BLOB_NO_DISPONIBLE",
        "doc_it_politicas": "procesado:",
        "doc_it_legado": "procesado:",
    }
    assert (
        _contar(
            admin,
            "SELECT count(*) FROM vec.doc_chunks WHERE tenant_id = %s AND document_id IN"
            " ('doc_it_spec', 'doc_it_adr')",
            TENANT,
        )
        == 0
    )

    # od_motivo_chk: 'fallido' sin motivo, o motivo sin 'fallido', no se pueden guardar.
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "UPDATE core.onboarding_documents SET motivo_fallo = NULL WHERE id = 'doc_it_spec'"
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "UPDATE core.onboarding_documents SET motivo_fallo = 'x' WHERE id = 'doc_it_manual'"
        )


def test_rls_aisla_chunks_y_enlaces(admin: psycopg.Connection[Any], lector: LectorFalso) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        procesar_documentos_pendientes(loader, lector, TEIFalso(), TENANT)  # type: ignore[arg-type]

    with GraphLoader(dsn=APP_DSN, tenant_id=OTRO_TENANT) as ajeno:
        assert ajeno.obtener_documentos(("pendiente", "procesado", "fallido")) == []
        assert ajeno.obtener_chunks_docs() == []
        assert ajeno.obtener_hashes_doc_chunks("doc_it_manual") == {}
        with ajeno.fase("lectura") as cur:
            cur.execute("SELECT count(*) FROM cm.doc_chunk_links")
            assert (cur.fetchone() or [None])[0] == 0
