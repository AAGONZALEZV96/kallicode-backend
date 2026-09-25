"""Fase 7 - Persistencia del grafo CodeMapping en PostgreSQL 16 (esquemas cm y vec).

KC-ONB-03-2 (CM-F01): reemplaza el cargador Neo4j por carga masiva con COPY.

Patrón por tabla:
    1. COPY de las filas a una tabla temporal de staging (ON COMMIT DROP).
    2. INSERT ... SELECT desde staging hacia cm.* con ON CONFLICT (idempotente).

Cada fase (archivos, símbolos, esquema, semántica, vectores) corre en su
propia transacción: si una fase falla, se revierte completa y las anteriores
quedan confirmadas. Al abrir cada transacción se fija `app.tenant_id` para
que las políticas RLS confinen la sesión al tenant.

Las aristas cuyo extremo no existe (p. ej. una llamada a un símbolo que no
está en el grafo) se descartan en el INSERT ... SELECT, igual que hacía el
MATCH de Cypher, en vez de romper la FK.

Contrato de esquema: docs/esquema_cm.md (DDL en db/migrations, KC-ONB-03-4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from itertools import count
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from .config import Settings
from .logging_setup import get_logger

logger = get_logger()

# Capas del pipeline (embeddings.LAYERS) -> valores del CHECK de_capa_chk en vec.definition_embeddings
CAPA_POR_LAYER: dict[str, str] = {
    "code": "codigo",
    "definitions": "definicion",
    "business": "negocio",
}

# Convención de 001_vec_modelo_embedding_hash.sql: sha256(texto)[:32]
LARGO_TEXTO_HASH = 32


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
            raise ValueError("Falta CM_STORE_DATABASE_URL: PostgreSQL de Kallicode donde persiste el grafo")
        self.dsn = normalizar_dsn(raw)
        self.tenant_id = tenant_id or (settings.tenant_id if settings else None)
        if not self.tenant_id:
            raise ValueError("Falta tenant_id para inicializar GraphLoader")
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
            logger.info(f"fase {nombre} confirmada", extra={"tenant_id": self.tenant_id, "fase": nombre})

    def _staging(
        self, cur: psycopg.Cursor[Any], tabla: str, columnas: Sequence[str], filas: Iterable[Sequence[Any]]
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
            + sql.SQL(", ").join(sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in resto)
            if resto
            else sql.SQL("DO NOTHING")
        )
        cur.execute(
            sql.SQL("INSERT INTO {} ({}) SELECT DISTINCT ON ({}) {} FROM {} ON CONFLICT ({}) {}").format(
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
                sql.Identifier(esquema, nombre), sql.SQL(", ").join(map(sql.Identifier, columnas))
            )
            + sql.SQL(select_filtrado).format(stg=stg)
            + sql.SQL(" ON CONFLICT DO NOTHING"),
            {"t": self.tenant_id},
        )
        return cur.rowcount

    # ------------------------------------------------------------------
    # Carga por fase
    # ------------------------------------------------------------------
    def load_files(self, files: list[Any]) -> None:
        with self.fase("archivos") as cur:
            self.write_files(cur, files)

    def write_files(self, cur: psycopg.Cursor[Any], files: list[Any]) -> int:
        t = self.tenant_id
        n = self._copy_upsert(
            cur,
            "cm.files",
            ("tenant_id", "path", "language", "sha256"),
            [(t, f.path, f.language, f.sha256) for f in files],
            pk=("tenant_id", "path"),
        )
        logger.info(f"cm.files: {n} filas", extra={"tenant_id": t})
        return n

    def load_symbols(self, symbols: list[Any]) -> None:
        with self.fase("simbolos") as cur:
            self.write_symbols(cur, symbols)

    def write_symbols(self, cur: psycopg.Cursor[Any], symbols: list[Any]) -> int:
        """Símbolos + llamadas. Las llamadas salientes de los símbolos cargados se reemplazan completas."""
        t = self.tenant_id
        n = self._copy_upsert(
            cur,
            "cm.symbols",
            ("tenant_id", "id", "file_path", "name", "kind", "start_line", "end_line", "signature"),
            [(t, y.id, y.file, y.name, y.kind, y.start_line, y.end_line, y.signature) for y in symbols],
            pk=("tenant_id", "id"),
        )
        ids = [y.id for y in symbols]
        if ids:
            cur.execute(
                "DELETE FROM cm.symbol_calls WHERE tenant_id = %s AND from_symbol_id = ANY(%s)",
                (t, ids),
            )
        # La llamada viene por NOMBRE; se resuelve contra todos los símbolos del tenant con ese nombre.
        llamadas = [(y.id, c) for y in symbols for c in y.calls]
        if llamadas:
            stg = self._staging_llamadas(cur, llamadas)
            cur.execute(
                sql.SQL(
                    """
                    INSERT INTO cm.symbol_calls (tenant_id, from_symbol_id, to_symbol_id)
                    SELECT DISTINCT %(t)s, c.from_id, s.id
                      FROM {} c
                      JOIN cm.symbols s ON s.tenant_id = %(t)s AND s.name = c.callee
                     WHERE s.id <> c.from_id
                    ON CONFLICT DO NOTHING
                    """
                ).format(stg),
                {"t": t},
            )
        logger.info(f"cm.symbols: {n} filas, {len(llamadas)} llamadas candidatas", extra={"tenant_id": t})
        return n

    def _staging_llamadas(self, cur: psycopg.Cursor[Any], llamadas: list[tuple[str, str]]) -> sql.Identifier:
        stg = sql.Identifier(f"_stg_calls_{next(self._stg)}")
        cur.execute(sql.SQL("CREATE TEMP TABLE {} (from_id text, callee text) ON COMMIT DROP").format(stg))
        with cur.copy(sql.SQL("COPY {} (from_id, callee) FROM STDIN").format(stg)) as cp:
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
    ) -> None:
        t = self.tenant_id
        with self.fase("esquema") as cur:
            self._copy_upsert(
                cur, "cm.tables", ("tenant_id", "name"), [(t, x["name"]) for x in tables], pk=("tenant_id", "name")
            )
            self._copy_upsert(
                cur,
                "cm.columns",
                ("tenant_id", "table_name", "name", "type"),
                [(t, x["name"], c["name"], c["type"]) for x in tables for c in x.get("columns", [])],
                pk=("tenant_id", "table_name", "name"),
            )
            self._copy_aristas(
                cur,
                "cm.table_references",
                ("tenant_id", "from_table", "to_table"),
                [(t, fk["from_table"], fk["to_table"]) for fk in fks],
                """SELECT DISTINCT s.tenant_id, s.from_table, s.to_table FROM {stg} s
                    JOIN cm.tables a ON a.tenant_id = %(t)s AND a.name = s.from_table
                    JOIN cm.tables b ON b.tenant_id = %(t)s AND b.name = s.to_table""",
            )
            self.replace_table_usage(cur, usage)

    def replace_table_usage(
        self, cur: psycopg.Cursor[Any], usage: list[dict[str, str]], files: list[str] | None = None
    ) -> None:
        """Reemplaza cm.file_table_usage de los archivos indicados (o de los presentes en `usage`)."""
        t = self.tenant_id
        paths = files if files is not None else sorted({u["file"] for u in usage})
        if paths:
            cur.execute("DELETE FROM cm.file_table_usage WHERE tenant_id = %s AND file_path = ANY(%s)", (t, paths))
        self._copy_aristas(
            cur,
            "cm.file_table_usage",
            ("tenant_id", "file_path", "table_name"),
            [(t, u["file"], u["table"]) for u in usage],
            """SELECT DISTINCT s.tenant_id, s.file_path, s.table_name FROM {stg} s
                JOIN cm.files f  ON f.tenant_id = %(t)s AND f.path = s.file_path
                JOIN cm.tables x ON x.tenant_id = %(t)s AND x.name = s.table_name""",
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
                ("tenant_id", "id", "name", "kind", "description"),
                [(t, d["id"], d["name_en"], d.get("kind", "module"), d.get("description_en", "")) for d in defs],
                pk=("tenant_id", "id"),
            )
            self._copy_aristas(
                cur,
                "cm.definition_symbols",
                ("tenant_id", "definition_id", "symbol_id"),
                [(t, d["id"], sid) for d in defs for sid in d.get("member_symbol_ids", [])],
                """SELECT DISTINCT s.tenant_id, s.definition_id, s.symbol_id FROM {stg} s
                    JOIN cm.symbols y ON y.tenant_id = %(t)s AND y.id = s.symbol_id""",
            )
            self._copy_upsert(
                cur,
                "cm.concepts",
                ("tenant_id", "name_en", "name_local", "description", "confidence"),
                [
                    (
                        t,
                        c["name_en"],
                        c.get("name_local", c["name_en"]),
                        c.get("description_en", ""),
                        c.get("confidence"),
                    )
                    for c in ontology.get("concepts", [])
                    if c.get("name_en")
                ],
                pk=("tenant_id", "name_en"),
            )
            self._copy_aristas(
                cur,
                "cm.concept_relations",
                ("tenant_id", "from_concept", "to_concept", "kind"),
                [(t, r["from"], r["to"], r["kind"]) for r in ontology.get("relations", [])],
                """SELECT DISTINCT s.tenant_id, s.from_concept, s.to_concept, s.kind FROM {stg} s
                    JOIN cm.concepts a ON a.tenant_id = %(t)s AND a.name_en = s.from_concept
                    JOIN cm.concepts b ON b.tenant_id = %(t)s AND b.name_en = s.to_concept""",
            )
            # Confianza: si un enlace viene repetido se conserva el de mayor confianza.
            self._copy_aristas(
                cur,
                "cm.definition_concepts",
                ("tenant_id", "definition_id", "concept_name_en", "confidence"),
                [(t, lk["definition_id"], lk["concept_name_en"], lk["confidence"]) for lk in links],
                """SELECT DISTINCT ON (s.definition_id, s.concept_name_en)
                          s.tenant_id, s.definition_id, s.concept_name_en, s.confidence
                     FROM {stg} s
                     JOIN cm.definitions d ON d.tenant_id = %(t)s AND d.id = s.definition_id
                     JOIN cm.concepts c    ON c.tenant_id = %(t)s AND c.name_en = s.concept_name_en
                 ORDER BY s.definition_id, s.concept_name_en, s.confidence DESC""",
            )

    def upsert_vectors(self, items: list[dict[str, Any]], modelo: str = "bge-m3") -> None:
        with self.fase("vectores") as cur:
            self.write_vectors(cur, items, modelo)

    def write_vectors(self, cur: psycopg.Cursor[Any], items: list[dict[str, Any]], modelo: str = "bge-m3") -> int:
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
    # Estado incremental (cm.refresh_state) y lecturas para el refresh
    # ------------------------------------------------------------------
    def set_last_sha(self, sha: str) -> None:
        with self.fase("refresh_state") as cur:
            self.write_last_sha(cur, sha)

    def write_last_sha(self, cur: psycopg.Cursor[Any], sha: str) -> None:
        cur.execute(
            """INSERT INTO cm.refresh_state (tenant_id, last_sha) VALUES (%s, %s)
               ON CONFLICT (tenant_id) DO UPDATE SET last_sha = EXCLUDED.last_sha, actualizado_en = now()""",
            (self.tenant_id, sha),
        )

    def get_last_sha(self) -> str | None:
        with self.fase("lectura") as cur:
            cur.execute("SELECT last_sha FROM cm.refresh_state WHERE tenant_id = %s", (self.tenant_id,))
            row = cur.fetchone()
        return str(row[0]) if row else None

    def get_file_hashes(self) -> dict[str, str]:
        with self.fase("lectura") as cur:
            cur.execute("SELECT path, sha256 FROM cm.files WHERE tenant_id = %s", (self.tenant_id,))
            return {str(p): str(h) for p, h in cur.fetchall()}

    def get_symbol_ids(self, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        with self.fase("lectura") as cur:
            cur.execute(
                "SELECT id FROM cm.symbols WHERE tenant_id = %s AND file_path = ANY(%s)", (self.tenant_id, paths)
            )
            return {str(r[0]) for r in cur.fetchall()}

    def get_table_names(self) -> list[dict[str, Any]]:
        with self.fase("lectura") as cur:
            cur.execute("SELECT name FROM cm.tables WHERE tenant_id = %s", (self.tenant_id,))
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
    def delete_files(self, cur: psycopg.Cursor[Any], paths: list[str]) -> list[str]:
        """Borra archivos (cascada a símbolos, llamadas, definition_symbols y usos).

        Devuelve los ids de los símbolos que se borraron, para purgar sus vectores.
        """
        if not paths:
            return []
        cur.execute("SELECT id FROM cm.symbols WHERE tenant_id = %s AND file_path = ANY(%s)", (self.tenant_id, paths))
        ids = [str(r[0]) for r in cur.fetchall()]
        cur.execute("DELETE FROM cm.files WHERE tenant_id = %s AND path = ANY(%s)", (self.tenant_id, paths))
        return ids

    def delete_symbols(self, cur: psycopg.Cursor[Any], ids: list[str]) -> None:
        if ids:
            cur.execute("DELETE FROM cm.symbols WHERE tenant_id = %s AND id = ANY(%s)", (self.tenant_id, ids))

    def purge_vectors(self, cur: psycopg.Cursor[Any], node_refs: list[str]) -> int:
        if not node_refs:
            return 0
        cur.execute(
            "DELETE FROM vec.definition_embeddings WHERE tenant_id = %s AND nodo_ref = ANY(%s)",
            (self.tenant_id, node_refs),
        )
        return cur.rowcount
