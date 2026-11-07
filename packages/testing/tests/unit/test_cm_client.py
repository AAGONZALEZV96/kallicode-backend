"""
Ticket: KC-QA-B3-01
Suite de pruebas unitarias y adversarias para CodeMappingClient (cm_client.py).
Valida aislamiento multi-tenant RLS en cm.*, recorrido de subgrafo hasta 2 saltos,
degradación elegante ante grafo vacío y prevención de inyección SQL en patrones de ruta.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from kallicode_testing.cm_client import CodeMappingClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

TENANT_A = "tenant_cm_alpha_b3_01"
TENANT_B = "tenant_cm_beta_b3_01"
TENANT_EMPTY = "tenant_cm_empty_b3_01"


async def _seed_tenant_graph(
    db_session: AsyncSession,
    tenant_id: str,
    prefix: str,
) -> None:
    """
    Siembra una organización y un grafo sintético completo en cm.* bajo el contexto RLS
    del tenant indicado, respetando la unicidad de IDs en cm.nodes (migración 022).
    """
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )

    await db_session.execute(
        text("""
            INSERT INTO core.organizations (id, nombre)
            VALUES (:id, :nombre)
            ON CONFLICT (id) DO NOTHING
        """),
        {"id": tenant_id, "nombre": f"Org {prefix}"},
    )

    snap_id = f"snap_{prefix}"
    file_id = f"file_{prefix}_tickets"
    sym_endpoint_id = f"sym_{prefix}_crear_ticket"
    sym_service_id = f"sym_{prefix}_guardar_ticket"
    sym_schema_id = f"sym_{prefix}_ticket_in"
    table_id = f"dbt_{prefix}_core_tickets"
    def_id = f"def_{prefix}_tickets_api"

    await db_session.execute(
        text("""
            INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado)
            VALUES (:id, :tenant_id, 'abc123456789', 'main', 'completo')
        """),
        {"id": snap_id, "tenant_id": tenant_id},
    )

    await db_session.execute(
        text("""
            INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id)
            VALUES (:id, :tenant_id, :path, 'python', 'deadbeef', :snap_id)
        """),
        {
            "id": file_id,
            "tenant_id": tenant_id,
            "path": f"apps/api/routers/tickets_{prefix}.py",
            "snap_id": snap_id,
        },
    )

    # Símbolo 1: Endpoint FastAPI
    await db_session.execute(
        text("""
            INSERT INTO cm.symbols
                (id, tenant_id, file_id, kind, name, start_line, end_line, signature, descripcion, role)
            VALUES
                (:id, :tenant_id, :file_id, 'function', :name, 10, 35, :sig, :desc, 'domain')
        """),
        {
            "id": sym_endpoint_id,
            "tenant_id": tenant_id,
            "file_id": file_id,
            "name": f"crear_ticket_{prefix}",
            "sig": f"@router.post('/api/v1/tickets') async def crear_ticket_{prefix}(datos: TicketIn_{prefix}) -> dict",
            "desc": "Endpoint de creación de tickets",
        },
    )

    # Símbolo 2: Función de servicio invocada por el endpoint (Salto 1: CALLS)
    await db_session.execute(
        text("""
            INSERT INTO cm.symbols
                (id, tenant_id, file_id, kind, name, start_line, end_line, signature, descripcion, role)
            VALUES
                (:id, :tenant_id, :file_id, 'function', :name, 40, 70, :sig, :desc, 'domain')
        """),
        {
            "id": sym_service_id,
            "tenant_id": tenant_id,
            "file_id": file_id,
            "name": f"guardar_ticket_db_{prefix}",
            "sig": f"async def guardar_ticket_db_{prefix}(db: AsyncSession, datos: TicketIn_{prefix}) -> str",
            "desc": "Persiste el ticket en core.tickets",
        },
    )

    # Símbolo 3: Esquema Pydantic en el mismo archivo
    await db_session.execute(
        text("""
            INSERT INTO cm.symbols
                (id, tenant_id, file_id, kind, name, start_line, end_line, signature, descripcion, role)
            VALUES
                (:id, :tenant_id, :file_id, 'class', :name, 1, 9, :sig, :desc, 'domain')
        """),
        {
            "id": sym_schema_id,
            "tenant_id": tenant_id,
            "file_id": file_id,
            "name": f"TicketIn_{prefix}",
            "sig": f"class TicketIn_{prefix}(BaseModel)",
            "desc": "Esquema Pydantic de entrada",
        },
    )

    # Tabla de base de datos usada en Salto 2 (USES_TABLE)
    await db_session.execute(
        text("""
            INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre, snapshot_id)
            VALUES (:id, :tenant_id, 'core', :nombre, :snap_id)
        """),
        {
            "id": table_id,
            "tenant_id": tenant_id,
            "nombre": f"tickets_{prefix}",
            "snap_id": snap_id,
        },
    )

    # Definición semántica del módulo
    await db_session.execute(
        text("""
            INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion, snapshot_id)
            VALUES (:id, :tenant_id, :modulo, :nombre, 'capability', :desc, :snap_id)
        """),
        {
            "id": def_id,
            "tenant_id": tenant_id,
            "modulo": f"routers/tickets_{prefix}",
            "nombre": f"Gestión de Tickets {prefix}",
            "desc": "Capacidad de alta de tickets con validación comercial",
            "snap_id": snap_id,
        },
    )

    # Aristas: DEFINES (file -> endpoint), CALLS (endpoint -> service), USES_TABLE (service -> db_table)
    await db_session.execute(
        text("""
            INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, props, snapshot_id)
            VALUES
                (:tenant_id, 'DEFINES', 'file', :file_id, 'symbol', :ep_id, '{"confidence": 1.0}'::jsonb, :snap_id),
                (:tenant_id, 'CALLS', 'symbol', :ep_id, 'symbol', :svc_id, '{"confidence": 0.95}'::jsonb, :snap_id),
                (:tenant_id, 'USES_TABLE', 'symbol', :svc_id, 'db_table', :tbl_id, '{"confidence": 0.92}'::jsonb, :snap_id)
        """),
        {
            "tenant_id": tenant_id,
            "file_id": file_id,
            "ep_id": sym_endpoint_id,
            "svc_id": sym_service_id,
            "tbl_id": table_id,
            "snap_id": snap_id,
        },
    )


async def test_get_symbols_por_tenant_exitoso(db_session: AsyncSession) -> None:
    """
    Prueba 1: Inserta símbolos sintéticos para Tenant A y Tenant B, consulta con Tenant A
    y verifica el aislamiento estricto multi-tenant (Tenant A nunca recibe datos de Tenant B).
    """
    await _seed_tenant_graph(db_session, TENANT_A, "alpha")
    await _seed_tenant_graph(db_session, TENANT_B, "beta")

    client_a = CodeMappingClient(db_session=db_session, tenant_id=TENANT_A)
    symbols_a = await client_a.get_symbols_by_path("apps/api/**")

    assert len(symbols_a) == 3, (
        f"Se esperaban 3 símbolos en Tenant A, se obtuvieron {len(symbols_a)}."
    )
    names_a = {s.name for s in symbols_a}
    assert "crear_ticket_alpha" in names_a
    assert "guardar_ticket_db_alpha" in names_a
    assert "TicketIn_alpha" in names_a

    # Verificar que ningún símbolo de Tenant B se haya filtrado a Tenant A
    for sym in symbols_a:
        assert "beta" not in sym.name
        assert "beta" not in sym.id
        assert sym.file_path == "apps/api/routers/tickets_alpha.py"


async def test_get_impacted_subgraph_recorrido_edges(db_session: AsyncSession) -> None:
    """
    Prueba 2: Consulta símbolo raíz y recorre relaciones CALLS, DEFINES y USES_TABLE
    resolviendo dependencias hasta 2 saltos, firmas de endpoints y healthcheck del grafo.
    """
    await _seed_tenant_graph(db_session, TENANT_A, "alpha")

    client_a = CodeMappingClient(db_session=db_session, tenant_id=TENANT_A)

    assert await client_a.healthcheck_graph() is True

    subgraph = await client_a.get_impacted_subgraph(
        symbol_names=["crear_ticket_alpha"],
        max_depth=2,
    )

    assert len(subgraph.symbols) == 2
    resolved_symbol_names = {s.name for s in subgraph.symbols}
    assert resolved_symbol_names == {"crear_ticket_alpha", "guardar_ticket_db_alpha"}

    assert len(subgraph.edges) >= 2
    edge_types = {e.tipo for e in subgraph.edges}
    assert "CALLS" in edge_types
    assert "USES_TABLE" in edge_types

    # Salto 2: el servicio usa la tabla core.tickets_alpha
    assert len(subgraph.db_tables) == 1
    assert subgraph.db_tables[0].esquema == "core"
    assert subgraph.db_tables[0].nombre == "tickets_alpha"

    # Verificar extracción de firmas de endpoints y esquemas Pydantic dentro del alcance
    endpoints = await client_a.get_endpoint_signatures(["apps/api/**"])
    assert len(endpoints) == 2
    post_ep = next(ep for ep in endpoints if ep.name == "crear_ticket_alpha")
    assert post_ep.http_method == "POST"
    assert post_ep.route_path == "/api/v1/tickets"
    assert "TicketIn_alpha" in post_ep.pydantic_schemas
    assert post_ep.definition_summary is not None
    assert "Gestión de Tickets alpha" in post_ep.definition_summary


async def test_degradacion_elegante_grafo_vacio(db_session: AsyncSession) -> None:
    """
    Prueba 3: En una base sin snapshots ni símbolos para el tenant, verifica que el cliente
    degrade elegantemente devolviendo DTOs vacíos (result.symbols == []), healthcheck False
    y registrando la advertencia estructurada sin lanzar excepciones no controladas.
    """
    await db_session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": TENANT_EMPTY},
    )
    await db_session.execute(
        text("""
            INSERT INTO core.organizations (id, nombre)
            VALUES (:id, 'Org Empty Graph')
            ON CONFLICT (id) DO NOTHING
        """),
        {"id": TENANT_EMPTY},
    )

    client_empty = CodeMappingClient(db_session=db_session, tenant_id=TENANT_EMPTY)

    with patch("kallicode_testing.cm_client.logger") as mock_logger:
        is_healthy = await client_empty.healthcheck_graph()
        assert is_healthy is False

        result = await client_empty.get_impacted_subgraph(["simbolo_inexistente"], max_depth=2)
        assert result.symbols == []
        assert result.edges == []
        assert result.db_tables == []

        empty_endpoints = await client_empty.get_endpoint_signatures(["apps/api/**"])
        assert empty_endpoints == []

        assert mock_logger.warning.called, "Debe registrarse una advertencia estructurada."


async def test_prevencion_sql_injection_en_path(db_session: AsyncSession) -> None:
    """
    Prueba 4: Inyecta payloads SQL maliciosos en path_pattern y verifica que SQLAlchemy
    parametrice la entrada al 100% sin interpolación ni destrucción de cm.symbols.
    """
    await _seed_tenant_graph(db_session, TENANT_A, "alpha")
    client_a = CodeMappingClient(db_session=db_session, tenant_id=TENANT_A)

    malicious_path = "'; DROP TABLE cm.symbols; --"
    symbols = await client_a.get_symbols_by_path(malicious_path)
    assert symbols == []

    endpoints = await client_a.get_endpoint_signatures([malicious_path])
    assert endpoints == []

    # Confirmar que la tabla cm.symbols y los símbolos de Tenant A siguen intactos
    legit_symbols = await client_a.get_symbols_by_path("apps/api/**")
    assert len(legit_symbols) == 3

    with pytest.raises(ValueError):
        CodeMappingClient(db_session=db_session, tenant_id="   ")
