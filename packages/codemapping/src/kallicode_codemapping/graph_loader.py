"""Fase 7 - Persistencia del grafo CodeMapping en PostgreSQL 16 (esquemas cm y vec).

KC-ONB-03-1/03-2 (CM-F01): carga masiva con COPY sobre el esquema cm de
db/migrations/002_esquema_cm.sql (11 tablas, aristas genéricas en cm.edges,
instantáneas de ingesta en cm.repo_snapshots).

Patrón por tabla:
    1. COPY de las filas a una tabla temporal de staging (ON COMMIT DROP).
    2. INSERT ... SELECT desde staging hacia cm.* con ON CONFLICT (idempotente).

Cada fase (archivos, símbolos, esquema, semántica, vectores) corre en su
propia transacción: si una fase falla, se revierte completa y las anteriores
quedan confirmadas. Al abrir cada transacción se fija `app.tenant_id` para
que las políticas RLS confinen la sesión al tenant.

Toda relación del grafo (CALLS, REFERENCES, USES_TABLE, REALIZED_BY,
RELATES, EXPRESSES) es una fila de cm.edges: la clave primaria compuesta
(tenant_id, tipo, src_id, dst_id) hace estructuralmente imposible una
arista entre dos tenants. Las aristas cuyo extremo no existe se descartan
en el INSERT ... SELECT en vez de romper la FK.

Instantáneas (cm.repo_snapshots): cada corrida de build_graph.full_build()
o refresh.run_refresh() registra una fila con su commit, su fase_actual y
su estado. Una corrida que muere a mitad de camino queda en estado
'fallido' y find_resumable_snapshot() la reutiliza en el siguiente intento
para el mismo commit, en vez de crear una instantánea nueva.

Contrato de esquema: docs/esquema_cm.md (DDL en db/migrations/002_esquema_cm.sql).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from itertools import count
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from .config import Settings
from .curation import CurationItem
from .ingest_docs import DocumentoOnboarding, EnlaceChunkNodo, NodoGrafo
from .logging_setup import get_logger
from .nuggets import ChunkFuente, NuggetPropuesto

logger = get_logger()

# Capas del pipeline (embeddings.LAYERS) -> kind del nodo en cm.nodes. de_capa_chk de
# vec.definition_embeddings admite exactamente los kinds de cm.node_types (KC-DB-04-4).
CAPA_POR_LAYER: dict[str, str] = {
    "code": "symbol",
    "definitions": "definition",
    "business": "concept",
}

# Convención de 001_vec_modelo_embedding_hash.sql: sha256(texto)[:32]
LARGO_TEXTO_HASH = 32

# Estados de cm.repo_snapshots desde los que una corrida puede reanudarse
# (snapshots_estado_chk de 002_esquema_cm.sql).
ESTADOS_REANUDABLES = ("en_progreso", "fallido", "pendiente_intervencion")


def normalizar_dsn(url: str) -> str:
    """Convierte una URL estilo SQLAlchemy (postgresql+asyncpg://...) a DSN libpq."""
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def texto_hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:LARGO_TEXTO_HASH]


