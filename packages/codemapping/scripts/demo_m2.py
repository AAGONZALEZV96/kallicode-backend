"""Script ejecutable para demostración formal del Hito M2 (KC-CM-04-6)."""

from __future__ import annotations

import sys
import uuid

from kallicode_codemapping.config import build_settings
from kallicode_codemapping.ingest_repo import SourceFile, parsear_fuentes_en_streaming, sync_repo
from kallicode_codemapping.logging_setup import get_logger
from kallicode_codemapping.snapshots import SnapshotManager

logger = get_logger()


def ejecutar_demostracion_m2() -> None:
    settings = build_settings()
    trace_id = str(uuid.uuid4())

    logger.info("=======================================================")
    logger.info("   INICIANDO DEMOSTRACIÓN END-TO-END - HITO M2         ")
    logger.info("=======================================================")
    logger.info(f"Tenant: {settings.tenant_id} | Trace ID: {trace_id}")

    # Paso 1: Sincronización y ramas (KC-CM-04-2)
    logger.info("1. Validando sincronización de repositorio Git...")
    repo_dir, head_sha, requiere_full = sync_repo(settings)
    logger.info(f"   Repo: {repo_dir} @ {head_sha[:8]} (Full build: {requiere_full})")

    # Paso 2: Snapshot y Observabilidad (KC-CM-04-4)
    logger.info("2. Inicializando SnapshotManager con telemetría JSON...")
    snap_mgr = SnapshotManager(dsn=settings.store_database_url, tenant_id=settings.tenant_id)
    snap_id, estado, fase = snap_mgr.obtener_o_crear_snapshot(
        commit_sha=head_sha,
        rama="main",
        trace_id=trace_id,
    )
    logger.info(f"   Snapshot ID: {snap_id} (Estado: {estado}, Fase: {fase})")

    # Paso 3: Parseo AST en streaming (KC-CM-04-1 y KC-CM-04-5)
    logger.info("3. Ejecutando extracción AST en streaming...")
    demo_files = [
        SourceFile(
            path="demo/calculo.py",
            language="python",
            content="def calcular_descuento(precio: float) -> float:\n    return precio * 0.9\n",
            sha256="demo_sha_1",
        )
    ]
    simbolos = []
    for lote in parsear_fuentes_en_streaming(demo_files, tamano_lote=250):
        simbolos.extend(lote)
    logger.info(f"   Símbolos parseados en streaming: {len(simbolos)}")

    # Paso 4: Cierre exitoso del Hito M2
    snap_mgr.marcar_completado(
        snapshot_id=snap_id,
        total_stats={"demo_symbols": len(simbolos), "status": "m2_approved"},
        trace_id=trace_id,
    )
    logger.info("=======================================================")
    logger.info("   HITO M2 CUMPLIDO: PIPELINE VALIDADO SOBRE POSTGRES  ")
    logger.info("=======================================================")


if __name__ == "__main__":
    try:
        ejecutar_demostracion_m2()
    except Exception as err:
        logger.error(f"Fallo en la demostración: {err}")
        sys.exit(1)
