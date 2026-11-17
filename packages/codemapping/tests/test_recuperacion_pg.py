"""Recuperación en dos fases y duplicados de tickets contra PostgreSQL 16 + pgvector (KC-CM-09-5).

Requiere `make up && make db`. Las consultas corren como kallicode_app con el tenant fijado por
GraphLoader.fase(); el administrador solo siembra. Dos organizaciones con los MISMOS ids y los
MISMOS vectores: si la recuperación mezclara tenants, la otra saldría igual de cerca.

Los vectores se construyen a mano (`_vec(coseno, eje)`): el coseno con la consulta es exacto, así
que el orden esperado y el umbral se comprueban sin depender de un embebedor.

Criterios:
- El contexto combinado nunca supera el presupuesto de tokens de la etapa Diseño.
- El re-ranking topológico sube lo cercano al área aunque sea menos parecido en la fase 1.
- Detecta duplicados con coseno > umbral y solo dentro del tenant.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from kallicode_codemapping.budget import estimar_tokens_texto
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.recuperacion import buscar_duplicados, contexto_diseno
from kallicode_core import cm_busqueda

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT_A = "org_test_rec_a"
TENANT_B = "org_test_rec_b"
TODOS = (TENANT_A, TENANT_B)
SNAP = "snap_rec"
DIM = 1024


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                """SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'cm' AND p.proname = 'buscar_hibrido'"""
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 043 sin aplicar (make up && make db)"
)


def _vec(coseno: float, eje: int) -> list[float]:
    """Vector unitario con `coseno` exacto frente a la consulta (el eje 0)."""
    v = [0.0] * DIM
    v[0] = coseno
    v[eje] = math.sqrt(max(1.0 - coseno * coseno, 0.0))
    return v


CONSULTA = _vec(1.0, 1)


class EmbebedorFijo:
    """La consulta de todo texto es el eje 0: los cosenos sembrados son los esperados."""

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        return [CONSULTA for _ in texts]


# Símbolos: id, nombre, coseno con la consulta, contenido.
FICHERO = "src/billing/invoice.py"
SIMBOLOS = [
    ("sym_emitir", "emitir_factura", 0.70, "Emite la factura y calcula el IVA."),
    ("sym_iva", "calcular_iva", 0.75, "Calcula el IVA de una factura."),
    ("sym_pdf", "exportar_pdf", 0.90, "Exporta la factura a PDF (otro módulo, sin relación)."),
    ("sym_lejos", "enviar_correo", 0.60, "Envía correos."),
]
# Tickets: id, título, coseno con la consulta.
TICKETS = [
    ("tk_dup_fuerte", "IVA duplicado al reemitir", 0.95),
    ("tk_dup_justo", "Reemisión repite el impuesto", 0.80),
    ("tk_borde", "Exportar a PDF falla", 0.70),  # == umbral por defecto: NO es duplicado
    ("tk_distinto", "Cambiar el logo", 0.40),
]


def _marca(tenant: str, texto: str) -> str:
    return f"[{tenant}] {texto}"


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        for tabla in (
            "vec.ticket_embeddings",
            "core.tickets",
            "core.ticket_counters",
            "vec.definition_embeddings",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _sembrar(conn: psycopg.Connection[Any], tenant: str, relleno: int = 0) -> None:
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) VALUES (%s, %s, 'sha', 'main')",
        (SNAP, tenant),
    )
    conn.execute(
        "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
        "VALUES ('file_inv', %s, %s, 'python', 'x', %s)",
        (tenant, FICHERO, SNAP),
    )
    for i, (sid, nombre, coseno, texto) in enumerate(SIMBOLOS, start=2):
        conn.execute(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line,"
            " signature) VALUES (%s, %s, 'file_inv', 'function', %s, 1, 2, %s)",
            (sid, tenant, nombre, f"def {nombre}()"),
        )
        conn.execute(
            "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre,"
            " descripcion, modulo, embedding, texto_hash)"
            " VALUES (%s, %s, %s, 'symbol', %s, %s, 'src/billing', %s::vector, %s)",
            (
                f"de_{sid}",
                tenant,
                sid,
                _marca(tenant, nombre),
                _marca(tenant, texto) + (" relleno" * relleno),
                cm_busqueda.vector_literal(_vec(coseno, i)),
                f"h_{sid}",
            ),
        )
    # emitir -> iva: iva queda a un salto del área (el fichero y sus símbolos).
    conn.execute(
        "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id) "
        "VALUES (%s, 'CALLS', 'symbol', 'sym_emitir', 'symbol', 'sym_iva')",
        (tenant,),
    )
    # sym_iva también pertenece al fichero en este corpus; el área real es solo `sym_emitir`.
    for i, (tid, titulo, coseno) in enumerate(TICKETS, start=2):
        id_t = f"{tid}_{tenant}"  # core.tickets.id es único en toda la base
        conn.execute(
            "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion)"
            " VALUES (%s, %s, 'bug', %s, 'd')",
            (id_t, tenant, _marca(tenant, titulo)),
        )
        conn.execute(
            "INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding, texto_hash)"
            " VALUES (%s, %s, %s::vector, %s)",
            (id_t, tenant, cm_busqueda.vector_literal(_vec(coseno, i)), f"h_{tid}"),
        )


@pytest.fixture(scope="module")
def datos() -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        _sembrar(conn, TENANT_A)
        _sembrar(conn, TENANT_B)
        conn.execute("ANALYZE vec.definition_embeddings")
        conn.execute("ANALYZE vec.ticket_embeddings")
        yield
        _limpiar(conn)


# ---------------------------------------------------------------------------
# Contexto para Diseño
# ---------------------------------------------------------------------------
def _contexto(tenant: str, **kw: Any) -> Any:
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        return contexto_diseno(
            loader, EmbebedorFijo(), "La factura duplica el IVA", capas=["symbol"], **kw
        )


def test_el_reranking_topologico_sube_lo_cercano_al_area(datos: None) -> None:
    sin_grafo = _contexto(TENANT_A, nodos=["sym_emitir"], peso_topologico=0.0)
    con_grafo = _contexto(TENANT_A, nodos=["sym_emitir"], peso_topologico=0.35)

    # Fase 1 pura: manda el coseno (pdf 0,90 > iva 0,75 > emitir 0,70 > lejos 0,60).
    assert [e.referencia for e in sin_grafo.elementos] == [
        "sym_pdf",
        "sym_iva",
        "sym_emitir",
        "sym_lejos",
    ]
    # Con grafo: el área (0 saltos) y lo que llama (1 salto) pasan por delante de lo ajeno.
    assert [e.referencia for e in con_grafo.elementos] == [
        "sym_emitir",
        "sym_iva",
        "sym_pdf",
        "sym_lejos",
    ]
    por_ref = {e.referencia: e for e in con_grafo.elementos}
    assert (por_ref["sym_emitir"].distancia, por_ref["sym_iva"].distancia) == (0, 1)
    assert por_ref["sym_pdf"].distancia is None
    assert con_grafo.reordenado and con_grafo.semillas == ["sym_emitir"]


def test_sin_area_explicita_la_semilla_son_los_mejores_de_la_fase_1(datos: None) -> None:
    ctx = _contexto(TENANT_A)
    assert ctx.reordenado
    # Los 3 mejores nodos de la fase 1 son el área (pdf 0,90, iva 0,75 y emitir 0,70).
    assert set(ctx.semillas) == {"sym_pdf", "sym_iva", "sym_emitir"}


def test_el_contexto_respeta_el_presupuesto_de_tokens(datos: None) -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE vec.definition_embeddings SET descripcion = descripcion || %s "
            "WHERE tenant_id = %s",
            (" detalle largo" * 80, TENANT_A),
        )
    try:
        completo = _contexto(TENANT_A, nodos=["sym_emitir"], presupuesto_tokens=100_000)
        chico = _contexto(TENANT_A, nodos=["sym_emitir"], presupuesto_tokens=300)
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(
                "UPDATE vec.definition_embeddings SET descripcion = replace(descripcion, %s, '') "
                "WHERE tenant_id = %s",
                (" detalle largo" * 80, TENANT_A),
            )
    assert completo.omitidos_por_presupuesto == 0 and len(completo.elementos) == 4
    assert chico.tokens_estimados <= 300
    assert estimar_tokens_texto(chico.texto) == chico.tokens_estimados
    assert chico.omitidos_por_presupuesto >= 1
    assert chico.candidatos == 4
    # Lo que entra es lo mejor puntuado.
    assert chico.elementos[0].referencia == "sym_emitir"


def test_el_contexto_solo_trae_filas_del_tenant(datos: None) -> None:
    for tenant, otro in ((TENANT_A, TENANT_B), (TENANT_B, TENANT_A)):
        ctx = _contexto(tenant, nodos=["sym_emitir"])
        assert ctx.texto and f"[{tenant}]" in ctx.texto
        assert f"[{otro}]" not in ctx.texto


# ---------------------------------------------------------------------------
# Duplicados de tickets
# ---------------------------------------------------------------------------
def _duplicados(tenant: str, **kw: Any) -> Any:
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        return buscar_duplicados(loader, EmbebedorFijo(), "IVA duplicado en factura", **kw)


def test_detecta_duplicados_sobre_el_umbral_configurado(datos: None) -> None:
    r = _duplicados(TENANT_A, umbral=0.70)
    assert [c.ticket_id for c in r.candidatos] == [
        f"tk_dup_fuerte_{TENANT_A}",
        f"tk_dup_justo_{TENANT_A}",
        f"tk_borde_{TENANT_A}",
        f"tk_distinto_{TENANT_A}",
    ]
    # Estrictamente superior al umbral: el de 0,70 exacto no cuenta.
    assert [d.ticket_id for d in r.duplicados] == [
        f"tk_dup_fuerte_{TENANT_A}",
        f"tk_dup_justo_{TENANT_A}",
    ]
    assert [d.similitud for d in r.duplicados] == [0.95, 0.8]


def test_el_umbral_cambia_el_resultado(datos: None) -> None:
    assert len(_duplicados(TENANT_A, umbral=0.90).duplicados) == 1
    assert len(_duplicados(TENANT_A, umbral=0.60).duplicados) == 3
    assert _duplicados(TENANT_A, umbral=0.99).duplicados == []


def test_los_duplicados_estan_aislados_por_tenant(datos: None) -> None:
    for tenant, otro in ((TENANT_A, TENANT_B), (TENANT_B, TENANT_A)):
        r = _duplicados(tenant, umbral=0.0, k=50)
        assert len(r.candidatos) == len(TICKETS)  # ni uno del otro tenant
        assert all(c.ticket_id.endswith(tenant) for c in r.candidatos)
        assert all(f"[{otro}]" not in c.titulo for c in r.candidatos)


def test_excluye_al_propio_ticket(datos: None) -> None:
    r = _duplicados(TENANT_A, umbral=0.70, ticket_id=f"tk_dup_fuerte_{TENANT_A}")
    assert f"tk_dup_fuerte_{TENANT_A}" not in [c.ticket_id for c in r.candidatos]
    assert [d.ticket_id for d in r.duplicados] == [f"tk_dup_justo_{TENANT_A}"]
