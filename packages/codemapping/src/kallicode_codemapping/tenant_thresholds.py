"""Resolución de umbrales de confianza semántica por tenant (KC-CM-06-5).

Permite que cada organización calibre la tolerancia a enlaces dudosos antes
de derivarlos a cm.curation_queue.
"""

from __future__ import annotations

from typing import Any

import psycopg

UMBRAL_CONFIANZA_DEFAULT: float = 0.75


def obtener_umbral_confianza_tenant(
    conn: psycopg.Connection[Any] | None,
    tenant_id: str,
    override_umbral: float | None = None,
) -> float:
    """Obtiene el umbral de confianza calibrado para un tenant específico.

    Prioridad:
    1. override explícito pasado en el contexto de ejecución.
    2. Configuración persistida en base de datos (core.organizations / cm.tenant_settings).
    3. Valor predeterminado global (0.75).
    """
    if override_umbral is not None:
        if not (0.0 <= override_umbral <= 1.0):
            raise ValueError(
                f"El umbral de confianza debe estar en el rango [0.0, 1.0], recibido: {override_umbral}"
            )
        return float(override_umbral)

    if not conn or conn.closed:
        return UMBRAL_CONFIANZA_DEFAULT

    try:
        with conn.cursor() as cur:
            # Consultar si el tenant definió un umbral específico en sus metadatos/configuraciones
            cur.execute(
                """
                SELECT COALESCE(
                    (configuracion->>'link_confidence_threshold')::float,
                    %s
                )
                FROM core.organizations
                WHERE id = %s
                """,
                (UMBRAL_CONFIANZA_DEFAULT, tenant_id),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                valor = float(row[0])
                return max(0.0, min(1.0, valor))
    except Exception:
        # Degradación graciosa si la tabla u organización aún no posee la columna
        return UMBRAL_CONFIANZA_DEFAULT

    return UMBRAL_CONFIANZA_DEFAULT
