"""Fase 7 - persistencia del grafo de conocimiento en PostgreSQL (KC-ONB-03-1).

Reemplaza el diseño anterior sobre Neo4j (ver docs/esquema_cm.md). Cada nodo
se escribe con INSERT ... ON CONFLICT DO UPDATE sobre su clave natural
(tenant_id + id determinista vía UUIDv5), así que cargar el mismo commit dos
veces no duplica nada (CM-F09) y dos tenants nunca colisionan (CM-F01) -- el
esquema anterior declaraba constraints globales (f.path, s.id, ...) sin
tenant y resolvía CALLS con un MATCH sin filtro de cliente.

El grafo de un snapshot no se considera vigente para consulta hasta que
`complete_snapshot()` lo marca 'completo'; si el proceso muere a medias,
`fase_actual` permite saber dónde retomar sin repetir trabajo ya hecho.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from .cm_db import ejecutar, ejecutar_lote, sesion_tenant, todos, uno
from .config import Settings
from .embeddings import generar_uuid5_vector
from .ingest_repo import SourceFile
from .parser_treesitter import Symbol

# Tipos de arista válidos en cm.edges, heredados del modelo anterior de Neo4j.
TIPOS_ARISTA = frozenset(
    {"DEFINES", "CALLS", "HAS_COLUMN", "REFERENCES", "USES_TABLE", "REALIZED_BY", "RELATES", "EXPRESSES"}
)


class GraphLoader:
    """Persistencia del grafo en el esquema `cm`. Recibe `settings` siempre por
    invocación explícita -- nunca lee el `settings` global de config.py -- para
    que dos jobs de tenants distintos en el mismo proceso no puedan compartir
    conexión ni contexto por accidente."""

    def __init__(self, settings: Settings) -> None:
        self.tenant_id = settings.tenant_id
        self.platform_database_url = settings.platform_database_url

    def _sesion(self):
        return sesion_tenant(self.platform_database_url, self.tenant_id)

    def _det_id(self, prefijo: str, kind: str, *clave_natural: str) -> str:
        """Id determinista: la misma clave natural produce siempre el mismo id,
        en este proceso o en cualquier otro (UUIDv5, no hash() de Python)."""
        ref = ":".join(clave_natural)
        return f"{prefijo}_{generar_uuid5_vector(self.tenant_id, kind, ref).hex}"

    def _upsert_edges(
        self,
        db: Session,
        tipo: str,
        src_kind: str,
        dst_kind: str,
        filas: list[dict[str, Any]],
    ) -> None:
        """`filas`: [{"src_id", "dst_id", "props"?}]. Sin FK a los nodos (el
        destino varía según el tipo), así que no hay ON DELETE CASCADE acá --
        delete_file_subgraph() limpia las aristas huérfanas a mano."""
        if tipo not in TIPOS_ARISTA:
            raise ValueError(f"tipo de arista desconocido: {tipo}")
        if not filas:
            return
        payload = [
            {
                "tenant_id": self.tenant_id,
                "tipo": tipo,
                "src_kind": src_kind,
                "src_id": f["src_id"],
                "dst_kind": dst_kind,
                "dst_id": f["dst_id"],
                "props": json.dumps(f.get("props", {})),
            }
            for f in filas
        ]
        ejecutar_lote(
            db,
            """
            INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, props)
            VALUES (:tenant_id, :tipo, :src_kind, :src_id, :dst_kind, :dst_id, CAST(:props AS jsonb))
            ON CONFLICT (tenant_id, tipo, src_id, dst_id) DO UPDATE SET props = EXCLUDED.props
            """,
            payload,
        )

    # ------------------------------------------------------------------
    # Snapshots (KC-ONB-03-2): ciclo de vida de cada corrida de ingesta
    # ------------------------------------------------------------------
    def start_snapshot(self, commit_sha: str, rama: str) -> str:
        snapshot_id = f"snap_{uuid.uuid4().hex}"
        with self._sesion() as db:
            ejecutar(
                db,
                """
                INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado)
                VALUES (:id, :tenant_id, :commit_sha, :rama, 'en_progreso')
                """,
                {"id": snapshot_id, "tenant_id": self.tenant_id, "commit_sha": commit_sha, "rama": rama},
            )
        return snapshot_id

    def checkpoint(self, snapshot_id: str, fase: str, stats: dict[str, Any] | None = None) -> None:
        with self._sesion() as db:
            ejecutar(
                db,
                """
                UPDATE cm.repo_snapshots
                   SET fase_actual = :fase, stats = stats || CAST(:stats AS jsonb)
                 WHERE tenant_id = :tenant_id AND id = :id
                """,
                {"fase": fase, "stats": json.dumps(stats or {}), "tenant_id": self.tenant_id, "id": snapshot_id},
            )

    def complete_snapshot(self, snapshot_id: str, estado: str = "completo", motivo: str | None = None) -> None:
        """`motivo` se guarda en stats.motivo_fallo sin tocar fase_actual --
        un snapshot fallido debe conservar hasta dónde llegó de verdad (el
        último checkpoint()) además de por qué murió, no perder uno por el otro."""
        with self._sesion() as db:
            ejecutar(
                db,
                """
                UPDATE cm.repo_snapshots
                   SET estado = :estado, finalizado_en = now(),
                       stats = CASE WHEN CAST(:motivo_check AS text) IS NULL THEN stats
                                    ELSE stats || jsonb_build_object('motivo_fallo', CAST(:motivo_val AS text)) END
                 WHERE tenant_id = :tenant_id AND id = :id
                """,
                {
                    "estado": estado,
                    "motivo_check": motivo,
                    "motivo_val": motivo,
                    "tenant_id": self.tenant_id,
                    "id": snapshot_id,
                },
            )

    def find_resumable_snapshot(self, commit_sha: str, rama: str) -> tuple[str, str | None]:
        """Si hay un snapshot sin completar para el MISMO commit -- 'en_progreso',
        'fallido' (un intento anterior murió) o 'pendiente_intervencion' -- lo
        reutiliza, así build_graph.py puede reanudar desde su punto de control
        en vez de repetir fases ya hechas. Solo 'completo' se considera
        terminado y no se reintenta. Si no hay ninguno pendiente, crea uno
        nuevo. Devuelve (snapshot_id, fase_actual_o_None)."""
        with self._sesion() as db:
            fila = uno(
                db,
                """
                SELECT id, fase_actual FROM cm.repo_snapshots
                 WHERE tenant_id = :tenant_id AND commit_sha = :commit_sha
                   AND estado IN ('en_progreso', 'fallido', 'pendiente_intervencion')
                 ORDER BY iniciado_en DESC
                 LIMIT 1
                """,
                {"tenant_id": self.tenant_id, "commit_sha": commit_sha},
            )

        if fila is None:
            return self.start_snapshot(commit_sha, rama), None

        with self._sesion() as db:
            ejecutar(
                db,
                "UPDATE cm.repo_snapshots SET estado = 'en_progreso' WHERE tenant_id = :t AND id = :id",
                {"t": self.tenant_id, "id": fila["id"]},
            )
        return str(fila["id"]), (str(fila["fase_actual"]) if fila["fase_actual"] else None)

    def get_last_sha(self) -> str | None:
        """Último commit de un snapshot *completo* de este tenant (no de uno a medias)."""
        with self._sesion() as db:
            fila = uno(
                db,
                """
                SELECT commit_sha FROM cm.repo_snapshots
                 WHERE tenant_id = :tenant_id AND estado = 'completo'
                 ORDER BY finalizado_en DESC
                 LIMIT 1
                """,
                {"tenant_id": self.tenant_id},
            )
        return str(fila["commit_sha"]) if fila else None

    # ------------------------------------------------------------------
    # Fase 1-2: archivos y símbolos
    # ------------------------------------------------------------------
    def load_files(self, snapshot_id: str, files: list[SourceFile]) -> dict[str, str]:
        """Inserta/actualiza cm.files. Devuelve {path: file_id}."""
        filas = []
        ids_por_path: dict[str, str] = {}
        for f in files:
            file_id = self._det_id("file", "file", f.path)
            ids_por_path[f.path] = file_id
            filas.append(
                {
                    "id": file_id,
                    "tenant_id": self.tenant_id,
                    "path": f.path,
                    "language": f.language,
                    "sha256": f.sha256,
                    "snapshot_id": snapshot_id,
                }
            )
        with self._sesion() as db:
            ejecutar_lote(
                db,
                """
                INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id)
                VALUES (:id, :tenant_id, :path, :language, :sha256, :snapshot_id)
                ON CONFLICT (tenant_id, id) DO UPDATE
                   SET language = EXCLUDED.language, sha256 = EXCLUDED.sha256,
                       snapshot_id = EXCLUDED.snapshot_id
                """,
                filas,
            )
        return ids_por_path

    def load_symbols(self, symbols: list[Symbol], file_ids: dict[str, str]) -> None:
        """Inserta/actualiza cm.symbols y las aristas DEFINES/CALLS. CALLS se
        resuelve dentro del propio lote que se está cargando, acotado siempre
        por tenant_id (el diseño original en Neo4j no filtraba por cliente) y
        por (nombre, lenguaje): dos símbolos homónimos en lenguajes distintos
        no deben enlazarse entre sí -- el nombre solo no alcanza."""
        filas_sym = []
        aristas_define = []
        por_nombre_lenguaje: dict[tuple[str, str], list[str]] = {}
        for s in symbols:
            por_nombre_lenguaje.setdefault((s.name, s.language), []).append(s.id)

        for s in symbols:
            file_id = file_ids.get(s.file)
            if file_id is None:
                continue
            filas_sym.append(
                {
                    "id": s.id,
                    "tenant_id": self.tenant_id,
                    "file_id": file_id,
                    "kind": s.kind,
                    "name": s.name,
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "signature": s.signature,
                }
            )
            aristas_define.append({"src_id": file_id, "dst_id": s.id})

        aristas_calls = [
            {"src_id": s.id, "dst_id": dst_id}
            for s in symbols
            for callee in s.calls
            for dst_id in por_nombre_lenguaje.get((callee, s.language), [])
            if dst_id != s.id
        ]

        with self._sesion() as db:
            ejecutar_lote(
                db,
                """
                INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature)
                VALUES (:id, :tenant_id, :file_id, :kind, :name, :start_line, :end_line, :signature)
                ON CONFLICT (tenant_id, id) DO UPDATE
                   SET file_id = EXCLUDED.file_id, kind = EXCLUDED.kind, name = EXCLUDED.name,
                       start_line = EXCLUDED.start_line, end_line = EXCLUDED.end_line,
                       signature = EXCLUDED.signature
                """,
                filas_sym,
            )
            self._upsert_edges(db, "DEFINES", "file", "symbol", aristas_define)
            self._upsert_edges(db, "CALLS", "symbol", "symbol", aristas_calls)

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
        """Inserta/actualiza cm.db_tables, cm.db_columns y sus aristas. Devuelve
        {nombre_tabla: id}. `esquema` se fija a 'public': extract_sql.py hoy
        solo cubre el search_path por defecto (CM-F13, fuera de este ticket)."""
        filas_tabla = []
        filas_columna = []
        aristas_columna = []
        id_tabla: dict[str, str] = {}

        for t in tables:
            nombre = str(t["name"])
            tabla_id = self._det_id("dbt", "db_table", nombre)
            id_tabla[nombre] = tabla_id
            filas_tabla.append({"id": tabla_id, "tenant_id": self.tenant_id, "esquema": "public", "nombre": nombre})
            for c in t.get("columns", []):
                col_id = self._det_id("dbc", "db_column", nombre, str(c["name"]))
                filas_columna.append(
                    {
                        "id": col_id,
                        "tenant_id": self.tenant_id,
                        "table_id": tabla_id,
                        "nombre": c["name"],
                        "tipo": c["type"],
                        "nullable": c["nullable"],
                    }
                )
                aristas_columna.append({"src_id": tabla_id, "dst_id": col_id})

        aristas_referencia = [
            {"src_id": id_tabla[fk["from_table"]], "dst_id": id_tabla[fk["to_table"]]}
            for fk in fks
            if fk["from_table"] in id_tabla and fk["to_table"] in id_tabla
        ]
        aristas_uso = [
            {"src_id": file_ids[u["file"]], "dst_id": id_tabla[u["table"]]}
            for u in usage
            if u["file"] in file_ids and u["table"] in id_tabla
        ]

        with self._sesion() as db:
            ejecutar_lote(
                db,
                """
                INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre)
                VALUES (:id, :tenant_id, :esquema, :nombre)
                ON CONFLICT (tenant_id, id) DO NOTHING
                """,
                filas_tabla,
            )
            ejecutar_lote(
                db,
                """
                INSERT INTO cm.db_columns (id, tenant_id, table_id, nombre, tipo, nullable)
                VALUES (:id, :tenant_id, :table_id, :nombre, :tipo, :nullable)
                ON CONFLICT (tenant_id, id) DO UPDATE
                   SET tipo = EXCLUDED.tipo, nullable = EXCLUDED.nullable
                """,
                filas_columna,
            )
            self._upsert_edges(db, "HAS_COLUMN", "db_table", "db_column", aristas_columna)
            self._upsert_edges(db, "REFERENCES", "db_table", "db_table", aristas_referencia)
            self._upsert_edges(db, "USES_TABLE", "file", "db_table", aristas_uso)

        return id_tabla

    # ------------------------------------------------------------------
    # Fase 4-5: capa semántica (definiciones y ontología de negocio)
    # ------------------------------------------------------------------
    def load_semantic(
        self,
        definitions: list[dict[str, Any]],
        ontology: dict[str, Any],
        links: list[dict[str, Any]],
    ) -> None:
        """Inserta/actualiza cm.definitions, cm.concepts y las aristas
        REALIZED_BY/RELATES/EXPRESSES. Ignora en silencio referencias a
        símbolos/conceptos que no llegaron a insertarse (ej. filtrados por
        curación) en vez de fallar toda la carga por una entrada parcial."""
        filas_def = []
        aristas_realiza = []
        id_definicion: dict[str, str] = {}
        for d in definitions:
            if "id" not in d or "name_en" not in d:
                continue
            origen = str(d["id"])
            def_id = self._det_id("def", "definition", origen)
            id_definicion[origen] = def_id
            filas_def.append(
                {
                    "id": def_id,
                    "tenant_id": self.tenant_id,
                    "modulo": d.get("module", ""),
                    "nombre": d["name_en"],
                    "kind": d.get("kind", "misc"),
                    "descripcion": d.get("description_en", ""),
                }
            )
            for symbol_id in d.get("member_symbol_ids", []):
                aristas_realiza.append({"src_id": def_id, "dst_id": symbol_id})

        filas_concepto = []
        id_concepto: dict[str, str] = {}
        for c in ontology.get("concepts", []):
            concept_id = self._det_id("con", "concept", c["name_en"])
            id_concepto[c["name_en"]] = concept_id
            filas_concepto.append(
                {
                    "id": concept_id,
                    "tenant_id": self.tenant_id,
                    "name_en": c["name_en"],
                    "name_local": c.get("name_local", c["name_en"]),
                    "descripcion": c.get("description_en", ""),
                    "confianza": c.get("confidence"),
                }
            )

        aristas_relaciona = [
            {"src_id": id_concepto[r["from"]], "dst_id": id_concepto[r["to"]], "props": {"kind": r["kind"]}}
            for r in ontology.get("relations", [])
            if r["from"] in id_concepto and r["to"] in id_concepto
        ]
        aristas_expresa = [
            {
                "src_id": id_definicion[str(link["definition_id"])],
                "dst_id": id_concepto[link["concept_name_en"]],
                "props": {"confidence": link["confidence"]},
            }
            for link in links
            if str(link["definition_id"]) in id_definicion and link["concept_name_en"] in id_concepto
        ]

        with self._sesion() as db:
            ejecutar_lote(
                db,
                """
                INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion)
                VALUES (:id, :tenant_id, :modulo, :nombre, :kind, :descripcion)
                ON CONFLICT (tenant_id, id) DO UPDATE
                   SET modulo = EXCLUDED.modulo, nombre = EXCLUDED.nombre,
                       kind = EXCLUDED.kind, descripcion = EXCLUDED.descripcion
                """,
                filas_def,
            )
            ejecutar_lote(
                db,
                """
                INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion, confianza)
                VALUES (:id, :tenant_id, :name_en, :name_local, :descripcion, :confianza)
                ON CONFLICT (tenant_id, id) DO UPDATE
                   SET name_local = EXCLUDED.name_local, descripcion = EXCLUDED.descripcion,
                       confianza = EXCLUDED.confianza
                """,
                filas_concepto,
            )
            self._upsert_edges(db, "REALIZED_BY", "definition", "symbol", aristas_realiza)
            self._upsert_edges(db, "RELATES", "concept", "concept", aristas_relaciona)
            self._upsert_edges(db, "EXPRESSES", "definition", "concept", aristas_expresa)

    # ------------------------------------------------------------------
    # Refresco incremental (borrado; la reconciliación completa es CM-F05,
    # fuera de este ticket -- KC-ONB-05-1)
    # ------------------------------------------------------------------
    def delete_file_subgraph(self, paths: list[str]) -> None:
        """Borra los archivos dados, sus símbolos (ON DELETE CASCADE) y toda
        arista que los tocaba. cm.edges no tiene FK a los nodos (el destino
        varía según el tipo), así que las aristas se limpian a mano antes."""
        if not paths:
            return
        with self._sesion() as db:
            filas_symbol = todos(
                db,
                """
                SELECT s.id FROM cm.symbols s
                JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
                WHERE s.tenant_id = :tenant_id AND f.path = ANY(:paths)
                """,
                {"tenant_id": self.tenant_id, "paths": paths},
            )
            ids_afectados = [str(r["id"]) for r in filas_symbol]
            if ids_afectados:
                ejecutar(
                    db,
                    """
                    DELETE FROM cm.edges
                     WHERE tenant_id = :tenant_id AND (src_id = ANY(:ids) OR dst_id = ANY(:ids))
                    """,
                    {"tenant_id": self.tenant_id, "ids": ids_afectados},
                )
            ejecutar(
                db,
                "DELETE FROM cm.files WHERE tenant_id = :tenant_id AND path = ANY(:paths)",
                {"tenant_id": self.tenant_id, "paths": paths},
            )