def vector_literal(vector: Sequence[float]) -> str:
    """Formato de texto de pgvector: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def det_id(prefijo: str, tenant_id: str, *clave_natural: str) -> str:
    """ID determinista con prefijo: mismo tenant + misma clave natural -> mismo id.

    Es lo que hace idempotente cargar el mismo contenido dos veces (CM-F09):
    el ON CONFLICT DO UPDATE actualiza la misma fila en vez de duplicarla.
    """
    ident = f"{tenant_id}:{':'.join(clave_natural)}"
    return f"{prefijo}_{hashlib.sha256(ident.encode('utf-8')).hexdigest()[:16]}"


class GraphLoader:
    """Cargador del grafo de un tenant sobre PostgreSQL (cm.* y vec.definition_embeddings)."""

    def __init__(
        self,
        dsn: str | None = None,
        tenant_id: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        raw = dsn or (settings.store_database_url if settings else None)
        if not raw:
            raise ValueError(
                "Falta CM_STORE_DATABASE_URL: PostgreSQL de Kallicode donde persiste el grafo"
            )
        self.dsn = normalizar_dsn(raw)
        tenant_id_resuelto = tenant_id or (settings.tenant_id if settings else None)
        if not tenant_id_resuelto:
            raise ValueError("Falta tenant_id para inicializar GraphLoader")
        self.tenant_id: str = tenant_id_resuelto
        self._conn: psycopg.Connection[Any] | None = None
        self._stg = count()

    # ------------------------------------------------------------------
    # Conexión y transacciones
    # ------------------------------------------------------------------
    @property
    def conn(self) -> psycopg.Connection[Any]:
        if self._conn is None or self._conn.closed:
            # autocommit=True: los BEGIN/COMMIT los controla explícitamente conn.transaction() en fase()
            self._conn = psycopg.connect(self.dsn, autocommit=True)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    def __enter__(self) -> GraphLoader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def fase(self, nombre: str) -> Iterator[psycopg.Cursor[Any]]:
        """Transacción de una fase: commit al salir, rollback completo ante error."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (self.tenant_id,))
            cur.execute("SELECT set_config('app.actor', %s, true)", ("svc:codemapping",))
            yield cur
        if nombre != "lectura":
            logger.info(
                f"fase {nombre} confirmada",
                extra={"tenant_id": self.tenant_id, "fase": nombre},
            )

    def _staging(
        self,
        cur: psycopg.Cursor[Any],
        tabla: str,
        columnas: Sequence[str],
        filas: Iterable[Sequence[Any]],
    ) -> sql.Identifier:
        """Crea staging con la forma de `tabla` y la llena por COPY. Devuelve su identificador."""
        stg = sql.Identifier(f"_stg_{tabla.replace('.', '_')}_{next(self._stg)}")
        esquema, nombre = tabla.split(".", 1)
        cur.execute(
            sql.SQL("CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP").format(
                stg, sql.Identifier(esquema, nombre)
            )
        )
        cols = sql.SQL(", ").join(map(sql.Identifier, columnas))
        with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(stg, cols)) as cp:
            for fila in filas:
                cp.write_row(fila)
        return stg

    def _copy_upsert(
        self,
        cur: psycopg.Cursor[Any],
        tabla: str,
        columnas: Sequence[str],
        filas: Sequence[Sequence[Any]],
        pk: Sequence[str],
    ) -> int:
        """COPY a staging + INSERT ON CONFLICT (pk) DO UPDATE de las columnas no-PK."""
        if not filas:
            return 0
        stg = self._staging(cur, tabla, columnas, filas)
        esquema, nombre = tabla.split(".", 1)
        cols = sql.SQL(", ").join(map(sql.Identifier, columnas))
        resto = [c for c in columnas if c not in pk]
        accion = (
            sql.SQL("DO UPDATE SET ")
            + sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in resto
            )
            if resto
            else sql.SQL("DO NOTHING")
        )
        cur.execute(
            sql.SQL(
                "INSERT INTO {} ({}) SELECT DISTINCT ON ({}) {} FROM {} ON CONFLICT ({}) {}"
            ).format(
                sql.Identifier(esquema, nombre),
                cols,
                sql.SQL(", ").join(map(sql.Identifier, pk)),
                cols,
                stg,
                sql.SQL(", ").join(map(sql.Identifier, pk)),
                accion,
            )
        )
        return cur.rowcount

    def _copy_aristas(
        self,
        cur: psycopg.Cursor[Any],
        tabla: str,
        columnas: Sequence[str],
        filas: Sequence[Sequence[Any]],
        select_filtrado: LiteralString,
    ) -> int:
        """COPY a staging + INSERT de solo las aristas cuyos extremos existen.

        `select_filtrado` es el SELECT (con {stg} y %(t)s) que produce las filas válidas.
        """
        if not filas:
            return 0
        stg = self._staging(cur, tabla, columnas, filas)
        esquema, nombre = tabla.split(".", 1)
        cur.execute(
            sql.SQL("INSERT INTO {} ({}) ").format(
                sql.Identifier(esquema, nombre),
                sql.SQL(", ").join(map(sql.Identifier, columnas)),
            )
            + sql.SQL(select_filtrado).format(stg=stg)
            + sql.SQL(" ON CONFLICT DO NOTHING"),
            {"t": self.tenant_id},
        )
        return cur.rowcount

    # ------------------------------------------------------------------
    # Instantáneas de ingesta (cm.repo_snapshots, KC-ONB-03-1/KC-ONB-04-4)
    # ------------------------------------------------------------------
    def find_resumable_snapshot(self, commit_sha: str) -> str | None:
        """Busca una instantánea del mismo commit que no haya terminado con éxito."""
        with self.fase("lectura") as cur:
            cur.execute(
                sql.SQL("""
                    SELECT id FROM cm.repo_snapshots
                     WHERE tenant_id = %(t)s AND commit_sha = %(c)s AND estado = ANY(%(estados)s)
                     ORDER BY iniciado_en DESC
                     LIMIT 1
                    """),
                {
                    "t": self.tenant_id,
                    "c": commit_sha,
                    "estados": list(ESTADOS_REANUDABLES),
                },
            )
            row = cur.fetchone()
        return str(row[0]) if row else None

    def start_snapshot(self, commit_sha: str, rama: str) -> str:
        """Reanuda una instantánea reanudable del mismo commit, o crea una nueva."""
        reanudable = self.find_resumable_snapshot(commit_sha)
        if reanudable:
            logger.info(
                f"snapshot {reanudable} reanudado",
                extra={"tenant_id": self.tenant_id, "commit_sha": commit_sha},
            )
            return reanudable
        snap_id = f"snap_{uuid.uuid4().hex[:20]}"
        with self.fase("snapshot") as cur:
            cur.execute(
                """INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado)
                   VALUES (%s, %s, %s, %s, 'en_progreso')""",
                (snap_id, self.tenant_id, commit_sha, rama),
            )
        return snap_id

    def marcar_fase(self, snapshot_id: str, fase_actual: str) -> None:
        """Punto de control: hasta dónde llegó la instantánea con éxito."""
        with self.fase("snapshot") as cur:
            cur.execute(
                "UPDATE cm.repo_snapshots SET fase_actual = %s WHERE tenant_id = %s AND id = %s",
                (fase_actual, self.tenant_id, snapshot_id),
            )

    def complete_snapshot(
        self, snapshot_id: str, estado: str = "completo", motivo: str | None = None
    ) -> None:
        """Cierra la instantánea. `motivo` (si hay) se agrega a stats sin tocar fase_actual,
        que debe seguir reflejando el último punto de control confirmado con éxito."""
        with self.fase("snapshot") as cur:
            if motivo is not None:
                cur.execute(
                    """UPDATE cm.repo_snapshots
                       SET estado = %s, finalizado_en = now(),
                           stats = stats || jsonb_build_object('motivo', %s::text)
                     WHERE tenant_id = %s AND id = %s""",
                    (estado, motivo, self.tenant_id, snapshot_id),
                )
            else:
                cur.execute(
                    "UPDATE cm.repo_snapshots SET estado = %s, finalizado_en = now() WHERE tenant_id = %s AND id = %s",
                    (estado, self.tenant_id, snapshot_id),
                )

    # ------------------------------------------------------------------
    # Fase 1-2: archivos y símbolos
    # ------------------------------------------------------------------
    def load_files(self, snapshot_id: str, files: list[Any]) -> dict[str, str]:
        with self.fase("archivos") as cur:
            return self.write_files(cur, snapshot_id, files)

    def write_files(
        self, cur: psycopg.Cursor[Any], snapshot_id: str, files: list[Any]
    ) -> dict[str, str]:
        t = self.tenant_id
        file_ids = {f.path: det_id("file", t, f.path) for f in files}
        n = self._copy_upsert(
            cur,
            "cm.files",
            ("id", "tenant_id", "path", "language", "sha256", "snapshot_id"),
            [(file_ids[f.path], t, f.path, f.language, f.sha256, snapshot_id) for f in files],
            pk=("tenant_id", "id"),
        )
        logger.info(f"cm.files: {n} filas", extra={"tenant_id": t})
        return file_ids

    def load_symbols(self, symbols: list[Any], file_ids: dict[str, str]) -> None:
        with self.fase("simbolos") as cur:
            self.write_symbols(cur, symbols, file_ids)

    def write_symbols(
        self, cur: psycopg.Cursor[Any], symbols: list[Any], file_ids: dict[str, str]
    ) -> int:
        """Símbolos + llamadas. Las llamadas salientes de los símbolos cargados se reemplazan completas."""
        t = self.tenant_id
        n = self._copy_upsert(
            cur,
            "cm.symbols",
            (
                "id",
                "tenant_id",
                "file_id",
                "kind",
                "name",
                "start_line",
                "end_line",
                "signature",
            ),
            [
                (
                    y.id,
                    t,
                    file_ids[y.file],
                    y.kind,
                    y.name,
                    y.start_line,
                    y.end_line,
                    y.signature,
                )
                for y in symbols
                if y.file in file_ids
            ],
            pk=("tenant_id", "id"),
        )
        ids = [y.id for y in symbols]
        if ids:
            cur.execute(
                "DELETE FROM cm.edges WHERE tenant_id = %s AND tipo = 'CALLS' AND src_id = ANY(%s)",
                (t, ids),
            )
        # La llamada viene por NOMBRE; se resuelve solo contra símbolos del mismo lenguaje
        # (dos símbolos homónimos en lenguajes distintos no deben enlazarse).
        llamadas = [(y.id, c, y.language) for y in symbols for c in y.calls]
        if llamadas:
            stg = self._staging_llamadas(cur, llamadas)
            cur.execute(
                sql.SQL("""
                    INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id)
                    SELECT DISTINCT %(t)s, 'CALLS', 'symbol', c.from_id, 'symbol', s.id
                      FROM {} c
                      JOIN cm.symbols s ON s.tenant_id = %(t)s AND s.name = c.callee
                      JOIN cm.files f   ON f.tenant_id = %(t)s AND f.id = s.file_id AND f.language = c.lang
                     WHERE s.id <> c.from_id
                    ON CONFLICT DO NOTHING
                    """).format(stg),
                {"t": t},
            )
        logger.info(
            f"cm.symbols: {n} filas, {len(llamadas)} llamadas candidatas",
            extra={"tenant_id": t},
        )
        return n

    def _staging_llamadas(
        self, cur: psycopg.Cursor[Any], llamadas: list[tuple[str, str, str]]
    ) -> sql.Identifier:
        stg = sql.Identifier(f"_stg_calls_{next(self._stg)}")
        cur.execute(
            sql.SQL(
                "CREATE TEMP TABLE {} (from_id text, callee text, lang text) ON COMMIT DROP"
            ).format(stg)
        )
        with cur.copy(sql.SQL("COPY {} (from_id, callee, lang) FROM STDIN").format(stg)) as cp:
            for fila in llamadas:
                cp.write_row(fila)
        return stg

    # ------------------------------------------------------------------
    # Fase 3: esquema de la base del cliente
    # ------------------------------------------------------------------
    def load_schema(
        self,
        tables: list[dict[str, Any]],
        fks: list[dict[str, Any]],
        usage: list[dict[str, str]],
        file_ids: dict[str, str],
    ) -> dict[str, str]:
        t = self.tenant_id
        tabla_ids = {
            x["name"]: det_id("dbt", t, x.get("esquema", "public"), x["name"]) for x in tables
        }
        with self.fase("esquema") as cur:
            self._copy_upsert(
                cur,
                "cm.db_tables",
                ("id", "tenant_id", "esquema", "nombre"),
                [(tabla_ids[x["name"]], t, x.get("esquema", "public"), x["name"]) for x in tables],
                pk=("tenant_id", "id"),
            )
            columnas = [
                (
                    det_id("dbc", t, x["name"], c["name"]),
                    t,
                    tabla_ids[x["name"]],
                    c["name"],
                    c["type"],
                    c["nullable"],
                )
                for x in tables
                for c in x.get("columns", [])
            ]
            self._copy_upsert(
                cur,
                "cm.db_columns",
                ("id", "tenant_id", "table_id", "nombre", "tipo", "nullable"),
                columnas,
                pk=("tenant_id", "id"),
            )
            self._copy_aristas(
                cur,
                "cm.edges",
                ("tenant_id", "tipo", "src_kind", "src_id", "dst_kind", "dst_id"),
                [
                    (
                        t,
                        "REFERENCES",
                        "db_table",
                        tabla_ids[fk["from_table"]],
                        "db_table",
                        tabla_ids[fk["to_table"]],
                    )
                    for fk in fks
                    if fk["from_table"] in tabla_ids and fk["to_table"] in tabla_ids
                ],
                "SELECT DISTINCT s.tenant_id, s.tipo, s.src_kind, s.src_id, s.dst_kind, s.dst_id FROM {stg} s",
            )
            self.replace_table_usage(cur, usage, file_ids, tabla_ids)
        return tabla_ids

    def replace_table_usage(
        self,
        cur: psycopg.Cursor[Any],
        usage: list[dict[str, str]],
        file_ids: dict[str, str],
        tabla_ids: dict[str, str],
        files: list[str] | None = None,
    ) -> None:
        """Reemplaza las aristas USES_TABLE de los archivos indicados (o de los presentes en `usage`)."""
        t = self.tenant_id
        paths = files if files is not None else sorted({u["file"] for u in usage})
        ids_afectados = [file_ids[p] for p in paths if p in file_ids]
        if ids_afectados:
            cur.execute(
                "DELETE FROM cm.edges WHERE tenant_id = %s AND tipo = 'USES_TABLE' AND src_id = ANY(%s)",
                (t, ids_afectados),
            )
        self._copy_aristas(
            cur,
            "cm.edges",
            ("tenant_id", "tipo", "src_kind", "src_id", "dst_kind", "dst_id"),
            [
                (
                    t,
                    "USES_TABLE",
                    "file",
                    file_ids[u["file"]],
                    "db_table",
                    tabla_ids[u["table"]],
                )
                for u in usage
                if u["file"] in file_ids and u["table"] in tabla_ids
            ],
            "SELECT DISTINCT s.tenant_id, s.tipo, s.src_kind, s.src_id, s.dst_kind, s.dst_id FROM {stg} s",
        )

    # ------------------------------------------------------------------
    # Fase 4-5: capa semántica (definiciones y ontología de negocio)
    # ------------------------------------------------------------------
    def load_semantic(
        self,
        definitions: list[dict[str, Any]],
        ontology: dict[str, Any],
        links: list[dict[str, Any]],
    ) -> None:
        t = self.tenant_id
        defs = [d for d in definitions if d.get("id") and d.get("name_en")]
        with self.fase("semantica") as cur:
            self._copy_upsert(
                cur,
                "cm.definitions",
                ("id", "tenant_id", "modulo", "nombre", "kind", "descripcion"),
                [
                    (
                        d["id"],
                        t,
                        d.get("module", "root"),
                        d["name_en"],
                        d.get("kind", "module"),
                        d.get("description_en", ""),
                    )
                    for d in defs
                ],
                pk=("tenant_id", "id"),
            )
            self._copy_aristas(
                cur,
                "cm.edges",
                ("tenant_id", "tipo", "src_kind", "src_id", "dst_kind", "dst_id"),
                [
                    (t, "REALIZED_BY", "definition", d["id"], "symbol", sid)
                    for d in defs
                    for sid in d.get("member_symbol_ids", [])
                ],
                """SELECT DISTINCT s.tenant_id, s.tipo, s.src_kind, s.src_id, s.dst_kind, s.dst_id FROM {stg} s
                    JOIN cm.symbols y ON y.tenant_id = %(t)s AND y.id = s.dst_id""",
            )
            concept_ids = {
                c["name_en"]: det_id("con", t, c["name_en"])
                for c in ontology.get("concepts", [])
                if c.get("name_en")
            }
            self._copy_upsert(
                cur,
                "cm.concepts",
                (
                    "id",
                    "tenant_id",
                    "name_en",
                    "name_local",
                    "descripcion",
                    "confianza",
                ),
                [
                    (
                        concept_ids[c["name_en"]],
                        t,
                        c["name_en"],
                        c.get("name_local", c["name_en"]),
                        c.get("description_en", ""),
                        c.get("confidence"),
                    )
                    for c in ontology.get("concepts", [])
                    if c.get("name_en")
                ],
                pk=("tenant_id", "id"),
            )
            self._copy_aristas(
                cur,
                "cm.edges",
                (
                    "tenant_id",
                    "tipo",
                    "src_kind",
                    "src_id",
                    "dst_kind",
                    "dst_id",
                    "props",
                ),
                [
                    (
                        t,
                        "RELATES",
                        "concept",
                        concept_ids[r["from"]],
                        "concept",
                        concept_ids[r["to"]],
                        f'{{"kind": "{r["kind"]}"}}',
                    )
                    for r in ontology.get("relations", [])
                    if r["from"] in concept_ids and r["to"] in concept_ids
                ],
                "SELECT DISTINCT s.tenant_id, s.tipo, s.src_kind, s.src_id, s.dst_kind, s.dst_id, s.props FROM {stg} s",
            )
            # Confianza: si un enlace viene repetido se conserva el de mayor confianza.
            self._copy_aristas(
                cur,
                "cm.edges",
                (
                    "tenant_id",
                    "tipo",
                    "src_kind",
                    "src_id",
                    "dst_kind",
                    "dst_id",
                    "props",
                ),
                [
                    (
                        t,
                        "EXPRESSES",
                        "definition",
                        lk["definition_id"],
                        "concept",
                        concept_ids[lk["concept_name_en"]],
                        f'{{"confidence": {lk["confidence"]}}}',
                    )
                    for lk in links
                    if lk["concept_name_en"] in concept_ids
                ],
                """SELECT DISTINCT ON (s.src_id, s.dst_id)
                          s.tenant_id, s.tipo, s.src_kind, s.src_id, s.dst_kind, s.dst_id, s.props
                     FROM {stg} s
                     JOIN cm.definitions d ON d.tenant_id = %(t)s AND d.id = s.src_id
                 ORDER BY s.src_id, s.dst_id, (s.props->>'confidence')::numeric DESC""",
            )

    def upsert_vectors(self, items: list[dict[str, Any]], modelo: str = "bge-m3") -> None:
        with self.fase("vectores") as cur:
            self.write_vectors(cur, items, modelo)

    def write_vectors(
        self,
        cur: psycopg.Cursor[Any],
        items: list[dict[str, Any]],
        modelo: str = "bge-m3",
    ) -> int:
        """Persiste en vec.definition_embeddings items con forma {id, layer, node_ref, text, vector, payload}."""
        t = self.tenant_id
        filas = []
        for it in items:
            payload = it.get("payload", {})
            texto = str(it["text"])
            filas.append(
                (
                    it["id"],
                    t,
                    it["node_ref"],
                    CAPA_POR_LAYER[it["layer"]],
                    payload.get("name") or payload.get("name_en") or it["node_ref"],
                    texto,
                    payload.get("module"),
                    vector_literal(it["vector"]),
                    modelo,
                    texto_hash(texto),
                )
            )
        n = self._copy_upsert(
            cur,
            "vec.definition_embeddings",
            (
                "id",
                "tenant_id",
                "nodo_ref",
                "capa",
                "nombre",
                "descripcion",
                "modulo",
                "embedding",
                "modelo_embedding",
                "texto_hash",
            ),
            filas,
            pk=("tenant_id", "nodo_ref"),
        )
        logger.info(f"vec.definition_embeddings: {n} filas", extra={"tenant_id": t})
        return n

    # ------------------------------------------------------------------
    # Lecturas para el refresco incremental (refresh.py, KC-ONB-03-3)
    # ------------------------------------------------------------------
    def get_last_sha(self) -> str | None:
        """Commit de la última instantánea completada con éxito de este tenant."""
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT commit_sha FROM cm.repo_snapshots
                    WHERE tenant_id = %s AND estado = 'completo'
                    ORDER BY finalizado_en DESC NULLS LAST, iniciado_en DESC
                    LIMIT 1""",
                (self.tenant_id,),
            )
            row = cur.fetchone()
        return str(row[0]) if row else None

    def get_file_hashes(self) -> dict[str, str]:
        with self.fase("lectura") as cur:
            cur.execute(
                "SELECT path, sha256 FROM cm.files WHERE tenant_id = %s",
                (self.tenant_id,),
            )
            return {str(p): str(h) for p, h in cur.fetchall()}

    def get_symbol_ids(self, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT s.id FROM cm.symbols s
                    JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
                   WHERE s.tenant_id = %s AND f.path = ANY(%s)""",
                (self.tenant_id, paths),
            )
            return {str(r[0]) for r in cur.fetchall()}

    def get_table_names(self) -> list[dict[str, Any]]:
        with self.fase("lectura") as cur:
            cur.execute(
                "SELECT nombre FROM cm.db_tables WHERE tenant_id = %s",
                (self.tenant_id,),
            )
            return [{"name": str(r[0])} for r in cur.fetchall()]

    def get_vector_hashes(self, node_refs: list[str]) -> dict[str, str]:
        if not node_refs:
            return {}
        with self.fase("lectura") as cur:
            cur.execute(
                "SELECT nodo_ref, texto_hash FROM vec.definition_embeddings"
                " WHERE tenant_id = %s AND nodo_ref = ANY(%s)",
                (self.tenant_id, node_refs),
            )
            return {str(r): str(h) for r, h in cur.fetchall()}

    # ------------------------------------------------------------------
    # Borrado (refresh.py, KC-ONB-03-3)
    # ------------------------------------------------------------------
    def delete_file_subgraph(self, paths: list[str]) -> None:
        """Borra archivos, sus símbolos (cascada), y limpia a mano lo que no tiene FK:
        las aristas de cm.edges (el destino varía según tipo, no hay FK cruzada) y
        los vectores de los símbolos borrados."""
        if not paths:
            return
        t = self.tenant_id
        with self.fase("borrado") as cur:
            cur.execute(
                "SELECT id FROM cm.files WHERE tenant_id = %s AND path = ANY(%s)",
                (t, paths),
            )
            file_ids = [str(r[0]) for r in cur.fetchall()]
            cur.execute(
                "SELECT id FROM cm.symbols WHERE tenant_id = %s AND file_id = ANY(%s)",
                (t, file_ids),
            )
            symbol_ids = [str(r[0]) for r in cur.fetchall()]

            nodos = file_ids + symbol_ids
            if nodos:
                cur.execute(
                    "DELETE FROM cm.edges WHERE tenant_id = %s AND (src_id = ANY(%s) OR dst_id = ANY(%s))",
                    (t, nodos, nodos),
                )
            if symbol_ids:
                cur.execute(
                    "DELETE FROM vec.definition_embeddings WHERE tenant_id = %s AND nodo_ref = ANY(%s)",
                    (t, symbol_ids),
                )
            # cm.symbols cae en cascada por la FK a cm.files (ON DELETE CASCADE)
            cur.execute(
                "DELETE FROM cm.files WHERE tenant_id = %s AND path = ANY(%s)",
                (t, paths),
            )

    def load_curation_queue(
        self,
        snapshot_id: str,
        items: list[CurationItem],
    ) -> int:
        """Persiste elementos candidatos en cm.curation_queue bajo RLS forzado (KC-CM-07-2)."""
        if not items:
            return 0

        # Usar la propiedad de conexión canónica del loader
        conn = self.conn

        with conn.cursor() as cur:
            # Asegurar contexto multi-tenant activo para las políticas RLS
            cur.execute("SET LOCAL app.tenant_id = %s;", (self.tenant_id,))

            # cm.curation_queue guarda el detalle en `payload` (jsonb). Un elemento ya resuelto por
            # una persona (aprobado/rechazado) no se reabre al reingestar: solo se refrescan los
            # pendientes.
            query = """
                INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (tenant_id, id) DO UPDATE SET payload = EXCLUDED.payload
                WHERE cm.curation_queue.estado = 'pendiente';
            """

            filas = [item.to_row() for item in items]
            cur.executemany(query, filas)

        conn.commit()
        return len(filas)

    # ------------------------------------------------------------------
    # Documentación (KC-CM-08-1): core.onboarding_documents -> vec.doc_chunks
    # -> cm.doc_chunk_links. Implementa ingest_docs.RepositorioDocumentos.
    # Columnas de vec.doc_chunks según 037 + 039 (document_id, chunk_index, content).
    # ------------------------------------------------------------------
    def obtener_documentos(
        self, estados: Sequence[str] = ("pendiente",)
    ) -> list[DocumentoOnboarding]:
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT id, nombre_archivo, content_type, blob_ref, tipo_documento
                     FROM core.onboarding_documents
                    WHERE tenant_id = %s AND estado = ANY(%s)
                    ORDER BY creado_en, id""",
                (self.tenant_id, list(estados)),
            )
            return [
                DocumentoOnboarding(
                    id=str(r[0]),
                    nombre_archivo=str(r[1]),
                    content_type=str(r[2]),
                    blob_ref=str(r[3]),
                    tipo_documento=str(r[4]),
                )
                for r in cur.fetchall()
            ]

    def buscar_documento_por_hash(self, hash_contenido: str, excluir_id: str) -> str | None:
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT id FROM core.onboarding_documents
                    WHERE tenant_id = %s AND hash_contenido = %s AND id <> %s
                    LIMIT 1""",
                (self.tenant_id, hash_contenido, excluir_id),
            )
            row = cur.fetchone()
        return str(row[0]) if row else None

    def obtener_hashes_doc_chunks(self, documento_id: str) -> dict[int, str]:
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT chunk_index, texto_hash FROM vec.doc_chunks
                    WHERE tenant_id = %s AND document_id = %s""",
                (self.tenant_id, documento_id),
            )
            return {int(i): str(h) for i, h in cur.fetchall()}

    def guardar_doc_chunks(
        self,
        documento_id: str,
        items: list[dict[str, Any]],
        total_chunks: int,
        tipo_documento: str,
    ) -> int:
        """Upsert idempotente por (tenant_id, document_id, chunk_index) en una sola transacción.

        `items` son solo los chunks nuevos o cambiados (ya embebidos); los no cambiados se dejan
        como están. Los índices >= total_chunks (sobrantes de una versión más larga del documento)
        se borran, y con ellos sus enlaces (FK en cascada).
        """
        filas = [
            (
                it["id"],
                self.tenant_id,
                documento_id,
                int(it["chunk_idx"]),
                it["texto"],
                vector_literal(it["vector"]),
                it.get("seccion") or "",
                tipo_documento,
                json.dumps(it.get("metadata") or {}, ensure_ascii=False),
                it.get("modelo_embedding") or "bge-m3",
                it["contenido_hash"],
            )
            for it in items
        ]
        with self.fase("documentos") as cur:
            if filas:
                cur.executemany(
                    """INSERT INTO vec.doc_chunks (
                           id, tenant_id, document_id, chunk_index, content, embedding,
                           seccion, tipo_documento, metadatos, modelo_embedding, texto_hash
                       ) VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s::jsonb, %s, %s)
                       ON CONFLICT (tenant_id, document_id, chunk_index) DO UPDATE SET
                           content          = EXCLUDED.content,
                           embedding        = EXCLUDED.embedding,
                           seccion          = EXCLUDED.seccion,
                           tipo_documento   = EXCLUDED.tipo_documento,
                           metadatos        = EXCLUDED.metadatos,
                           modelo_embedding = EXCLUDED.modelo_embedding,
                           texto_hash       = EXCLUDED.texto_hash,
                           actualizado_en   = now()""",
                    filas,
                )
            cur.execute(
                """DELETE FROM vec.doc_chunks
                    WHERE tenant_id = %s AND document_id = %s AND chunk_index >= %s""",
                (self.tenant_id, documento_id, total_chunks),
            )
            cur.execute(
                """UPDATE vec.doc_chunks SET tipo_documento = %s
                    WHERE tenant_id = %s AND document_id = %s AND tipo_documento <> %s""",
                (tipo_documento, self.tenant_id, documento_id, tipo_documento),
            )
        return len(filas)

    def obtener_nodos_enlazables(self) -> list[NodoGrafo]:
        """Nodos con nombre que un documento puede describir: símbolos, tablas, definiciones, conceptos."""
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT id, name, 'symbol' FROM cm.symbols WHERE tenant_id = %(t)s
                   UNION ALL
                   SELECT id, nombre, 'db_table' FROM cm.db_tables WHERE tenant_id = %(t)s
                   UNION ALL
                   SELECT id, nombre, 'definition' FROM cm.definitions WHERE tenant_id = %(t)s
                   UNION ALL
                   SELECT id, name_en, 'concept' FROM cm.concepts WHERE tenant_id = %(t)s""",
                {"t": self.tenant_id},
            )
            return [NodoGrafo(str(i), str(n), str(k)) for i, n, k in cur.fetchall()]

    def obtener_chunks_docs(
        self, documento_ids: Sequence[str] | None = None
    ) -> list[tuple[str, str, str]]:
        """(chunk_id, document_id, content) de los documentos procesados del tenant."""
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT c.id, c.document_id, c.content
                     FROM vec.doc_chunks c
                     JOIN core.onboarding_documents d
                       ON d.tenant_id = c.tenant_id AND d.id = c.document_id
                    WHERE c.tenant_id = %(t)s AND d.estado = 'procesado'
                      AND (%(docs)s::text[] IS NULL OR c.document_id = ANY(%(docs)s::text[]))
                    ORDER BY c.document_id, c.chunk_index""",
                {"t": self.tenant_id, "docs": list(documento_ids) if documento_ids else None},
            )
            return [(str(i), str(d), str(c)) for i, d, c in cur.fetchall()]

    def reemplazar_enlaces_docs(
        self,
        documento_ids: list[str],
        enlaces_lexicos: list[EnlaceChunkNodo],
        umbral_semantico: float,
        top_k_semantico: int,
    ) -> int:
        """Recalcula los enlaces chunk -> nodo de `documento_ids` en una transacción.

        Léxicos: los calculados en Python (ingest_docs.enlazar_chunks_lexico).
        Semánticos: los top-k nodos de vec.definition_embeddings más cercanos a cada chunk con
        similitud coseno >= umbral (HNSW con los parámetros de 'diseno', KC-DB-08-1). Si un nodo
        sale por ambos métodos se queda el score mayor.
        """
        if not documento_ids:
            return 0
        t = self.tenant_id
        upsert = """
            ON CONFLICT (tenant_id, chunk_id, nodo_ref) DO UPDATE SET
                metodo = CASE WHEN EXCLUDED.score > cm.doc_chunk_links.score
                              THEN EXCLUDED.metodo ELSE cm.doc_chunk_links.metodo END,
                score  = GREATEST(cm.doc_chunk_links.score, EXCLUDED.score)"""
        with self.fase("enlaces_docs") as cur:
            cur.execute(
                """DELETE FROM cm.doc_chunk_links l
                    USING vec.doc_chunks c
                    WHERE l.tenant_id = %(t)s AND c.tenant_id = %(t)s
                      AND l.chunk_id = c.id AND c.document_id = ANY(%(docs)s)""",
                {"t": t, "docs": documento_ids},
            )
            if enlaces_lexicos:
                cur.executemany(
                    """INSERT INTO cm.doc_chunk_links (tenant_id, chunk_id, nodo_ref, metodo, score)
                       SELECT %s, %s, %s, %s, %s
                        WHERE EXISTS (SELECT 1 FROM cm.nodes n WHERE n.tenant_id = %s AND n.id = %s)
                    """
                    + upsert,
                    [
                        (t, e.chunk_id, e.nodo_ref, e.metodo, e.score, t, e.nodo_ref)
                        for e in enlaces_lexicos
                    ],
                )
            if top_k_semantico > 0:
                cur.execute("SELECT vec.configurar_busqueda('diseno')")
                cur.execute(
                    """INSERT INTO cm.doc_chunk_links (tenant_id, chunk_id, nodo_ref, metodo, score)
                       SELECT c.tenant_id, c.id, n.nodo_ref, 'semantico', n.sim
                         FROM vec.doc_chunks c
                         CROSS JOIN LATERAL (
                               SELECT d.nodo_ref,
                                      LEAST(1, GREATEST(0, 1 - (d.embedding <=> c.embedding)))::real AS sim
                                 FROM vec.definition_embeddings d
                                WHERE d.tenant_id = c.tenant_id
                                ORDER BY d.embedding <=> c.embedding
                                LIMIT %(k)s
                         ) n
                        WHERE c.tenant_id = %(t)s AND c.document_id = ANY(%(docs)s)
                          AND n.sim >= %(umbral)s
                    """
                    + upsert,
                    {
                        "t": t,
                        "docs": documento_ids,
                        "k": top_k_semantico,
                        "umbral": umbral_semantico,
                    },
                )
            cur.execute(
                """SELECT count(*) FROM cm.doc_chunk_links l
                     JOIN vec.doc_chunks c ON c.tenant_id = l.tenant_id AND c.id = l.chunk_id
                    WHERE l.tenant_id = %(t)s AND c.document_id = ANY(%(docs)s)""",
                {"t": t, "docs": documento_ids},
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def marcar_documento_procesado(
        self, documento_id: str, hash_contenido: str, chunks_count: int, tipo_documento: str
    ) -> None:
        with self.fase("documentos") as cur:
            cur.execute(
                """UPDATE core.onboarding_documents
                      SET estado = 'procesado', motivo_fallo = NULL, procesado_en = now(),
                          chunks_count = %s, hash_contenido = %s, tipo_documento = %s
                    WHERE tenant_id = %s AND id = %s""",
                (chunks_count, hash_contenido, tipo_documento, self.tenant_id, documento_id),
            )

    def marcar_documento_fallido(self, documento_id: str, motivo: str) -> None:
        """Estado terminal de fallo: sin chunks a medias (sus enlaces caen en cascada)."""
        with self.fase("documentos") as cur:
            cur.execute(
                "DELETE FROM vec.doc_chunks WHERE tenant_id = %s AND document_id = %s",
                (self.tenant_id, documento_id),
            )
            cur.execute(
                """UPDATE core.onboarding_documents
                      SET estado = 'fallido', motivo_fallo = %s, procesado_en = now(),
                          chunks_count = 0
                    WHERE tenant_id = %s AND id = %s""",
                (motivo or "ERROR_INTERNO: sin detalle", self.tenant_id, documento_id),
            )

    def obtener_extractos_docs(self, limite: int = 200) -> list[str]:
        """Contenido de los chunks procesados para la inducción de ontología, acotado a `limite`."""
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT c.content
                     FROM vec.doc_chunks c
                     JOIN core.onboarding_documents d
                       ON d.tenant_id = c.tenant_id AND d.id = c.document_id
                    WHERE c.tenant_id = %s AND d.estado = 'procesado'
                    ORDER BY c.document_id, c.chunk_index
                    LIMIT %s""",
                (self.tenant_id, limite),
            )
            return [str(r[0]) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Nuggets (KC-CM-08-2): vec.doc_chunks -> cm.nuggets ('propuesto').
    # Implementa nuggets.RepositorioNuggets. La revisión pendiente en cm.curation_queue la abre el
    # trigger nuggets_encolar de la 041; la aprobación solo llega con cm.resolver_nugget().
    # ------------------------------------------------------------------
    def obtener_chunks_para_nuggets(
        self, documento_ids: Sequence[str] | None = None
    ) -> list[ChunkFuente]:
        """Chunks (id, documento, sección, contenido) de los documentos procesados del tenant."""
        with self.fase("lectura") as cur:
            cur.execute(
                """SELECT c.id, c.document_id, c.seccion, c.content
                     FROM vec.doc_chunks c
                     JOIN core.onboarding_documents d
                       ON d.tenant_id = c.tenant_id AND d.id = c.document_id
                    WHERE c.tenant_id = %(t)s AND d.estado = 'procesado'
                      AND (%(docs)s::text[] IS NULL OR c.document_id = ANY(%(docs)s::text[]))
                    ORDER BY c.document_id, c.chunk_index""",
                {"t": self.tenant_id, "docs": list(documento_ids) if documento_ids else None},
            )
            return [ChunkFuente(str(i), str(d), str(s), str(c)) for i, d, s, c in cur.fetchall()]

    def guardar_nuggets_propuestos(self, nuggets: Sequence[NuggetPropuesto]) -> int:
        """Inserta los nuggets como 'propuesto'; los que ya existen no se tocan. Devuelve los nuevos.

        ON CONFLICT DO NOTHING: un nugget ya propuesto, aprobado o rechazado no se reabre ni se
        reescribe al reprocesar (los resueltos son inmutables en la base).
        """
        if not nuggets:
            return 0
        nuevos = 0
        with self.fase("nuggets") as cur:
            for n in nuggets:
                cur.execute(
                    """INSERT INTO cm.nuggets
                           (id, tenant_id, regla, tipo, scope_refs, evidencia_ref, seccion, cita)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                       ON CONFLICT (tenant_id, id) DO NOTHING""",
                    (
                        n.id,
                        self.tenant_id,
                        n.regla,
                        n.tipo,
                        json.dumps(list(n.scope_refs)),
                        n.evidencia_ref,
                        n.seccion,
                        n.cita,
                    ),
                )
                nuevos += cur.rowcount
        return nuevos
