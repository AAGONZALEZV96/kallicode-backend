from typing import Any
from neo4j import GraphDatabase

from .config import settings
from .ingest_repo import SourceFile
from .parser_treesitter import Symbol

CONSTRAINTS = [
    "CREATE CONSTRAINT file_path IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE",
    "CREATE CONSTRAINT symbol_id IF NOT EXISTS FOR (s:Symbol) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT def_id IF NOT EXISTS FOR (d:Definition) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT concept_name IF NOT EXISTS FOR (c:Concept) REQUIRE c.name_en IS UNIQUE",
    "CREATE CONSTRAINT table_name IF NOT EXISTS FOR (t:Table) REQUIRE t.name IS UNIQUE",
]


class GraphLoader:
    def __init__(self) -> None:
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )

    def setup(self) -> None:
        with self.driver.session() as s:
            for c in CONSTRAINTS:
                s.run(c)

    def load_files(self, files: list[SourceFile]) -> None:
        rows = [{"path": f.path, "language": f.language, "sha256": f.sha256} for f in files]
        with self.driver.session() as s:
            s.run(
                """
                UNWIND $rows AS r
                MERGE (f:File {path: r.path})
                SET f.language = r.language, f.sha256 = r.sha256,
                    f.client = $client
            """,
                rows=rows,
                client=settings.client_id,
            )

    def load_symbols(self, symbols: list[Symbol]) -> None:
        rows = [
            {
                "id": y.id,
                "file": y.file,
                "name": y.name,
                "kind": y.kind,
                "start": y.start_line,
                "end": y.end_line,
                "signature": y.signature,
            }
            for y in symbols
        ]
        calls = [{"from_id": y.id, "callee": c} for y in symbols for c in y.calls]
        with self.driver.session() as s:
            s.run(
                """
                UNWIND $rows AS r
                MERGE (sym:Symbol {id: r.id})
                SET sym += {name: r.name, kind: r.kind, start: r.start,
                            end: r.end, signature: r.signature}
                WITH sym, r
                MATCH (f:File {path: r.file})
                MERGE (f)-[:DEFINES]->(sym)
            """,
                rows=rows,
            )
            s.run(
                """
                UNWIND $calls AS c
                MATCH (a:Symbol {id: c.from_id})
                MATCH (b:Symbol {name: c.callee})
                WHERE a <> b
                MERGE (a)-[:CALLS]->(b)
            """,
                calls=calls,
            )

    def load_schema(
        self,
        tables: list[dict[str, Any]],
        fks: list[dict[str, Any]],
        usage: list[dict[str, str]],
    ) -> None:
        with self.driver.session() as s:
            s.run(
                """
                UNWIND $tables AS t
                MERGE (x:Table {name: t.name})
                WITH x, t UNWIND t.columns AS col
                MERGE (c:Column {id: t.name + '.' + col.name})
                SET c.name = col.name, c.type = col.type
                MERGE (x)-[:HAS_COLUMN]->(c)
            """,
                tables=tables,
            )
            s.run(
                """
                UNWIND $fks AS fk
                MATCH (a:Table {name: fk.from_table})
                MATCH (b:Table {name: fk.to_table})
                MERGE (a)-[:REFERENCES]->(b)
            """,
                fks=fks,
            )
            s.run(
                """
                UNWIND $usage AS u
                MATCH (f:File {path: u.file})
                MATCH (t:Table {name: u.table})
                MERGE (f)-[:USES_TABLE]->(t)
            """,
                usage=usage,
            )

    def load_semantic(
        self,
        definitions: list[dict[str, Any]],
        ontology: dict[str, Any],
        links: list[dict[str, Any]],
    ) -> None:
        with self.driver.session() as s:
            s.run(
                """
                UNWIND $defs AS d
                MERGE (x:Definition {id: d.id})
                SET x.name = d.name_en, x.kind = d.kind,
                    x.description = d.description_en
                WITH x, d UNWIND d.member_symbol_ids AS sid
                MATCH (sym:Symbol {id: sid})
                MERGE (x)-[:REALIZED_BY]->(sym)
            """,
                defs=definitions,
            )
            s.run(
                """
                UNWIND $concepts AS c
                MERGE (x:Concept {name_en: c.name_en})
                SET x.name_local = c.name_local,
                    x.description = c.description_en,
                    x.confidence = c.confidence
            """,
                concepts=ontology["concepts"],
            )
            s.run(
                """
                UNWIND $rels AS r
                MATCH (a:Concept {name_en: r.from})
                MATCH (b:Concept {name_en: r.to})
                MERGE (a)-[:RELATES {kind: r.kind}]->(b)
            """,
                rels=ontology["relations"],
            )
            s.run(
                """
                UNWIND $links AS l
                MATCH (d:Definition {id: l.definition_id})
                MATCH (c:Concept {name_en: l.concept_name_en})
                MERGE (d)-[e:EXPRESSES]->(c)
                SET e.confidence = l.confidence
            """,
                links=links,
            )

    def set_last_sha(self, sha: str) -> None:
        with self.driver.session() as s:
            s.run(
                "MERGE (m:Meta {client: $client}) SET m.last_sha = $sha",
                client=settings.client_id,
                sha=sha,
            )

    def get_last_sha(self) -> str | None:
        with self.driver.session() as s:
            rec = s.run(
                "MATCH (m:Meta {client: $client}) RETURN m.last_sha AS sha",
                client=settings.client_id,
            ).single()
            return str(rec["sha"]) if rec else None

    def delete_file_subgraph(self, paths: list[str]) -> None:
        with self.driver.session() as s:
            s.run(
                """
                UNWIND $paths AS p
                MATCH (f:File {path: p})
                OPTIONAL MATCH (f)-[:DEFINES]->(sym:Symbol)
                DETACH DELETE f, sym
            """,
                paths=paths,
            )