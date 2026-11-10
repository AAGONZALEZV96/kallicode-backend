"""
Ticket: KC-QA-B3-01
Cliente de Introspección del Grafo CodeMapping (cm.*).
Consulta las tablas cm.symbols, cm.files, cm.edges, cm.db_tables y cm.definitions
sobre PostgreSQL 16 local con aislamiento multi-tenant RLS estricto.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_testing.logger import get_logger

logger = get_logger()

_HTTP_ROUTE_REGEX = re.compile(
    r"@(?:\w+\.)?(get|post|put|patch|delete)\(\s*['\"]([^'\"]*)['\"]",
    re.IGNORECASE,
)


class SymbolDTO(BaseModel):
    """Representa un símbolo determinista extraído de cm.symbols unido con cm.files."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    kind: str
    signature: str
    file_path: str
    start_line: int
    end_line: int
    descripcion: str | None = None
    role: str | None = None


class EdgeDTO(BaseModel):
    """Representa una arista dirigida en cm.edges."""

    model_config = ConfigDict(frozen=True)

    tipo: str
    src_kind: str
    src_id: str
    dst_kind: str
    dst_id: str
    props: dict[str, Any] = Field(default_factory=dict)


class DbTableDTO(BaseModel):
    """Representa una tabla de base de datos registrada en cm.db_tables."""

    model_config = ConfigDict(frozen=True)

    id: str
    esquema: str
    nombre: str


class SubgraphDTO(BaseModel):
    """Subgrafo de impacto resuelto a partir de símbolos raíz y aristas en cm.edges."""

    model_config = ConfigDict(frozen=True)

    root_symbols: list[str] = Field(default_factory=list)
    symbols: list[SymbolDTO] = Field(default_factory=list)
    edges: list[EdgeDTO] = Field(default_factory=list)
    db_tables: list[DbTableDTO] = Field(default_factory=list)


class EndpointSignatureDTO(BaseModel):
    """Firma enriquecida de un endpoint/router FastAPI y sus esquemas Pydantic asociados."""

    model_config = ConfigDict(frozen=True)

    symbol_id: str
    name: str
    signature: str
    file_path: str
    http_method: str | None = None
    route_path: str | None = None
    pydantic_schemas: list[str] = Field(default_factory=list)
    definition_summary: str | None = None


def _glob_to_sql_like(path_pattern: str) -> str:
    """
    Convierte un patrón glob (ej. 'apps/api/**' o 'apps/api/*.py') en un patrón LIKE
    seguro para ser pasado exclusivamente como parámetro ligado (:path_like).
    """
    cleaned = path_pattern.strip()
    if not cleaned:
        return "%"
    # Escapar comodines literales de SQL antes de traducir glob (* y ?)
    escaped = cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pattern = escaped.replace("**", "%").replace("*", "%").replace("?", "_")
    return like_pattern


