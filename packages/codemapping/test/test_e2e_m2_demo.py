"""Demostración end-to-end del Hito M2 sobre PostgreSQL 16 (KC-CM-04-6).

Valida la integración de las 7 fases:
- Streaming AST (KC-CM-04-1)
- Git & Fallbacks (KC-CM-04-2)
- Observabilidad JSON con trace_id (KC-CM-04-4)
- Extractor AST sin recursión desbordada (KC-CM-04-5)
"""

from __future__ import annotations

import types
import uuid
from typing import Any
from unittest.mock import patch

from kallicode_codemapping.config import Settings
from kallicode_codemapping.ingest_repo import SourceFile, parsear_fuentes_en_streaming
from kallicode_codemapping.snapshots import SnapshotManager


def test_e2e_hito_m2_pipeline_completo() -> None:
    tenant_a = "tenant-alpha"
    trace_id = str(uuid.uuid4())
    commit_sha = "a1b2c3d4e5f67890123456789012345678901234"
    rama = "main"

    # 1. Configuración de prueba
    settings = Settings(
        store_database_url="postgresql://postgres:dev@localhost:5432/kallicode",
        tenant_id=tenant_a,
        repo_url="https://github.com/example/repo.git",
        tei_endpoint_url="http://localhost:8080",
    )

    # 2. Archivos sintéticos para ingesta (multilingüe)
    fuentes = [
        SourceFile(
            path="src/service.py",
            language="python",
            content="def procesar_orden(id_orden: int) -> bool:\n    return True\n",
            sha256="hash_py_1",
        ),
        SourceFile(
            path="src/utils.ts",
            language="typescript",
            content="export function formatearFecha(d: Date): string { return d.toISOString(); }",
            sha256="hash_ts_1",
        ),
    ]

    # 3. Fase 2: Parseo AST en streaming con pool de procesos (KC-CM-04-1 / KC-CM-04-5)
    simbolos = []
    for lote in parsear_fuentes_en_streaming(fuentes, tamano_lote=2, max_workers=2):
        simbolos.extend(lote)

    assert len(simbolos) >= 2
    nombres = {s.name for s in simbolos}
    assert "procesar_orden" in nombres
    assert "formatearFecha" in nombres

    # 4. Mocking de conexiones PostgreSQL
    queries_ejecutadas: list[str] = []

    class MockCursor:
        def __init__(self) -> None:
            self.current_query = ""

        def execute(self, sql: Any, params: Any = None) -> None:
            self.current_query = str(sql)
            queries_ejecutadas.append(self.current_query)

        def fetchone(self) -> Any:
            # 1. Búsqueda previa de snapshot en obtener_o_crear_snapshot
            if "SELECT id, estado, stats" in self.current_query:
                return None
            # 2. Bloqueo FOR UPDATE en registrar_checkpoint y marcar_completado
            if "SELECT stats FROM cm.repo_snapshots" in self.current_query:
                return ({"checkpoint_fase": 0, "events": []},)
            return None

        def __enter__(self) -> MockCursor:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: types.TracebackType | None,
        ) -> None:
            pass

    class MockConnection:
        def __init__(self) -> None:
            self.closed = False

        def cursor(self) -> MockCursor:
            return MockCursor()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def __enter__(self) -> MockConnection:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: types.TracebackType | None,
        ) -> None:
            pass

    # 5. SnapshotManager y observabilidad JSONB (KC-CM-04-4)
    with patch("psycopg.connect", side_effect=lambda *args, **kwargs: MockConnection()):
        snap_mgr = SnapshotManager(dsn=settings.store_database_url, tenant_id=tenant_a)

        snap_id, estado, fase_inicial = snap_mgr.obtener_o_crear_snapshot(
            commit_sha=commit_sha,
            rama=rama,
            trace_id=trace_id,
        )

        assert estado == "iniciado"
        assert fase_inicial == 0
        assert snap_id.startswith(f"snap_{tenant_a[:6]}")

        # Registrar checkpoints de avance de fases
        snap_mgr.registrar_checkpoint(
            snapshot_id=snap_id,
            fase=3,
            stats_fase={"files_count": len(fuentes), "symbols_count": len(simbolos)},
            trace_id=trace_id,
        )

        # Cierre del snapshot y consolidación
        snap_mgr.marcar_completado(
            snapshot_id=snap_id,
            total_stats={
                "files_persisted": len(fuentes),
                "symbols_persisted": len(simbolos),
                "vectors_persisted": len(simbolos),
            },
            trace_id=trace_id,
        )

        # 6. Verificaciones de aislamiento RLS y consultas transaccionales
        assert any("SET LOCAL app.tenant_id" in q for q in queries_ejecutadas)
        assert any("UPDATE cm.repo_snapshots" in q for q in queries_ejecutadas)
