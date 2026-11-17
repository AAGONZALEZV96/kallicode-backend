"""Refresco por hash, purga y presupuesto de la ingesta documental (KC-CM-08-5 / KC-CM-08-6).

Sin base de datos: `RepoDocs` emula lo que importa de PostgreSQL (hash por documento, UNIQUE por
chunk, cascada chunk -> enlaces, baja lógica). La misma purga contra PostgreSQL real está en
tests/test_refresco_docs_pg.py.

Criterios cubiertos:
    08-5  purga de chunks y enlaces; mismo hash = 0 llamadas a TEI; procesados/omitidos/fallidos
          en las stats del snapshot.
    08-6  dry-run con documentos pendientes y desviación; superar el presupuesto detiene solo la
          fase documental; consumo registrado aparte en core.usage_tokens.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from kallicode_codemapping.budget import (
    CODIGO_DOCS_MB,
    CODIGO_PRESUPUESTO_TIEMPO,
    CODIGO_PRESUPUESTO_TOKENS,
    ControlPresupuestoDocs,
    PresupuestoDocumentalExcedido,
    estimar_tokens_texto,
)
from kallicode_codemapping.embeddings import id_doc_chunk
from kallicode_codemapping.fase_documental import (
    construir_control_docs,
    refrescar_capa_documental,
    registrar_resultado_documental,
)
from kallicode_codemapping.graph_loader import GraphLoader
from kallicode_codemapping.ingest_docs import (
    DocumentoOnboarding,
    EnlaceChunkNodo,
    IngestaDocError,
    NodoGrafo,
    procesar_documentos_pendientes,
    refrescar_documentos,
)

TENANT = "tenant-refresco"
NODOS = [NodoGrafo("n_factura", "Factura", "symbol"), NodoGrafo("n_cobro", "Cobro", "symbol")]


def _manual(secciones: dict[str, str]) -> bytes:
    cuerpo = "\n\n".join(f"## {t}\n\n{c}" for t, c in secciones.items())
    return f"# Manual de facturación\n\n{cuerpo}\n".encode()


BASE = {
    "Factura": "La Factura se emite al cerrar el cobro y registra el total con impuestos. " * 4,
    "Cobro": "El Cobro se concilia contra el banco todas las noches y deja trazabilidad. " * 4,
    "Anulacion": "La anulación de una Factura exige aprobación de un segundo usuario. " * 4,
}


class TEIFalso:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.llamadas = 0
        self.textos: list[str] = []

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.llamadas += 1
        if self.error is not None:
            raise self.error
        self.textos.extend(texts)
        return [
            [(hashlib.sha256(t.encode()).digest()[i % 32]) / 255.0 for i in range(1024)]
            for t in texts
        ]


class LectorFalso:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    def leer(self, blob_ref: str) -> bytes:
        if blob_ref not in self.blobs:
            raise IngestaDocError("BLOB_NO_DISPONIBLE", f"HTTP 404 al leer {blob_ref}")
        return self.blobs[blob_ref]


class RepoDocs:
    """core.onboarding_documents + vec.doc_chunks + cm.doc_chunk_links, con baja lógica."""

    def __init__(self, documentos: list[DocumentoOnboarding]) -> None:
        self.docs = {d.id: d for d in documentos}
        self.estado = {d.id: "pendiente" for d in documentos}
        self.hash: dict[str, str | None] = {}
        self.eliminado: set[str] = set()
        self.motivo: dict[str, str | None] = {}
        self.chunks: dict[tuple[str, int], dict[str, Any]] = {}
        self.enlaces: dict[tuple[str, str], EnlaceChunkNodo] = {}
        self.purgas = 0

    # --- lectura -----------------------------------------------------------------------------
    def obtener_documentos(
        self, estados: Sequence[str] = ("pendiente",)
    ) -> list[DocumentoOnboarding]:
        return [
            DocumentoOnboarding(
                id=d.id,
                nombre_archivo=d.nombre_archivo,
                content_type=d.content_type,
                blob_ref=d.blob_ref,
                estado=self.estado[i],
                hash_contenido=self.hash.get(i),
            )
            for i, d in self.docs.items()
            if self.estado[i] in estados and i not in self.eliminado
        ]

    def obtener_chunks_docs(
        self, documento_ids: Sequence[str] | None = None
    ) -> list[tuple[str, str, str]]:
        return []

    def buscar_documento_por_hash(self, hash_contenido: str, excluir_id: str) -> str | None:
        return next(
            (d for d, h in self.hash.items() if h == hash_contenido and d != excluir_id), None
        )

    def obtener_hashes_doc_chunks(self, documento_id: str) -> dict[int, str]:
        return {i: c["contenido_hash"] for (d, i), c in self.chunks.items() if d == documento_id}

    def obtener_nodos_enlazables(self) -> list[NodoGrafo]:
        return list(NODOS)

    # --- escritura ---------------------------------------------------------------------------
    def guardar_doc_chunks(
        self, documento_id: str, items: list[dict[str, Any]], total_chunks: int, tipo_documento: str
    ) -> int:
        for it in items:
            self.chunks[(documento_id, it["chunk_idx"])] = dict(it)
        for k in [k for k in self.chunks if k[0] == documento_id and k[1] >= total_chunks]:
            self._borrar_chunk(k)
        return len(items)

    def _borrar_chunk(self, clave: tuple[str, int]) -> None:
        chunk_id = self.chunks.pop(clave)["id"]
        for k in [k for k in self.enlaces if k[0] == chunk_id]:
            del self.enlaces[k]  # cascada dcl_chunk_fk

    def reemplazar_enlaces_docs(
        self,
        documento_ids: list[str],
        enlaces_lexicos: list[EnlaceChunkNodo],
        umbral: float,
        k: int,
    ) -> int:
        ids = {c["id"] for (d, _), c in self.chunks.items() if d in documento_ids}
        for key in [key for key in self.enlaces if key[0] in ids]:
            del self.enlaces[key]
        for e in enlaces_lexicos:
            if e.chunk_id in ids:
                self.enlaces[(e.chunk_id, e.nodo_ref)] = e
        return sum(1 for key in self.enlaces if key[0] in ids)

    def marcar_documento_procesado(
        self, documento_id: str, hash_contenido: str, chunks_count: int, tipo_documento: str
    ) -> None:
        self.estado[documento_id] = "procesado"
        self.hash[documento_id] = hash_contenido
        self.motivo[documento_id] = None

    def marcar_documento_fallido(self, documento_id: str, motivo: str) -> None:
        for k in [k for k in self.chunks if k[0] == documento_id]:
            self._borrar_chunk(k)
        self.estado[documento_id] = "fallido"
        self.motivo[documento_id] = motivo

    def obtener_documentos_a_purgar(self) -> list[str]:
        return [d for d in sorted(self.eliminado) if any(k[0] == d for k in self.chunks)]

    def purgar_documento(self, documento_id: str) -> int:
        self.purgas += 1
        claves = [k for k in self.chunks if k[0] == documento_id]
        for k in claves:
            self._borrar_chunk(k)
        self.hash[documento_id] = None
        return len(claves)


def _doc(doc_id: str) -> DocumentoOnboarding:
    return DocumentoOnboarding(
        id=doc_id,
        nombre_archivo=f"{doc_id}.md",
        content_type="text/markdown",
        blob_ref=f"b/{doc_id}",
    )


def _escenario(**blobs: bytes) -> tuple[RepoDocs, LectorFalso]:
    repo = RepoDocs([_doc(i) for i in blobs])
    return repo, LectorFalso({f"b/{i}": datos for i, datos in blobs.items()})


def _control(**kw: Any) -> ControlPresupuestoDocs:
    kw.setdefault("tokens_max", 1_000_000)
    return ControlPresupuestoDocs(**kw)


def _ingerir(repo: RepoDocs, lector: LectorFalso, tei: TEIFalso, **kw: Any) -> Any:
    return procesar_documentos_pendientes(
        repo,
        lector,
        tei,
        TENANT,
        control=kw.pop("control", _control()),
        **kw,  # type: ignore[arg-type]
    )


def _refrescar(repo: RepoDocs, lector: LectorFalso, tei: TEIFalso, **kw: Any) -> Any:
    return refrescar_documentos(
        repo,
        lector,
        tei,
        TENANT,
        control=kw.pop("control", _control()),
        **kw,  # type: ignore[arg-type]
    )


# =====================================================================
# KC-CM-08-5: refresco por hash
# =====================================================================
def test_mismo_hash_se_omite_con_cero_llamadas_a_tei() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    tei = TEIFalso()
    _ingerir(repo, lector, tei)
    assert tei.llamadas > 0 and repo.chunks

    tei_refresco = TEIFalso()
    resumen = _refrescar(repo, lector, tei_refresco)

    assert tei_refresco.llamadas == 0, "un documento sin cambios no se vuelve a embeber"
    assert (resumen.procesados, resumen.omitidos, resumen.fallidos) == (0, 1, 0)


def test_resubir_el_mismo_contenido_con_otro_id_no_se_embebe() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    _ingerir(repo, lector, TEIFalso())
    repo.docs["doc_b"] = _doc("doc_b")
    repo.estado["doc_b"] = "pendiente"
    lector.blobs["b/doc_b"] = _manual(BASE)

    tei = TEIFalso()
    resumen = _refrescar(repo, lector, tei)

    assert tei.llamadas == 0
    assert repo.estado["doc_b"] == "fallido"
    assert (repo.motivo["doc_b"] or "").startswith("DOCUMENTO_DUPLICADO")
    assert not [k for k in repo.chunks if k[0] == "doc_b"], "sin vectores ni enlaces redundantes"
    assert (resumen.omitidos, resumen.fallidos) == (1, 1)


def test_hash_cambiado_reembebe_solo_los_chunks_que_cambiaron() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    _ingerir(repo, lector, TEIFalso())
    total = len([k for k in repo.chunks if k[0] == "doc_a"])
    assert total >= 3
    lector.blobs["b/doc_a"] = _manual(
        {**BASE, "Cobro": "El Cobro ahora se concilia cada hora. " * 5}
    )

    tei = TEIFalso()
    resumen = _refrescar(repo, lector, tei)

    assert (resumen.procesados, resumen.omitidos) == (1, 0)
    assert resumen.resultados[0].chunks_embebidos == len(tei.textos) < total
    assert repo.hash["doc_a"] == hashlib.sha256(lector.blobs["b/doc_a"]).hexdigest()


def test_blob_desaparecido_de_un_documento_activo_queda_fallido_con_motivo() -> None:
    repo, lector = _escenario(
        doc_a=_manual(BASE), doc_b=_manual({"Otro": "Contenido distinto. " * 10})
    )
    _ingerir(repo, lector, TEIFalso())
    del lector.blobs["b/doc_b"]

    resumen = _refrescar(repo, lector, TEIFalso())

    assert repo.estado["doc_b"] == "fallido"
    assert (repo.motivo["doc_b"] or "").startswith("BLOB_NO_DISPONIBLE")
    assert (resumen.procesados, resumen.omitidos, resumen.fallidos) == (0, 1, 1)


# =====================================================================
# KC-CM-08-5: purga
# =====================================================================
def test_purga_borra_chunks_enlaces_y_libera_el_hash() -> None:
    repo, lector = _escenario(
        doc_a=_manual(BASE), doc_b=_manual({"Otro": "Contenido distinto. " * 10})
    )
    _ingerir(repo, lector, TEIFalso())
    assert any(k[0] == id_doc_chunk(TENANT, "doc_a", 0) for k in repo.enlaces)
    chunks_b = {k for k in repo.chunks if k[0] == "doc_b"}

    repo.eliminado.add("doc_a")
    tei = TEIFalso()
    resumen = _refrescar(repo, lector, tei)

    assert resumen.purgados == 1 and repo.purgas == 1
    assert not [k for k in repo.chunks if k[0] == "doc_a"]
    ids_b = {repo.chunks[k]["id"] for k in chunks_b}
    assert {k[0] for k in repo.enlaces} <= ids_b, "no quedan enlaces del documento purgado"
    assert repo.hash["doc_a"] is None
    assert tei.llamadas == 0
    assert resumen.omitidos == 1  # doc_b intacto

    again = _refrescar(repo, lector, TEIFalso())
    assert again.purgados == 0, "purgar es idempotente"


def test_documento_dado_de_baja_no_se_vuelve_a_ingerir() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    repo.eliminado.add("doc_a")

    tei = TEIFalso()
    resumen = _refrescar(repo, lector, tei)

    assert tei.llamadas == 0 and not repo.chunks
    assert (resumen.procesados, resumen.omitidos, resumen.fallidos, resumen.purgados) == (
        0,
        0,
        0,
        0,
    )


# =====================================================================
# KC-CM-08-5: stats del snapshot
# =====================================================================
def test_stats_del_snapshot_traen_procesados_omitidos_y_fallidos() -> None:
    repo, lector = _escenario(
        doc_a=_manual(BASE),
        doc_b=_manual({"Otro": "Contenido distinto. " * 10}),
        doc_c=_manual({"Tercero": "Un tercer documento, sin cambios. " * 8}),
    )
    _ingerir(repo, lector, TEIFalso())
    lector.blobs["b/doc_a"] = _manual({**BASE, "Cobro": "Cambio. " * 20})  # cambió -> procesado
    del lector.blobs["b/doc_b"]  # desapareció -> fallido; doc_c sin cambios -> omitido
    control = _control()
    resumen = _refrescar(repo, lector, TEIFalso(), control=control)

    loader = MagicMock()
    stats = registrar_resultado_documental(loader, "snap_1", control, resumen)

    loader.registrar_stats_snapshot.assert_called_once()
    snapshot_id, clave, valor = loader.registrar_stats_snapshot.call_args.args
    assert (snapshot_id, clave) == ("snap_1", "documentos")
    assert (valor["procesados"], valor["omitidos"], valor["fallidos"]) == (1, 1, 1)
    assert valor == stats and "presupuesto" in valor


def test_refresco_documental_aisla_sus_errores_del_grafo_de_codigo() -> None:
    loader = MagicMock()
    loader.obtener_limite_docs_mb.return_value = None
    loader.obtener_bytes_documentos_pendientes.return_value = 0
    loader.obtener_documentos_a_purgar.side_effect = psycopg.OperationalError("db caída")
    cfg = MagicMock(tenant_id=TENANT, docs_presupuesto_tokens=1000, docs_presupuesto_segundos=0)

    stats = refrescar_capa_documental(loader, cfg, "snap_1", lector=LectorFalso({}), tei=TEIFalso())  # type: ignore[arg-type]

    assert "error" in stats  # no relanza
    assert loader.registrar_stats_snapshot.call_args.args[:2] == ("snap_1", "documentos")


# =====================================================================
# KC-CM-08-6: control de presupuesto
# =====================================================================
def test_control_autoriza_hasta_el_presupuesto_y_luego_corta() -> None:
    c = _control(tokens_max=100)
    c.autorizar(60)
    with pytest.raises(PresupuestoDocumentalExcedido) as exc:
        c.autorizar(60)
    assert exc.value.codigo == CODIGO_PRESUPUESTO_TOKENS
    assert c.excedido and c.autorizado_tokens == 60
    with pytest.raises(PresupuestoDocumentalExcedido):  # una vez excedido, no vuelve a autorizar
        c.autorizar(1)


def test_control_corta_por_tiempo() -> None:
    ahora = [0.0]
    c = _control(segundos_max=10, reloj=lambda: ahora[0])
    c.autorizar(1)
    ahora[0] = 11.0
    with pytest.raises(PresupuestoDocumentalExcedido) as exc:
        c.autorizar(1)
    assert exc.value.codigo == CODIGO_PRESUPUESTO_TIEMPO


def test_control_corta_por_docs_mb_del_plan() -> None:
    c = _control(bytes_max=1024, bytes_pendientes=2048)
    c.registrar_estimacion(10, 1)
    with pytest.raises(PresupuestoDocumentalExcedido) as exc:
        c.autorizar(1)
    assert exc.value.codigo == CODIGO_DOCS_MB


def test_desviacion_mide_real_contra_estimado_de_lo_procesado() -> None:
    c = _control()
    c.registrar_real(tokens_reales=110, tokens_estimados=100, duracion_ms=5)
    assert c.desviacion_pct == 10.0
    assert _control().desviacion_pct is None


# =====================================================================
# KC-CM-08-6: dry-run y corte aislado
# =====================================================================
def test_dry_run_estima_los_pendientes_antes_de_llamar_a_tei() -> None:
    repo, lector = _escenario(
        doc_a=_manual(BASE), doc_b=_manual({"Otro": "Contenido distinto. " * 10})
    )
    control = _control()
    visto: dict[str, int] = {}

    class TEIQueMira(TEIFalso):
        def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
            visto.setdefault("estimado_al_embeber", control.estimado_tokens)
            visto.setdefault("docs_al_embeber", control.documentos_estimados)
            return super().embed_texts_sync(texts)

    _ingerir(repo, lector, TEIQueMira(), control=control)

    assert visto["docs_al_embeber"] == 2 and visto["estimado_al_embeber"] > 0
    assert control.real_tokens == control.estimado_procesados > 0
    assert control.desviacion_pct == 0.0


def test_estimacion_solo_cuenta_los_chunks_que_se_van_a_embeber() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    primero = _control()
    _ingerir(repo, lector, TEIFalso(), control=primero)
    lector.blobs["b/doc_a"] = _manual({**BASE, "Cobro": "Cambio. " * 20})

    segundo = _control()
    _refrescar(repo, lector, TEIFalso(), control=segundo)

    assert 0 < segundo.estimado_tokens < primero.estimado_tokens


def test_superar_el_presupuesto_detiene_solo_la_fase_y_deja_pendientes() -> None:
    repo, lector = _escenario(
        doc_a=_manual({"A": "Primer documento con contenido propio. " * 6}),
        doc_b=_manual({"B": "Segundo documento con otro contenido. " * 6}),
        doc_c=_manual({"C": "Tercer documento muy distinto a los dos. " * 6}),
    )
    una = estimar_tokens_texto("Primer documento con contenido propio. " * 6)
    control = _control(tokens_max=una * 2)  # alcanza para ~1 documento y medio

    resultados = _ingerir(repo, lector, TEIFalso(), control=control)

    assert repo.estado["doc_a"] == "procesado"
    assert [repo.estado[d] for d in ("doc_b", "doc_c")] == ["pendiente", "pendiente"]
    assert all(r.estado != "fallido" for r in resultados)
    assert control.excedido and control.documentos_sin_procesar == 2
    assert control.codigo == CODIGO_PRESUPUESTO_TOKENS
    assert any(k[0] == "doc_a" for k in repo.chunks), "lo ya ingerido se conserva"

    # Al ampliar el presupuesto, la siguiente corrida retoma los pendientes.
    _ingerir(repo, lector, TEIFalso(), control=_control())
    assert {repo.estado[d] for d in repo.docs} == {"procesado"}


def test_docs_mb_excedido_no_procesa_nada_y_no_falla_nada() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    control = _control(bytes_max=10, bytes_pendientes=999)
    tei = TEIFalso()

    resultados = _ingerir(repo, lector, tei, control=control)

    assert tei.llamadas == 0 and repo.estado["doc_a"] == "pendiente"
    assert resultados == [] and control.codigo == CODIGO_DOCS_MB


def test_corte_presupuestario_queda_en_las_stats() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE), doc_b=_manual({"X": "Distinto. " * 30}))
    control = _control(tokens_max=1)
    resultados = _ingerir(repo, lector, TEIFalso(), control=control)

    loader = MagicMock()
    stats = registrar_resultado_documental(loader, "snap_1", control, resultados=resultados)

    assert stats["presupuesto"]["presupuesto_documental_excedido"] is True
    assert stats["presupuesto"]["codigo"] == CODIGO_PRESUPUESTO_TOKENS
    assert stats["pendientes_restantes"] == 2


# =====================================================================
# KC-CM-08-6: consumo en core.usage_tokens, aparte
# =====================================================================
def test_el_consumo_documental_se_registra_por_separado() -> None:
    repo, lector = _escenario(doc_a=_manual(BASE))
    control = _control()
    resultados = _ingerir(repo, lector, TEIFalso(), control=control)

    loader = MagicMock()
    registrar_resultado_documental(loader, "snap_1", control, resultados=resultados)

    loader.registrar_consumo_documental.assert_called_once_with(
        "snap_1", control.real_tokens, 1, control.real_ms
    )
    assert control.real_tokens > 0


def test_registro_de_consumo_no_tumba_la_corrida_si_falla() -> None:
    control = _control()
    control.registrar_real(10, 10, 1)
    loader = MagicMock()
    loader.registrar_consumo_documental.side_effect = RuntimeError("boom")

    registrar_resultado_documental(loader, "snap_1", control, resultados=[])  # no lanza


def test_consumo_sin_migracion_045_avisa_y_no_falla() -> None:
    loader = GraphLoader(dsn="postgresql://x/y", tenant_id=TENANT)

    def fase(_nombre: str) -> Any:
        raise psycopg.errors.UndefinedColumn('column "origen" does not exist')

    loader.fase = fase  # type: ignore[method-assign,assignment]
    assert loader.registrar_consumo_documental("snap_1", 100, 1, 5) is False


def test_construir_control_toma_docs_mb_del_plan_y_settings() -> None:
    loader = MagicMock()
    loader.obtener_limite_docs_mb.return_value = 200
    loader.obtener_bytes_documentos_pendientes.return_value = 5
    cfg = MagicMock(docs_presupuesto_tokens=777, docs_presupuesto_segundos=30)

    c = construir_control_docs(loader, cfg)

    assert (c.tokens_max, c.segundos_max, c.bytes_max, c.bytes_pendientes) == (
        777, 30.0, 200 * 1024 * 1024, 5,
    )  # fmt: skip
    loader.obtener_limite_docs_mb.return_value = None  # enterprise: sin tope
    assert construir_control_docs(loader, cfg).bytes_max is None