class CodeMappingClient:
    """
    Cliente de introspección del grafo de conocimiento CodeMapping (esquema cm.*).
    Garantiza aislamiento multi-tenant explícito (RLS + filtro WHERE tenant_id = :tenant_id)
    y degradación elegante cuando el grafo está vacío o sin snapshots.
    """

    def __init__(self, db_session: AsyncSession, tenant_id: str) -> None:
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id es obligatorio para CodeMappingClient.")
        self._db: AsyncSession = db_session
        self.tenant_id: str = tenant_id.strip()

    async def _ensure_tenant_context(self) -> None:
        """Fija el tenant_id tanto en structlog.contextvars como en la sesión PostgreSQL RLS."""
        structlog.contextvars.bind_contextvars(tenant_id=self.tenant_id)
        await self._db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": self.tenant_id},
        )

    async def get_symbols_by_path(self, path_pattern: str) -> list[SymbolDTO]:
        """
        Consulta cm.symbols uniendo con cm.files y filtrando por tenant_id y patrón de ruta
        (ej. 'apps/api/**'). Devuelve lista vacía y registra advertencia si no hay coincidencias.
        """
        await self._ensure_tenant_context()
        like_pattern = _glob_to_sql_like(path_pattern)

        query = text("""
            SELECT s.id,
                   s.name,
                   s.kind,
                   s.signature,
                   f.path AS file_path,
                   s.start_line,
                   s.end_line,
                   s.descripcion,
                   s.role
            FROM cm.symbols s
            JOIN cm.files f
              ON f.tenant_id = s.tenant_id
             AND f.id = s.file_id
            WHERE s.tenant_id = :tenant_id
              AND f.tenant_id = :tenant_id
              AND f.path LIKE :path_like ESCAPE '\\'
            ORDER BY f.path ASC, s.start_line ASC
        """)

        try:
            result = await self._db.execute(
                query,
                {"tenant_id": self.tenant_id, "path_like": like_pattern},
            )
            rows = result.mappings().all()
        except SQLAlchemyError as exc:
            logger.warning(
                "cm.get_symbols_by_path.db_error",
                tenant_id=self.tenant_id,
                path_pattern=path_pattern,
                error=str(exc),
            )
            return []

        if not rows:
            logger.warning(
                "cm.get_symbols_by_path.empty",
                tenant_id=self.tenant_id,
                path_pattern=path_pattern,
            )
            return []

        return [
            SymbolDTO(
                id=str(r["id"]),
                name=str(r["name"]),
                kind=str(r["kind"]),
                signature=str(r["signature"]),
                file_path=str(r["file_path"]),
                start_line=int(r["start_line"]),
                end_line=int(r["end_line"]),
                descripcion=str(r["descripcion"]) if r["descripcion"] is not None else None,
                role=str(r["role"]) if r["role"] is not None else None,
            )
            for r in rows
        ]

    async def get_impacted_subgraph(
        self,
        symbol_names: list[str],
        max_depth: int = 2,
    ) -> SubgraphDTO:
        """
        Recorre cm.edges resolviendo relaciones ('CALLS', 'DEFINES', 'USES_TABLE')
        hasta max_depth saltos desde los símbolos semilla indicados.
        """
        await self._ensure_tenant_context()

        clean_names = [n.strip() for n in symbol_names if n and n.strip()]
        if not clean_names or max_depth < 1:
            logger.warning(
                "cm.get_impacted_subgraph.empty_input",
                tenant_id=self.tenant_id,
                symbol_names=symbol_names,
                max_depth=max_depth,
            )
            return SubgraphDTO(root_symbols=clean_names)

        seed_query = text("""
            SELECT s.id,
                   s.name,
                   s.kind,
                   s.signature,
                   f.path AS file_path,
                   s.start_line,
                   s.end_line,
                   s.descripcion,
                   s.role
            FROM cm.symbols s
            JOIN cm.files f
              ON f.tenant_id = s.tenant_id
             AND f.id = s.file_id
            WHERE s.tenant_id = :tenant_id
              AND f.tenant_id = :tenant_id
              AND s.name = ANY(:symbol_names)
        """)

        try:
            seed_res = await self._db.execute(
                seed_query,
                {"tenant_id": self.tenant_id, "symbol_names": clean_names},
            )
            seed_rows = seed_res.mappings().all()
        except SQLAlchemyError as exc:
            logger.warning(
                "cm.get_impacted_subgraph.db_error",
                tenant_id=self.tenant_id,
                error=str(exc),
            )
            return SubgraphDTO(root_symbols=clean_names)

        if not seed_rows:
            logger.warning(
                "cm.get_impacted_subgraph.empty_graph",
                tenant_id=self.tenant_id,
                symbol_names=clean_names,
            )
            return SubgraphDTO(root_symbols=clean_names)

        symbols_by_id: dict[str, SymbolDTO] = {}
        frontier_ids: set[str] = set()
        visited_node_ids: set[str] = set()

        for row in seed_rows:
            sym = SymbolDTO(
                id=str(row["id"]),
                name=str(row["name"]),
                kind=str(row["kind"]),
                signature=str(row["signature"]),
                file_path=str(row["file_path"]),
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                descripcion=str(row["descripcion"]) if row["descripcion"] is not None else None,
                role=str(row["role"]) if row["role"] is not None else None,
            )
            symbols_by_id[sym.id] = sym
            frontier_ids.add(sym.id)
            visited_node_ids.add(sym.id)

        edges_collected: dict[tuple[str, str, str], EdgeDTO] = {}
        table_ids_to_fetch: set[str] = set()
        symbol_ids_to_fetch: set[str] = set()

        edge_query = text("""
            SELECT tipo, src_kind, src_id, dst_kind, dst_id, props
            FROM cm.edges
            WHERE tenant_id = :tenant_id
              AND tipo IN ('CALLS', 'DEFINES', 'USES_TABLE')
              AND (src_id = ANY(:frontier) OR dst_id = ANY(:frontier))
        """)

        for _depth in range(max_depth):
            if not frontier_ids:
                break

            edge_res = await self._db.execute(
                edge_query,
                {"tenant_id": self.tenant_id, "frontier": list(frontier_ids)},
            )
            edge_rows = edge_res.mappings().all()
            next_frontier: set[str] = set()

            for erow in edge_rows:
                tipo = str(erow["tipo"])
                src_kind = str(erow["src_kind"])
                src_id = str(erow["src_id"])
                dst_kind = str(erow["dst_kind"])
                dst_id = str(erow["dst_id"])
                raw_props = erow["props"]
                props_dict: dict[str, Any] = dict(raw_props) if isinstance(raw_props, dict) else {}

                edge_key = (tipo, src_id, dst_id)
                if edge_key not in edges_collected:
                    edges_collected[edge_key] = EdgeDTO(
                        tipo=tipo,
                        src_kind=src_kind,
                        src_id=src_id,
                        dst_kind=dst_kind,
                        dst_id=dst_id,
                        props=props_dict,
                    )

                for n_kind, n_id in ((src_kind, src_id), (dst_kind, dst_id)):
                    if n_kind == "symbol" and n_id not in symbols_by_id:
                        symbol_ids_to_fetch.add(n_id)
                    elif n_kind == "db_table":
                        table_ids_to_fetch.add(n_id)

                    if n_id not in visited_node_ids:
                        visited_node_ids.add(n_id)
                        if n_kind in ("symbol", "file"):
                            next_frontier.add(n_id)

            frontier_ids = next_frontier

        if symbol_ids_to_fetch:
            extra_sym_query = text("""
                SELECT s.id,
                       s.name,
                       s.kind,
                       s.signature,
                       f.path AS file_path,
                       s.start_line,
                       s.end_line,
                       s.descripcion,
                       s.role
                FROM cm.symbols s
                JOIN cm.files f
                  ON f.tenant_id = s.tenant_id
                 AND f.id = s.file_id
                WHERE s.tenant_id = :tenant_id
                  AND f.tenant_id = :tenant_id
                  AND s.id = ANY(:sym_ids)
            """)
            extra_res = await self._db.execute(
                extra_sym_query,
                {"tenant_id": self.tenant_id, "sym_ids": list(symbol_ids_to_fetch)},
            )
            for srow in extra_res.mappings().all():
                sym = SymbolDTO(
                    id=str(srow["id"]),
                    name=str(srow["name"]),
                    kind=str(srow["kind"]),
                    signature=str(srow["signature"]),
                    file_path=str(srow["file_path"]),
                    start_line=int(srow["start_line"]),
                    end_line=int(srow["end_line"]),
                    descripcion=(
                        str(srow["descripcion"]) if srow["descripcion"] is not None else None
                    ),
                    role=str(srow["role"]) if srow["role"] is not None else None,
                )
                symbols_by_id[sym.id] = sym

        db_tables: list[DbTableDTO] = []
        if table_ids_to_fetch:
            tbl_query = text("""
                SELECT id, esquema, nombre
                FROM cm.db_tables
                WHERE tenant_id = :tenant_id
                  AND id = ANY(:tbl_ids)
                ORDER BY esquema ASC, nombre ASC
            """)
            tbl_res = await self._db.execute(
                tbl_query,
                {"tenant_id": self.tenant_id, "tbl_ids": list(table_ids_to_fetch)},
            )
            db_tables = [
                DbTableDTO(
                    id=str(trow["id"]),
                    esquema=str(trow["esquema"]),
                    nombre=str(trow["nombre"]),
                )
                for trow in tbl_res.mappings().all()
            ]

        return SubgraphDTO(
            root_symbols=clean_names,
            symbols=list(symbols_by_id.values()),
            edges=list(edges_collected.values()),
            db_tables=db_tables,
        )

    async def get_endpoint_signatures(
        self,
        ticket_scope_paths: list[str],
    ) -> list[EndpointSignatureDTO]:
        """
        Recupera firmas de métodos, routers FastAPI y esquemas Pydantic asociados
        dentro de las rutas de alcance del ticket.
        """
        await self._ensure_tenant_context()

        if not ticket_scope_paths:
            logger.warning(
                "cm.get_endpoint_signatures.empty_scope",
                tenant_id=self.tenant_id,
            )
            return []

        all_symbols: dict[str, SymbolDTO] = {}
        for scope_pattern in ticket_scope_paths:
            matched = await self.get_symbols_by_path(scope_pattern)
            for sym in matched:
                all_symbols[sym.id] = sym

        if not all_symbols:
            logger.warning(
                "cm.get_endpoint_signatures.no_symbols",
                tenant_id=self.tenant_id,
                ticket_scope_paths=ticket_scope_paths,
            )
            return []

        definitions_by_module: dict[str, str] = {}
        try:
            def_query = text("""
                SELECT modulo, nombre, descripcion
                FROM cm.definitions
                WHERE tenant_id = :tenant_id
            """)
            def_res = await self._db.execute(def_query, {"tenant_id": self.tenant_id})
            for drow in def_res.mappings().all():
                mod_key = str(drow["modulo"])
                definitions_by_module[mod_key] = f"{drow['nombre']}: {drow['descripcion']}"
        except SQLAlchemyError as exc:
            logger.warning(
                "cm.get_endpoint_signatures.definitions_error",
                tenant_id=self.tenant_id,
                error=str(exc),
            )

        # Clasificar clases (esquemas Pydantic) por archivo para asociarlas a los endpoints
        schemas_by_file: dict[str, list[str]] = {}
        for sym in all_symbols.values():
            if sym.kind == "class":
                schemas_by_file.setdefault(sym.file_path, []).append(sym.name)

        endpoints: list[EndpointSignatureDTO] = []
        for sym in all_symbols.values():
            if sym.kind not in ("function", "method"):
                continue

            http_method: str | None = None
            route_path: str | None = None
            match = _HTTP_ROUTE_REGEX.search(sym.signature) or (
                _HTTP_ROUTE_REGEX.search(sym.descripcion) if sym.descripcion else None
            )
            if match:
                http_method = match.group(1).upper()
                route_path = match.group(2)

            file_schemas = schemas_by_file.get(sym.file_path, [])
            referenced_schemas = [
                schema_name
                for schema_name in file_schemas
                if schema_name in sym.signature or not match
            ]

            def_summary: str | None = None
            for mod_name, summary in definitions_by_module.items():
                if mod_name in sym.file_path:
                    def_summary = summary
                    break

            endpoints.append(
                EndpointSignatureDTO(
                    symbol_id=sym.id,
                    name=sym.name,
                    signature=sym.signature,
                    file_path=sym.file_path,
                    http_method=http_method,
                    route_path=route_path,
                    pydantic_schemas=referenced_schemas or file_schemas,
                    definition_summary=def_summary or sym.descripcion,
                )
            )

        return endpoints

    async def healthcheck_graph(self) -> bool:
        """
        Verifica la disponibilidad del esquema cm.* y confirma si el tenant actual
        posee un grafo ingerido con al menos un snapshot en estado 'completo' y símbolos activos.
        """
        await self._ensure_tenant_context()

        schema_query = text("""
            SELECT (
                to_regclass('cm.repo_snapshots') IS NOT NULL
                AND to_regclass('cm.files') IS NOT NULL
                AND to_regclass('cm.symbols') IS NOT NULL
                AND to_regclass('cm.edges') IS NOT NULL
            ) AS schema_ready
        """)

        try:
            schema_res = await self._db.execute(schema_query)
            if not bool(schema_res.scalar()):
                logger.warning("cm.healthcheck.schema_missing", tenant_id=self.tenant_id)
                return False

            data_query = text("""
                SELECT (
                    EXISTS (
                        SELECT 1 FROM cm.repo_snapshots
                        WHERE tenant_id = :tenant_id
                          AND estado = 'completo'
                    )
                    AND EXISTS (
                        SELECT 1 FROM cm.symbols
                        WHERE tenant_id = :tenant_id
                    )
                ) AS graph_ingested
            """)
            data_res = await self._db.execute(data_query, {"tenant_id": self.tenant_id})
            is_healthy = bool(data_res.scalar())
            if not is_healthy:
                logger.warning("cm.healthcheck.empty_or_incomplete", tenant_id=self.tenant_id)
            return is_healthy
        except SQLAlchemyError as exc:
            logger.warning(
                "cm.healthcheck.db_error",
                tenant_id=self.tenant_id,
                error=str(exc),
            )
            return False
