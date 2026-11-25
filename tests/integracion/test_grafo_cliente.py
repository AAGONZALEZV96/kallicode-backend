"""Tests de integración para GrafoClientePostgres e impacto en features (KC-BE-10-6).

Verifica:
- Consultas reales sobre el esquema `cm` y `vec` con aislamiento RLS por tenant.
- `subgrafo_ticket`, `impacto` (con protección ante ciclos y límite `k`) e `inventario_ui`.
- Tenant sin grafo ingestado devuelve subgrafo/impacto vacíos sin error.
- `POST /api/v1/tickets/features` devuelve `impacto_estimado.tablas` derivado del grafo
  (o `[]` si el tenant no tiene grafo o la DB de consulta falla).
- Recorrido comparativo de las 6 etapas del orquestador (`triage`, `design`, `build`,
  `qa`, `security`, `deploy`) con `GrafoClienteDoble` frente a `GrafoClientePostgres`.
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from kallicode_api.routers import tickets as router_tickets
from kallicode_api.seguridad import crear_access_token
from kallicode_core import consultas_cm
from kallicode_core.db import sesion_sistema, sesion_tenant
from kallicode_core.grafo import (
    GrafoClienteDoble,
    GrafoClientePostgres,
    grafo_cliente,
)
from kallicode_core.llm import cliente as llm_cliente
from kallicode_core.llm.client import ResultadoPaso
from kallicode_core.llm.tiers import Tier
from kallicode_workers.orquestador import build, deploy, design, etapas, qa, security
from sqlalchemy import text

VECTOR_BASE = "[" + ",".join(["0.01"] * 1024) + "]"


def _db_disponible() -> bool:
    url = os.getenv("KC_DATABASE_URL", "")
    host = "127.0.0.1"
    port = 5432
    if "@" in url:
        resto = url.split("@", 1)[1].split("/", 1)[0]
        if ":" in resto:
            host, port_s = resto.split(":", 1)
            if port_s.isdigit():
                port = int(port_s)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _db_disponible(),
        reason="PostgreSQL/Supabase no disponible en este entorno",
    ),
]


async def _limpiar_cm(tenant_id: str) -> None:
    async with sesion_sistema() as db:
        for tabla in (
            "vec.definition_embeddings",
            "vec.ticket_embeddings",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.db_columns",
            "cm.db_tables",
            "cm.definitions",
            "cm.concepts",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
        ):
            await db.execute(
                text(f"DELETE FROM {tabla} WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        await db.commit()


async def _sembrar_grafo_tenant(tenant_id: str, *, sufijo: str) -> dict[str, str]:
    """Siembra un grafo realista en `cm` + `vec` con ciclo y componentes UI para `tenant_id`."""
    snap_id = f"snap_{sufijo}"
    file_inv = f"file_inv_{sufijo}"
    file_tax = f"file_tax_{sufijo}"
    file_ui = f"file_ui_{sufijo}"
    sym_calc = f"sym_calc_{sufijo}"
    sym_tax = f"sym_tax_{sufijo}"
    sym_ciclo = f"sym_ciclo_{sufijo}"
    sym_ui = f"sym_ui_{sufijo}"
    tbl_inv = f"dbt_invoices_{sufijo}"
    tbl_tax = f"dbt_tax_rates_{sufijo}"
    def_inv = f"def_billing_{sufijo}"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """
                INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama)
                VALUES (:s, :t, :sha, 'main')
                """
            ),
            {"s": snap_id, "t": tenant_id, "sha": f"sha_{sufijo}"},
        )
        await db.execute(
            text(
                """
                INSERT INTO cm.files (id, tenant_id, snapshot_id, path, language, sha256, lines)
                VALUES
                    (:finv, :t, :s, 'src/billing/invoice.py', 'python', 'h1', 120),
                    (:ftax, :t, :s, 'src/billing/tax.py', 'python', 'h2', 80),
                    (:fui,  :t, :s, 'src/ui/BillingPanel.tsx', 'tsx', 'h3', 95)
                """
            ),
            {"finv": file_inv, "ftax": file_tax, "fui": file_ui, "t": tenant_id, "s": snap_id},
        )
        await db.execute(
            text(
                """
                INSERT INTO cm.symbols
                    (id, tenant_id, file_id, kind, name, qualified_name, start_line, end_line, signature_hash)
                VALUES
                    (:scalc,  :t, :finv, 'function', 'calcular_factura', 'billing.invoice.calcular_factura', 10, 45, 'sh1'),
                    (:stax,   :t, :ftax, 'function', 'obtener_iva', 'billing.tax.obtener_iva', 5, 30, 'sh2'),
                    (:sciclo, :t, :ftax, 'function', 'recalcular_ciclo', 'billing.tax.recalcular_ciclo', 32, 50, 'sh3'),
                    (:sui,    :t, :fui,  'component', 'BillingPanel', 'ui.BillingPanel', 1, 90, 'sh4')
                """
            ),
            {
                "scalc": sym_calc,
                "stax": sym_tax,
                "sciclo": sym_ciclo,
                "sui": sym_ui,
                "finv": file_inv,
                "ftax": file_tax,
                "fui": file_ui,
                "t": tenant_id,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre)
                VALUES
                    (:tinv, :t, 'public', 'invoices'),
                    (:ttax, :t, 'billing', 'tax_rates')
                """
            ),
            {"tinv": tbl_inv, "ttax": tbl_tax, "t": tenant_id},
        )
        await db.execute(
            text(
                """
                INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion)
                VALUES (:dinv, :t, 'src/billing', 'FacturacionCore', 'module', 'Cálculo de facturas e impuestos')
                """
            ),
            {"dinv": def_inv, "t": tenant_id},
        )
        # Aristas: incluye ciclo sym_tax -> sym_ciclo -> sym_tax para verificar CYCLE.
        await db.execute(
            text(
                """
                INSERT INTO cm.edges
                    (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, origen, evidencia)
                VALUES
                    (:t, 'DEFINES',     'file',       :finv,   'symbol',   :scalc,  'ast', '{}'::jsonb),
                    (:t, 'DEFINES',     'file',       :ftax,   'symbol',   :stax,   'ast', '{}'::jsonb),
                    (:t, 'DEFINES',     'file',       :fui,    'symbol',   :sui,    'ast', '{}'::jsonb),
                    (:t, 'CALLS',       'symbol',     :scalc,  'symbol',   :stax,   'ast', '{}'::jsonb),
                    (:t, 'CALLS',       'symbol',     :stax,   'symbol',   :sciclo, 'ast', '{}'::jsonb),
                    (:t, 'CALLS',       'symbol',     :sciclo, 'symbol',   :stax,   'ast', '{}'::jsonb),
                    (:t, 'CALLS',       'symbol',     :sui,    'symbol',   :scalc,  'ast', '{}'::jsonb),
                    (:t, 'USES_TABLE',  'symbol',     :scalc,  'db_table', :tinv,   'sql', '{}'::jsonb),
                    (:t, 'USES_TABLE',  'symbol',     :stax,   'db_table', :ttax,   'sql', '{}'::jsonb),
                    (:t, 'REALIZED_BY', 'definition', :dinv,   'symbol',   :scalc,  'doc', '{}'::jsonb)
                """
            ),
            {
                "t": tenant_id,
                "finv": file_inv,
                "ftax": file_tax,
                "fui": file_ui,
                "scalc": sym_calc,
                "stax": sym_tax,
                "sciclo": sym_ciclo,
                "sui": sym_ui,
                "tinv": tbl_inv,
                "ttax": tbl_tax,
                "dinv": def_inv,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO vec.definition_embeddings
                    (id, tenant_id, nodo_ref, capa, nombre, descripcion, modulo, embedding, texto_hash)
                VALUES
                    (:deid, :t, :scalc, 'symbol', 'calcular_factura', 'Calcula totales e IVA', 'src/billing', CAST(:emb AS vector), 'th1')
                """
            ),
            {"deid": f"de_{sufijo}", "t": tenant_id, "scalc": sym_calc, "emb": VECTOR_BASE},
        )
        await db.commit()

    return {
        "file_inv": file_inv,
        "file_tax": file_tax,
        "file_ui": file_ui,
        "sym_calc": sym_calc,
        "sym_tax": sym_tax,
        "sym_ciclo": sym_ciclo,
        "sym_ui": sym_ui,
        "tbl_inv": tbl_inv,
        "tbl_tax": tbl_tax,
        "def_inv": def_inv,
    }


@pytest_asyncio.fixture
async def usuario_tenant_a(seed_dos_tenants):
    tenant_a, _ = seed_dos_tenants
    user_id = str(uuid.uuid4())
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """
                INSERT INTO core.users (id, tenant_id, email, nombre, rol, activo)
                VALUES (:u, :t, :email, 'Arquitecta A', 'architect', true)
                """
            ),
            {"u": user_id, "t": tenant_a, "email": f"arq_{user_id[:8]}@test.dev"},
        )
        await db.commit()
    token, _ = crear_access_token(
        user_id=user_id,
        tenant_id=tenant_a,
        rol="architect",
        session_id=str(uuid.uuid4()),
    )
    return tenant_a, user_id, {"Authorization": f"Bearer {token}"}


async def test_grafo_cliente_subgrafo_impacto_ui_y_aislamiento_rls(seed_dos_tenants) -> None:
    tenant_a, tenant_b = seed_dos_tenants
    try:
        ids_a = await _sembrar_grafo_tenant(tenant_a, sufijo="a1")
        async with sesion_sistema() as db:
            tk_id = str(uuid.uuid4())
            fila = (
                await db.execute(
                    text(
                        """
                        INSERT INTO core.tickets
                            (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, modulo_pista)
                        VALUES
                            (:id, :t, 'Ajustar cálculo IVA', 'Revisar tasas en billing', 'mejora', 'media', 'triage', 'src/billing')
                        RETURNING numero
                        """
                    ),
                    {"id": tk_id, "t": tenant_a},
                )
            ).mappings().one()
            await db.commit()
            numero_a = fila["numero"]

        cliente = grafo_cliente()
        assert isinstance(cliente, GrafoClientePostgres)

        # 1. Subgrafo real del ticket en tenant_a.
        sub_a = await cliente.subgrafo_ticket(tenant_a, numero_a)
        assert not sub_a.esta_vacio()
        nodos_ids = {n["id"] for n in sub_a.nodos}
        assert ids_a["sym_calc"] in nodos_ids
        assert ids_a["sym_tax"] in nodos_ids
        assert any(a["tipo"] == "CALLS" for a in sub_a.aristas)

        # 2. Impacto con ciclo (sym_tax <-> sym_ciclo) y límite k=2 termina sin colgarse.
        imp_a = await cliente.impacto(tenant_a, ids_a["sym_tax"], k=2)
        assert not imp_a.esta_vacio()
        assert imp_a.radio >= 1
        assert "src/billing" in imp_a.modulos
        assert "billing.tax_rates" in imp_a.tablas
        assert "invoices" in imp_a.tablas

        # 3. Inventario UI devuelve archivos y componentes UI de tenant_a.
        ui_a = await cliente.inventario_ui(tenant_a)
        assert len(ui_a) >= 2
        assert any(item["path"] == "src/ui/BillingPanel.tsx" for item in ui_a)
        assert any(item["nombre"] == "BillingPanel" for item in ui_a)

        # 4. Aislamiento RLS: tenant_b (sin grafo) recibe respuestas vacías y nunca ve datos de tenant_a.
        assert (await cliente.subgrafo_ticket(tenant_b, numero_a)).esta_vacio()
        assert (await cliente.impacto(tenant_b, ids_a["sym_tax"], k=2)).esta_vacio()
        assert await cliente.inventario_ui(tenant_b) == []
    finally:
        await _limpiar_cm(tenant_a)
        await _limpiar_cm(tenant_b)


async def test_crear_feature_expone_impacto_estimado_tablas(
    client,
    usuario_tenant_a,
    monkeypatch,
) -> None:
    tenant_a, _, headers = usuario_tenant_a
    try:
        # 1. Sin grafo ingestado para el tenant -> tablas == [] sin fallar.
        monkeypatch.setattr(router_tickets, "_embeber", AsyncMock(side_effect=RuntimeError("offline")))
        resp_vacia = await client.post(
            "/api/v1/tickets/features",
            headers=headers,
            json={
                "nombre": "Exportación contable inicial",
                "objetivo": "Permitir exportar asientos contables básicos desde el portal",
                "modulos_estimados": ["src/billing"],
            },
        )
        assert resp_vacia.status_code == 201, resp_vacia.text
        cuerpo_vacio = resp_vacia.json()
        assert cuerpo_vacio["impacto_estimado"]["modulos"] == ["src/billing"]
        assert cuerpo_vacio["impacto_estimado"]["tablas"] == []

        # 2. Con grafo ingestado en cm -> tablas pobladas desde cm.db_tables.
        await _sembrar_grafo_tenant(tenant_a, sufijo="feat")
        emb_falso = [0.01] * 1024
        monkeypatch.setattr(router_tickets, "_embeber", AsyncMock(return_value=emb_falso))

        resp_con_grafo = await client.post(
            "/api/v1/tickets/features",
            headers=headers,
            json={
                "nombre": "Desglose de impuestos por factura",
                "objetivo": "Calcular el desglose de IVA por línea en cada factura emitida",
                "modulos_estimados": ["src/billing"],
            },
        )
        assert resp_con_grafo.status_code == 201, resp_con_grafo.text
        cuerpo_grafo = resp_con_grafo.json()
        assert cuerpo_grafo["impacto_estimado"]["modulos"] == ["src/billing"]
        assert "invoices" in cuerpo_grafo["impacto_estimado"]["tablas"]
        assert "billing.tax_rates" in cuerpo_grafo["impacto_estimado"]["tablas"]

        # 3. Si la consulta de tablas falla tras crear el ticket, devuelve [] sin romper el alta.
        with patch.object(
            router_tickets,
            "estimar_tablas_feature",
            AsyncMock(side_effect=RuntimeError("timeout grafo")),
        ):
            resp_fallback = await client.post(
                "/api/v1/tickets/features",
                headers=headers,
                json={
                    "nombre": "Nota de crédito electrónica",
                    "objetivo": "Emitir notas de crédito asociadas a facturas existentes",
                    "modulos_estimados": ["src/billing"],
                },
            )
        assert resp_fallback.status_code == 201, resp_fallback.text
        assert resp_fallback.json()["impacto_estimado"]["tablas"] == []
    finally:
        await _limpiar_cm(tenant_a)


async def test_hnsw_ef_search_es_local_a_la_transaccion(seed_dos_tenants) -> None:
    """Verifica que `vec.configurar_busqueda` aplica `SET LOCAL hnsw.ef_search`."""
    tenant_a, _ = seed_dos_tenants
    async with sesion_tenant(tenant_a, actor="test:hnsw") as db:
        await consultas_cm.estimar_tablas_feature(
            db,
            modulos_estimados=[],
            embedding=[0.01] * 1024,
        )
        ef_en_tx = (
            await db.execute(text("SELECT current_setting('hnsw.ef_search', true)"))
        ).scalar()
        assert ef_en_tx == "400"

    # Fuera de la transacción, el ajuste local no debe persistir en la conexión del pool.
    async with sesion_tenant(tenant_a, actor="test:hnsw_post") as db2:
        ef_fuera = (
            await db2.execute(text("SELECT current_setting('hnsw.ef_search', true)"))
        ).scalar()
        assert ef_fuera != "400"


async def test_comparativa_seis_etapas_doble_vs_postgres(
    seed_dos_tenants,
    monkeypatch,
) -> None:
    """Ejecuta las 6 etapas del orquestador con GrafoClienteDoble y GrafoClientePostgres."""
    tenant_a, _ = seed_dos_tenants
    try:
        ids = await _sembrar_grafo_tenant(tenant_a, sufijo="orch")
        async with sesion_sistema() as db:
            tk_id = str(uuid.uuid4())
            tk = (
                await db.execute(
                    text(
                        """
                        INSERT INTO core.tickets
                            (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, modulo_pista)
                        VALUES
                            (:id, :t, 'Actualizar tasa IVA', 'Actualizar cálculo de IVA en billing', 'mejora', 'media', 'triage', 'src/billing')
                        RETURNING id, numero, titulo, descripcion, tipo, prioridad, etapa
                        """
                    ),
                    {"id": tk_id, "t": tenant_a},
                )
            ).mappings().one()
            await db.commit()

        entradas_capturadas: dict[str, dict[str, dict]] = {"doble": {}, "postgres": {}}
        modo_actual = "doble"

        salidas_llm: dict[str, dict] = {
            "triage.classify": {"tipo": "mejora", "prioridad": "media", "modulo": "src/billing"},
            "triage.impact": {"impacto_resumen": "Afecta billing", "riesgo": "medio"},
            "triage.estimate": {"complejidad": "media", "horas_estimadas": 4},
            "design.digest": {"resumen": "Digest de diseño"},
            "design.spec": {
                "spec_md": "# Spec\nActualizar IVA",
                "scope_paths": ["src/billing/"],
                "criterios": [{"given": "G", "when": "W", "then": "T"}],
            },
            "design.adversarial_review": {"hallazgos": [], "aprobado": True},
            "build.plan": {"pasos": ["Editar tax.py"]},
            "build.diff": {
                "diff": "--- a/src/billing/tax.py\n+++ b/src/billing/tax.py\n@@ -1 +1 @@\n-iva = 0.19\n+iva = 0.21\n",
                "needs_replan": False,
            },
            "qa.failure_diagnosis": {"causa_raiz": "ninguna"},
            "security.exploitability": {"hallazgos": [], "riesgo": "bajo"},
            "deploy.anomaly": {"anomalia": False, "resumen": "Despliegue normal"},
        }

        async def fake_ejecutar_paso(paso_id: str, entrada: dict, **kwargs) -> ResultadoPaso:
            entradas_capturadas[modo_actual][paso_id] = dict(entrada)
            return ResultadoPaso(
                paso_id=paso_id,
                version="v1",
                modelo="modelo-test",
                tier=Tier.FLASH,
                salida=salidas_llm.get(paso_id, {"ok": True}),
                confianza=0.92,
                validacion="ok",
                intentos=[],
                tokens_in=50,
                tokens_out=30,
                duracion_ms=12,
                consumo_registrado=True,
            )

        monkeypatch.setattr(llm_cliente, "ejecutar_paso", fake_ejecutar_paso)

        cliente_interno = AsyncMock()
        cliente_interno.publicar_paso = AsyncMock()
        cliente_interno.publicar_loc = AsyncMock()
        cliente_interno.obtener_ticket = AsyncMock(return_value=dict(tk))

        job_base = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_a,
            "ticket_id": tk["id"],
            "ticket_numero": tk["numero"],
            "pr_url": "https://github.com/org/repo/pull/10",
            "rama": "feat/iva",
        }
        spec_aprobada = {
            "spec_md_ref": "mem://spec",
            "scope_paths": ["src/billing/"],
            "adr_borrador": None,
            "alternativas": [],
            "criterios": [{"given": "G", "when": "W", "then": "T"}],
        }

        for nombre_modo, cliente_grafo in (
            ("doble", GrafoClienteDoble()),
            ("postgres", grafo_cliente()),
        ):
            modo_actual = nombre_modo
            with (
                patch.object(etapas, "_cargar_ticket", return_value=dict(tk)),
                patch.object(etapas, "_cargar_spec_aprobada", return_value=spec_aprobada),
                patch.object(etapas, "_subir_artefacto", AsyncMock(return_value="mem://artefacto")),
                patch.object(etapas, "_leer_artefacto", AsyncMock(return_value="# Spec")),
                patch.object(etapas, "_registrar_spec", AsyncMock(return_value={"id": "spec_1"})),
                patch.object(qa, "_cargar_ticket", AsyncMock(return_value=dict(tk))),
                patch.object(qa, "_cargar_spec_aprobada", AsyncMock(return_value=spec_aprobada)),
                patch.object(security, "_cargar_ticket", AsyncMock(return_value=dict(tk))),
                patch.object(deploy, "_cargar_ticket", AsyncMock(return_value=dict(tk))),
            ):
                await etapas.ejecutar_triage(
                    replace(job_base) if False else dict(job_base),
                    cliente_interno,
                    intento=1,
                    grafo=cliente_grafo,
                )
                await design.ejecutar_diseno(
                    dict(job_base),
                    cliente_interno,
                    intento=1,
                    grafo=cliente_grafo,
                )
                await build.ejecutar_build(
                    dict(job_base),
                    cliente_interno,
                    intento=1,
                    grafo=cliente_grafo,
                )
                await qa.ejecutar_qa(
                    dict(job_base),
                    cliente_interno,
                    intento=1,
                    grafo=cliente_grafo,
                )
                await security.ejecutar_security(
                    dict(job_base),
                    cliente=cliente_interno,
                    intento=1,
                    grafo=cliente_grafo,
                )
                await deploy.ejecutar_deploy(
                    dict(job_base),
                    cliente=cliente_interno,
                    intento=1,
                    grafo=cliente_grafo,
                )

        # Con el doble el subgrafo está vacío; con GrafoClientePostgres aporta nodos y aristas reales.
        grafo_doble_en_triage = entradas_capturadas["doble"]["triage.impact"]["graph"]
        grafo_pg_en_triage = entradas_capturadas["postgres"]["triage.impact"]["graph"]
        assert grafo_doble_en_triage["nodes"] == []
        assert len(grafo_pg_en_triage["nodes"]) >= 3
        assert any(n["id"] == ids["sym_calc"] for n in grafo_pg_en_triage["nodes"])
        assert len(grafo_pg_en_triage["edges"]) >= 2
    finally:
        await _limpiar_cm(tenant_a)
