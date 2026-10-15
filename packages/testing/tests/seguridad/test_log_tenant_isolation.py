"""
Ticket: KC-QA-B2-10
Auditoría de Aislamiento de Logs entre Tenants.
Asegura que el contexto asíncrono (contextvars) no filtre el tenant_id
entre ejecuciones concurrentes, validando el JSON final en disco.
"""

import asyncio
import json
import logging
import random
import tempfile
from pathlib import Path

import pytest
import structlog

# Habilitar el soporte asíncrono para la suite
pytestmark = pytest.mark.asyncio


async def simulate_tenant_request(
    tenant_id: str, req_id: int, logger: structlog.BoundLogger
) -> None:
    """
    Simula una petición HTTP asíncrona que enlaza su tenant_id al contexto,
    realiza pausas (I/O) cediendo el control al event loop, y escribe un log.
    """
    # Limpiar y enlazar el contexto específico de este worker en memoria
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(tenant_id=tenant_id, req_id=req_id)

    # Simular I/O concurrente que provoca un cambio de contexto (context switch)
    await asyncio.sleep(random.uniform(0.01, 0.05))

    # Escribir log. Debe capturar automáticamente el tenant_id inyectado por contextvars
    logger.info("Transaccion procesada", expected_tenant=tenant_id)


async def test_log_tenant_isolation_concurrent_execution() -> None:
    """
    Verifica que cada línea de log corresponda unívocamente al tenant_id
    en contexto, sin mezcla de datos bajo alta concurrencia.
    """
    # Archivo temporal para simular el almacenamiento local (Estrategia Zero-Cloud)
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tmp_file:
        log_path = Path(tmp_file.name)

    try:
        # Configurar un logger aislado para la prueba
        logger_name = f"test_logger_{random.randint(1000, 9999)}"
        std_logger = logging.getLogger(logger_name)
        std_logger.setLevel(logging.INFO)
        std_logger.propagate = False  # Evitar ensuciar la salida de pytest en consola

        file_handler = logging.FileHandler(log_path)
        std_logger.addHandler(file_handler)

        # Forzar el pipeline de structlog a usar JSON y contextvars
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.JSONRenderer(),
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,
        )

        bound_logger = structlog.wrap_logger(std_logger)

        # Crear una carga concurrente masiva (100 peticiones simuladas simultáneas)
        tenants = ["org-alpha", "org-beta", "org-gamma"]
        tasks = []
        for i in range(100):
            tenant = random.choice(tenants)
            tasks.append(simulate_tenant_request(tenant, i, bound_logger))

        # Ejecutar todas las peticiones al mismo tiempo en el event loop
        await asyncio.gather(*tasks)

        # Volcar a disco de forma síncrona para asegurar la lectura
        file_handler.flush()
        file_handler.close()

        # FASE DE AUDITORÍA: Parsing Cero-Nube del archivo local
        log_lines = log_path.read_text(encoding="utf-8").strip().split("\n")

        assert len(log_lines) == 100, (
            f"Se esperaban 100 líneas de log, se encontraron {len(log_lines)}"
        )

        for line in log_lines:
            if not line.strip():
                continue

            data = json.loads(line)

            actual_tenant = data.get("tenant_id")
            expected_tenant = data.get("expected_tenant")
            req_id = data.get("req_id")

            assert actual_tenant is not None, (
                f"Fallo de Observabilidad: tenant_id ausente en request {req_id}"
            )
            assert actual_tenant == expected_tenant, (
                f"VULNERABILIDAD CRÍTICA (Fuga Cross-Tenant): "
                f"El request {req_id} correspondía al tenant '{expected_tenant}', "
                f"pero el motor de logs registró el contexto del tenant '{actual_tenant}'."
            )

    finally:
        # Limpieza del artefacto temporal
        if log_path.exists():
            log_path.unlink()
