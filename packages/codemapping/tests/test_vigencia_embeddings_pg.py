"""Vigencia de los embeddings y métricas de los HNSW contra PostgreSQL real (KC-CM-11-4).

Requiere `make up && make db` con db/migrations/060_vigencia_embeddings_hnsw.sql aplicada. Corre como
kallicode_app (RLS forzada), igual que en producción.

Criterios que cubre:
  * vigencia por encima del 99 % tras el refresco diario, comparando el hash del texto embebido con el
    texto canónico de la descripción en base (test_tras_el_refresco_diario_...);
  * el texto canónico de Python y el de SQL son el mismo (test_el_texto_canonico_...): si divergieran,
    la medición marcaría como obsoletos embeddings correctos o al revés;
  * cada causa de no vigencia se detecta y se repara (descripción cambiada, modelo distinto, sin
    embedding) y el límite por corrida deja el resto para la siguiente;
  * el backfill de 060 recupera la descripción de los símbolos ya embebidos;
  * el tamaño y la hinchazón de los índices y la vigencia global se exponen como métrica.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from kallicode_codemapping import refresh
from kallicode_codemapping.config import build_settings
from kallicode_codemapping.embeddings import (
    hash_texto_embedding,
    texto_embedding_definicion,
    texto_embedding_simbolo,
)
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.ingest_repo import SourceFile
from kallicode_codemapping.metricas import (
    EMBEDDINGS_VIGENCIA_RATIO,
    HNSW_MUERTAS_PCT,
    HNSW_TAMANO_BYTES,
    REGISTRY_CODEMAPPING,
    actualizar_metricas_vectoriales,
)
from kallicode_codemapping.parser_treesitter import Symbol
from kallicode_codemapping.vigencia import UMBRAL_VIGENCIA, reparar_vigencia

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
_SEP = "&" if "?" in ADMIN_DSN else "?"
APP_DSN = ADMIN_DSN + _SEP + "options=-c%20role%3Dkallicode_app"
WORKER_DSN = ADMIN_DSN + _SEP + "options=-c%20role%3Dkallicode_worker"
MIGRACION = (
    Path(__file__).resolve().parents[3] / "db" / "migrations" / "060_vigencia_embeddings_hnsw.sql"
)

TENANT = "org_test_vigencia"
N_FICHEROS = 10
SIMBOLOS_POR_FICHERO = 10
N_DEFINICIONES = 20


def _migracion_lista() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                "SELECT to_regprocedure('vec.vigencia_embeddings(text)')"
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _migracion_lista(), reason="Postgres no disponible o 060 sin aplicar (make up && make db)"
)


class TEIFalso:
    """Vector determinista por texto: dos textos distintos dan vectores distintos."""

    def __init__(self) -> None:
        self.textos: list[str] = []

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.textos.extend(texts)
        return [
            [(hashlib.sha256(t.encode()).digest()[i % 32] + 1) / 256.0 for i in range(1024)]
            for t in texts
        ]


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for tabla in (
        "vec.definition_embeddings",
        "cm.edges",
        "cm.definitions",
        "cm.symbols",
        "cm.files",
        "cm.snapshot_vigente",
        "cm.repo_snapshots",
    ):
        conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.organizations WHERE id = %s", (TENANT,))


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org vigencia')", (TENANT,)
        )
        try:
            yield conn
        finally:
            _limpiar(conn)


@pytest.fixture
def loader(admin: psycopg.Connection[Any]) -> Iterator[GraphLoader]:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as gl:
        yield gl


def _simbolo(fichero: str, i: int, firma: str | None = None) -> Symbol:
    nombre = f"f{i}"
    return Symbol(
        id=f"sym_{fichero.replace('/', '_')}_{i}",
        file=fichero,
        language="python",
        kind="function",
        name=nombre,
        start_line=i,
        end_line=i + 1,
        signature=firma or f"def {nombre}(x)",
        snippet="",
    )


def _items_simbolos(
    simbolos: list[Symbol], descripciones: dict[str, str], tei: TEIFalso
) -> list[dict[str, Any]]:
    textos = [
        texto_embedding_simbolo(s.name, s.signature, descripciones.get(s.id)) for s in simbolos
    ]
    return [
        {
            "id": f"v_{s.id}",
            "layer": "code",
            "node_ref": s.id,
            "text": t,
            "vector": v,
            "payload": {"name": s.name, "module": s.file.split("/")[0]},
        }
        for s, t, v in zip(simbolos, textos, tei.embed_texts_sync(textos), strict=True)
    ]


def _grafo_inicial(loader: GraphLoader, admin: psycopg.Connection[Any]) -> dict[str, Any]:
    """Snapshot completo de sha-viejo con 100 símbolos y 20 definiciones, todo embebido y vigente."""
    tei = TEIFalso()
    snap = loader.start_snapshot(commit_sha="sha-viejo", rama="main")
    ficheros = [
        SourceFile(path=f"pkg/m{n}.py", language="python", sha256=f"h{n}", content="")
        for n in range(N_FICHEROS)
    ]
    file_ids = loader.load_files(snap, ficheros)
    simbolos = [_simbolo(f.path, i) for f in ficheros for i in range(SIMBOLOS_POR_FICHERO)]
    loader.load_symbols(simbolos, file_ids)
    descripciones = {s.id: f"Calcula {s.name} de {s.file}.  " for s in simbolos}
    loader.actualizar_descripciones_simbolos(descripciones)
    loader.upsert_vectors(_items_simbolos(simbolos, descripciones, tei))

    defs = [(f"def_{i}", f"Concepto{i}", f"Definición número {i}") for i in range(N_DEFINICIONES)]
    admin.execute(
        "INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion) "
        "SELECT d.id, %s, 'pkg', d.nombre, 'module', d.descripcion "
        "FROM unnest(%s::text[], %s::text[], %s::text[]) AS d(id, nombre, descripcion)",
        (TENANT, [d[0] for d in defs], [d[1] for d in defs], [d[2] for d in defs]),
    )
    textos = [texto_embedding_definicion(n, d) for _, n, d in defs]
    loader.upsert_vectors(
        [
            {
                "id": f"v_{i}",
                "layer": "definitions",
                "node_ref": i,
                "text": t,
                "vector": v,
                "payload": {},
            }
            for (i, _, _), t, v in zip(defs, textos, tei.embed_texts_sync(textos), strict=True)
        ]
    )
    loader.complete_snapshot(snap, estado="completo")
    return {"snap": snap, "simbolos": simbolos, "descripciones": descripciones}


def _vigencia(loader: GraphLoader) -> dict[str, dict[str, Any]]:
    return {c["capa"]: c for c in loader.vigencia_embeddings()}


def _ratio(loader: GraphLoader) -> float:
    capas = loader.vigencia_embeddings()
    return sum(int(c["vigentes"]) for c in capas) / sum(int(c["nodos"]) for c in capas)


# --- el texto canónico es el mismo en Python y en SQL ----------------------------
CASOS_TEXTO = [
    ("f", "def f()", "Hace algo."),
    ("f", "def f()", None),
    ("f", "def f()", ""),
    ("f", "def f()", "  con espacios al final \n\t"),
    ("f", "def f()", " nbsp y ideográfico　"),
    ("Clase", "class Clase(Base):\n    ...", "Multilínea\ncon salto"),
    ("  f", "def f()  ", "  desc  "),
    ("ñandú", "def ñandú(año: int) -> str", "Árbol — «comillas» 😀"),
    ("f", "def f()", " separador de línea "),
]


@pytest.mark.parametrize(("nombre", "firma", "descripcion"), CASOS_TEXTO)
def test_el_texto_canonico_y_su_hash_coinciden_en_python_y_sql(
    nombre: str, firma: str, descripcion: str | None
) -> None:
    with psycopg.connect(APP_DSN) as conn:
        sim, defi, h_sim = conn.execute(
            "SELECT cm.texto_embedding_simbolo(%s, %s, %s), cm.texto_embedding_definicion(%s, %s),"
            " vec.hash_texto(cm.texto_embedding_simbolo(%s, %s, %s))",
            (nombre, firma, descripcion, nombre, descripcion, nombre, firma, descripcion),
        ).fetchone()
    esperado = texto_embedding_simbolo(nombre, firma, descripcion)
    assert sim == esperado
    assert defi == texto_embedding_definicion(nombre, descripcion)
    assert h_sim == hash_texto_embedding(esperado)


def test_el_strip_de_sql_recorta_exactamente_los_espacios_de_python() -> None:
    import sys

    espacios = "".join(c for c in map(chr, range(sys.maxunicode + 1)) if c.isspace())
    texto = espacios + "x" + espacios
    with psycopg.connect(APP_DSN) as conn:
        fila = conn.execute("SELECT cm.recortar_espacios(%s)", (texto,)).fetchone()
    assert fila is not None and fila[0] == texto.strip() == "x"


# --- vigencia: medición, causas y reparación ---------------------------------------
def test_un_grafo_recien_cargado_esta_vigente_al_cien_por_cien(
    loader: GraphLoader, admin: psycopg.Connection[Any]
) -> None:
    _grafo_inicial(loader, admin)
    vig = _vigencia(loader)
    assert vig["symbol"]["nodos"] == N_FICHEROS * SIMBOLOS_POR_FICHERO
    assert vig["definition"]["nodos"] == N_DEFINICIONES
    for capa in vig.values():
        assert capa["vigentes"] == capa["nodos"] and capa["ratio"] == 1
    assert loader.embeddings_a_reparar(100) == []


def test_cada_causa_de_no_vigencia_se_detecta_y_se_repara(
    loader: GraphLoader, admin: psycopg.Connection[Any]
) -> None:
    g = _grafo_inicial(loader, admin)
    s0, s1, s2 = (g["simbolos"][i].id for i in range(3))
    # Descripción curada fuera del refresco, modelo distinto, embedding perdido y definición editada.
    admin.execute(
        "UPDATE cm.symbols SET descripcion = 'Descripción curada' WHERE tenant_id = %s AND id = %s",
        (TENANT, s0),
    )
    admin.execute(
        "UPDATE vec.definition_embeddings SET modelo_embedding = 'bge-m3-viejo'"
        " WHERE tenant_id = %s AND nodo_ref = %s",
        (TENANT, s1),
    )
    admin.execute(
        "DELETE FROM vec.definition_embeddings WHERE tenant_id = %s AND nodo_ref = %s", (TENANT, s2)
    )
    admin.execute(
        "UPDATE cm.definitions SET descripcion = 'Otra' WHERE tenant_id = %s AND id = 'def_3'",
        (TENANT,),
    )
    vig = _vigencia(loader)
    assert (vig["symbol"]["obsoletos"], vig["symbol"]["sin_embedding"]) == (2, 1)
    assert vig["definition"]["obsoletos"] == 1

    motivos = {(i["node_ref"], i["motivo"]) for i in loader.embeddings_a_reparar(100)}
    assert motivos == {
        (s0, "obsoleto"),
        (s1, "modelo"),
        (s2, "sin_embedding"),
        ("def_3", "obsoleto"),
    }

    tei = TEIFalso()
    res = reparar_vigencia(loader, tei, limite=100)
    assert res.reembebidos == 4 and not res.pendientes
    assert res.motivos == {"obsoleto": 2, "modelo": 1, "sin_embedding": 1}
    assert "f0 def f0(x) Descripción curada" in tei.textos
    assert "Concepto3 Otra" in tei.textos
    assert res.ratio == 1.0 and loader.embeddings_a_reparar(100) == []


def test_el_limite_por_corrida_deja_el_resto_para_la_siguiente(
    loader: GraphLoader, admin: psycopg.Connection[Any]
) -> None:
    _grafo_inicial(loader, admin)
    admin.execute(
        "UPDATE cm.definitions SET descripcion = descripcion || ' v2' WHERE tenant_id = %s",
        (TENANT,),
    )
    primera = reparar_vigencia(loader, TEIFalso(), limite=15)
    assert (primera.reembebidos, primera.pendientes) == (15, True)
    segunda = reparar_vigencia(loader, TEIFalso(), limite=15)
    assert (segunda.reembebidos, segunda.pendientes) == (N_DEFINICIONES - 15, False)
    assert segunda.cumple


def test_tras_el_refresco_diario_la_vigencia_supera_el_99_por_ciento(
    loader: GraphLoader, admin: psycopg.Connection[Any]
) -> None:
    """Refresco incremental real (GraphLoader contra la base): cambia un fichero y, fuera de él, se
    han curado descripciones de símbolos y de definiciones. El refresco solo reembebe el fichero
    cambiado; el paso de vigencia repara el resto y deja la vigencia por encima del umbral."""
    g = _grafo_inicial(loader, admin)
    curados = [s.id for s in g["simbolos"][SIMBOLOS_POR_FICHERO : SIMBOLOS_POR_FICHERO + 6]]
    admin.execute(
        "UPDATE cm.symbols SET descripcion = 'Curada a mano' WHERE tenant_id = %s AND id = ANY(%s)",
        (TENANT, curados),
    )
    admin.execute(
        "UPDATE cm.definitions SET descripcion = 'Reconsolidada' WHERE tenant_id = %s AND id IN ('def_1', 'def_2')",
        (TENANT,),
    )
    antes = _ratio(loader)
    assert antes < UMBRAL_VIGENCIA  # 8 de 120 obsoletos: 93 %

    cambiado = "pkg/m0.py"
    nuevos = [_simbolo(cambiado, i, firma=f"def f{i}(x, y)") for i in range(SIMBOLOS_POR_FICHERO)]
    tei = TEIFalso()
    parches = {
        "sync_repo": MagicMock(return_value=("/repo", "sha-nuevo", False)),
        "_rama_actual": MagicMock(return_value="main"),
        "changed_paths": MagicMock(return_value=([cambiado], [])),
        "load_file": MagicMock(
            return_value=SourceFile(path=cambiado, language="python", sha256="h-nuevo", content="")
        ),
        "parse_file": MagicMock(return_value=nuevos),
        "describe_symbols": MagicMock(
            return_value=[{"id": s.id, "description_en": f"Nueva {s.name}"} for s in nuevos]
        ),
        "TEIEmbeddingClient": MagicMock(return_value=tei),
        "refrescar_capa_documental": MagicMock(),
    }
    cfg = build_settings(
        tenant_id=TENANT, repo_url="https://example.com/r.git", store_database_url=APP_DSN
    )
    with patch.multiple(refresh, **parches):
        refresh.run_refresh(cfg)

    despues = _ratio(loader)
    assert despues >= UMBRAL_VIGENCIA, despues
    # Las descripciones nuevas quedaron en cm.symbols y sus embeddings son los del texto nuevo.
    fila = admin.execute(
        "SELECT s.descripcion, d.texto_hash FROM cm.symbols s JOIN vec.definition_embeddings d"
        " ON d.tenant_id = s.tenant_id AND d.nodo_ref = s.id WHERE s.tenant_id = %s AND s.id = %s",
        (TENANT, nuevos[0].id),
    ).fetchone()
    assert fila is not None and fila[0] == "Nueva f0"
    assert fila[1] == hash_texto_embedding("f0 def f0(x, y) Nueva f0")
    # Los curados fuera del fichero cambiado se reembebieron en el paso de vigencia.
    assert "f0 def f0(x) Curada a mano" in tei.textos


def test_el_backfill_de_060_recupera_la_descripcion_de_los_simbolos_embebidos(
    loader: GraphLoader, admin: psycopg.Connection[Any]
) -> None:
    """Estado previo a 060: el embedding se hizo con la descripción LLM, pero cm.symbols no la guardó."""
    g = _grafo_inicial(loader, admin)
    ids = [s.id for s in g["simbolos"][:5]]
    admin.execute(
        "UPDATE cm.symbols SET descripcion = NULL WHERE tenant_id = %s AND id = ANY(%s)",
        (TENANT, ids),
    )
    # Un símbolo cuyo texto embebido ya no encaja (firma cambiada): el backfill no debe inventar nada.
    admin.execute(
        "UPDATE cm.symbols SET signature = 'def otra()' WHERE tenant_id = %s AND id = %s",
        (TENANT, ids[4]),
    )
    assert _vigencia(loader)["symbol"]["obsoletos"] == 5

    texto = MIGRACION.read_text(encoding="utf-8")
    backfill = re.search(r"^UPDATE cm\.symbols s\b.*?;$", texto, re.DOTALL | re.MULTILINE)
    assert backfill is not None
    admin.execute(backfill.group(0))

    filas = dict(
        admin.execute(
            "SELECT id, descripcion FROM cm.symbols WHERE tenant_id = %s AND id = ANY(%s)",
            (TENANT, ids),
        ).fetchall()
    )
    for sid in ids[:4]:
        assert filas[sid] == g["descripciones"][sid].strip()
    assert filas[ids[4]] is None
    assert _vigencia(loader)["symbol"]["obsoletos"] == 1


# --- métricas -------------------------------------------------------------------------
@pytest.mark.parametrize("dsn", [APP_DSN, WORKER_DSN], ids=["kallicode_app", "kallicode_worker"])
def test_el_estado_de_los_hnsw_y_la_vigencia_global_se_exponen_como_metrica(
    loader: GraphLoader, admin: psycopg.Connection[Any], dsn: str
) -> None:
    _grafo_inicial(loader, admin)
    admin.execute("ANALYZE vec.definition_embeddings")
    with psycopg.connect(dsn, autocommit=True) as conn:
        estado = {f[0]: f for f in conn.execute("SELECT * FROM vec.estado_hnsw()").fetchall()}
        assert set(estado) == {
            "definition_embeddings_hnsw_ix",
            "doc_chunks_hnsw_ix",
            "ticket_embeddings_hnsw_ix",
        }
        assert estado["definition_embeddings_hnsw_ix"][2] > 0  # tamaño en bytes
        # vec.hnsw_metricas sigue siendo solo de kallicode_admin: la métrica va por la función.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT count(*) FROM vec.hnsw_metricas")
        actualizar_metricas_vectoriales(conn)

    tamano = REGISTRY_CODEMAPPING.get_sample_value(
        "kc_codemapping_hnsw_tamano_bytes", {"indice": "definition_embeddings_hnsw_ix"}
    )
    assert tamano == HNSW_TAMANO_BYTES.labels(indice="definition_embeddings_hnsw_ix")._value.get()
    assert tamano is not None and tamano > 0
    assert HNSW_MUERTAS_PCT.labels(indice="definition_embeddings_hnsw_ix")._value.get() >= 0
    ratio = EMBEDDINGS_VIGENCIA_RATIO.labels(capa="symbol")._value.get()
    assert 0 < ratio <= 1


def test_la_vigencia_global_no_expone_datos_de_ningun_tenant() -> None:
    with psycopg.connect(WORKER_DSN, autocommit=True) as conn:
        columnas = [
            c.name for c in conn.execute("SELECT * FROM vec.vigencia_global()").description or ()
        ]
        assert "tenant_id" not in columnas
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM vec.vigencia_detalle()")


# --- parámetros de los HNSW ------------------------------------------------------------
def _opciones_hojas(conn: psycopg.Connection[Any], indice: str) -> dict[str, list[str]]:
    filas = conn.execute(
        """SELECT pc.oid::regclass::text, pc.reloptions FROM pg_class pc
            WHERE pc.relkind = 'i'
              AND pc.oid IN (SELECT t.relid FROM pg_partition_tree(to_regclass(%s)) t WHERE t.isleaf)""",
        (indice,),
    ).fetchall()
    return {h: list(o or []) for h, o in filas}


def test_los_indices_hoja_tienen_los_parametros_del_catalogo() -> None:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        m, ef = conn.execute(
            "SELECT m, ef_construction FROM vec.hnsw_catalogo WHERE indice = 'definition_embeddings_hnsw_ix'"
        ).fetchone() or (None, None)
        assert (m, ef) == (24, 128)
        hojas = _opciones_hojas(conn, "vec.definition_embeddings_hnsw_ix")
        assert hojas and all({"m=24", "ef_construction=128"} <= set(o) for o in hojas.values())
        assert list(conn.execute("SELECT * FROM vec.alinear_parametros_hnsw()")) == []


def test_una_particion_nueva_hereda_16_64_y_la_alineacion_la_corrige() -> None:
    particion = "vec.definition_embeddings_kc_it_alinear"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {particion}")
        conn.execute(
            f"CREATE TABLE {particion} PARTITION OF vec.definition_embeddings"
            " FOR VALUES IN ('org_kc_it_alinear')"
        )
        try:
            nuevas = {
                h: o
                for h, o in _opciones_hojas(conn, "vec.definition_embeddings_hnsw_ix").items()
                if "kc_it_alinear" in h
            }
            assert len(nuevas) == 1 and "m=16" in next(iter(nuevas.values()))
            tocados = conn.execute(
                "SELECT indice_hoja, despues, aplicado FROM vec.alinear_parametros_hnsw()"
            ).fetchall()
            assert [(h, a) for h, _, a in tocados] == [(next(iter(nuevas)), True)]
            assert {"m=24", "ef_construction=128"} <= set(
                _opciones_hojas(conn, "vec.definition_embeddings_hnsw_ix")[next(iter(nuevas))]
            )
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {particion}")
