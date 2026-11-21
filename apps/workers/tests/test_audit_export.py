"""Exportaciones de auditoría (KC-PT-08-3): filtros del explorador en el CSV, PDF real del
expediente y EXPORTS_CONCURRENTES registrado (antes la 4.ª exportación respondía 500)."""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.auditoria import registrar_evento
from kallicode_core.db import sesion_sistema, uno
from kallicode_workers import audit_export
from kallicode_workers.audit_export import filtro_eventos
from kallicode_workers.expediente_pdf import generar_pdf
from sqlalchemy import text

# ---- filtros (sin base de datos) ----


def test_sin_filtros_solo_el_tenant() -> None:
    assert filtro_eventos("org_1", None, None) == ("tenant_id = :t", {"t": "org_1"})


def test_los_filtros_del_explorador_son_condiciones_fijas_con_parametros() -> None:
    where, params = filtro_eventos(
        "org_1",
        "KC-1",
        {
            "actor_tipo": "agente",
            "etapa": "build",
            "desde": "2026-11-01T03:00:00Z",
            "hasta": "2026-11-21T02:59:59.999Z",
            "q": "deploy",
        },
    )

    assert where == (
        "tenant_id = :t AND ticket_id = :tk AND actor_tipo = :actor_tipo AND etapa = :etapa"
        " AND creado_en >= :desde AND creado_en <= :hasta"
        " AND resumen ILIKE :q"
    )
    assert params == {
        "t": "org_1",
        "tk": "KC-1",
        "actor_tipo": "agente",
        "etapa": "build",
        "desde": datetime(2026, 11, 1, 3, 0, tzinfo=UTC),
        "hasta": datetime(2026, 11, 21, 2, 59, 59, 999000, tzinfo=UTC),
        "q": "%deploy%",
    }


def test_los_vacios_y_las_claves_desconocidas_se_ignoran() -> None:
    where, params = filtro_eventos("org_1", None, {"etapa": "", "q": None, "columna; DROP": "x"})
    assert (where, params) == ("tenant_id = :t", {"t": "org_1"})


# ---- PDF (sin base de datos) ----

_FECHA = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def _expediente(**cambios) -> dict:
    datos = {
        "solicitante": {"nombre": "Ana <Admin> & Co", "origen": "portal", "fecha": _FECHA},
        "integridad": {"integra": True, "hash_raiz": "a" * 64},
        "firmas": [
            {
                "gate": 1,
                "accion": "aprobado",
                "actor": "Ana",
                "fecha": _FECHA,
                "comentario": "Listo para diseño 🚀",
                "sello": "b" * 64,
            }
        ],
        "etapas": [
            {
                "etapa": "design",
                "pasos": [
                    {
                        "paso": "design.compile",
                        "modelo": "deepseek-v4",
                        "tier": "pro",
                        "confianza": 0.91,
                        "tokens": 1200,
                        "duracion_ms": 800,
                        "validacion": "ok",
                    }
                ],
            }
        ],
        "eventos": [
            {
                "creado_en": _FECHA,
                "actor_tipo": "humano",
                "actor_id": "u_1",
                "evento": "ticket_creado",
                # Con < y &: una celda escapada dos veces mostraría «&lt;» o «&amp;» en el PDF.
                "resumen": "Creo el ticket <KC-1001> & su plan",
                "sello": "c" * 64,
            }
        ],
    }
    return datos | cambios


def _texto_visible(pdf: bytes) -> str:
    """Texto impreso de un PDF sin comprimir: une los fragmentos (...) Tj de cada línea.

    reportlab parte el texto en varios Tj (por ejemplo en cada «&»), así que buscar en los
    bytes no alcanza para saber qué se ve.
    """
    lineas = []
    for bloque in re.findall(rb"BT (.*?) ET", pdf, re.DOTALL):
        crudo = b"".join(re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", bloque))
        # Escapes de las cadenas PDF: octales \ddd y \\, \(, \).
        crudo = re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m.group(1), 8)]), crudo)
        crudo = re.sub(rb"\\(.)", rb"\1", crudo)
        lineas.append(crudo.decode("cp1252"))
    return "\n".join(lineas)


def test_el_pdf_tiene_el_expediente_completo() -> None:
    pdf = generar_pdf(
        "KC-1001", _expediente(), generado_en=_FECHA, export_id="exp_1", comprimir=False
    )

    assert pdf.startswith(b"%PDF-")
    for texto in (b"KC-1001", b"design.compile", b"deepseek-v4", b"ticket_creado", b"exp_1"):
        assert texto in pdf, texto
    # «<» y «&» se escapan para Paragraph (no rompen el documento) y se imprimen tal cual,
    # sin escapar dos veces: en el texto y en las celdas de las tablas.
    visible = _texto_visible(pdf)
    assert "Ana <Admin> & Co" in visible
    assert "Creo el ticket <KC-1001> & su plan" in visible
    assert "Expediente de auditoría · KC-1001" in visible
    assert "&lt;" not in visible and "&amp;" not in visible


def test_sin_firmas_ni_pasos_ni_eventos_lo_dice() -> None:
    pdf = generar_pdf(
        "KC-1002",
        _expediente(firmas=[], etapas=[], eventos=[]),
        generado_en=_FECHA,
        export_id="exp_2",
        comprimir=False,
    )

    for texto in (b"Sin firmas registradas.", b"Sin pasos del modelo", b"Sin eventos registrados."):
        assert texto in pdf, texto


def test_comprimido_por_defecto() -> None:
    pdf = generar_pdf("KC-1001", _expediente(), generado_en=_FECHA, export_id="exp_1")
    assert pdf.startswith(b"%PDF-") and b"design.compile" not in pdf


# ---- de punta a punta: API → worker → Blob (PostgreSQL y Redis reales) ----


@pytest_asyncio.fixture
async def tenant_con_eventos():
    """Tenant con su owner, un ticket y eventos de varios actores y etapas."""
    tenant = f"org_exp_{uuid.uuid4().hex[:8]}"
    owner = f"u_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Exportaciones')"),
            {"t": tenant},
        )
        await db.execute(
            text(
                "INSERT INTO core.users (id, tenant_id, email, nombre, rol) "
                "VALUES (:u, :t, :e, 'Owner Export', 'owner')"
            ),
            {"u": owner, "t": tenant, "e": f"{owner}@{tenant}.test"},
        )
        numero = (
            await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                                 prioridad, etapa, reportado_por)
                       VALUES (:id, :t, 'Ticket exportable', 'Descripción', 'bug', 'media',
                               'triage', :u)
                       RETURNING numero"""
                ),
                {"id": str(uuid.uuid4()), "t": tenant, "u": owner},
            )
        ).scalar_one()
        for evento, resumen, actor, etapa, ticket in (
            ("ticket_creado", "Ticket creado desde el portal", "humano", "triage", numero),
            ("build_listo", "Build listo para deploy", "agente", "build", numero),
            ("deploy_iniciado", "Deploy iniciado en staging", "agente", "deploy", numero),
            ("org_modificada", "Organización actualizada", "humano", None, None),
        ):
            await registrar_evento(
                db,
                tenant,
                evento=evento,
                resumen=resumen,
                actor_tipo=actor,
                actor_id=owner if actor == "humano" else "agente-build",
                etapa=etapa,
                ticket_id=ticket,
            )
        await db.commit()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=owner, tenant_id=tenant, rol="owner", restringido=False
    )
    yield {"tenant": tenant, "numero": numero}
    app.dependency_overrides.clear()

    async with sesion_sistema() as db:
        for tabla in ("core.audit_exports", "core.tickets", "core.ticket_counters", "core.users"):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        await db.execute(text("DELETE FROM audit.chain_heads WHERE tenant_id=:t"), {"t": tenant})
        await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), {"t": tenant})
        await db.commit()


@pytest.fixture
def blob(monkeypatch):
    """Lo que el worker sube al Blob, sin un Azurite."""
    subidos: list[dict] = []

    async def subir(ref: str, contenido: bytes, content_type: str) -> str:
        subidos.append({"ref": ref, "contenido": contenido, "content_type": content_type})
        return ref

    monkeypatch.setattr(audit_export, "subir_archivo_servidor", subir)
    return subidos


async def _exportar(cuerpo: dict) -> tuple[int, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/v1/audit/exports", json=cuerpo)
    return r.status_code, r.json()


async def _procesar(tenant: str, export_id: str) -> dict:
    await audit_export.procesar({"export_id": export_id, "tenant_id": tenant})
    async with sesion_sistema() as db:
        return await uno(db, "SELECT * FROM core.audit_exports WHERE id=:i", {"i": export_id})


@pytest.mark.integracion
async def test_csv_con_los_filtros_del_explorador(tenant_con_eventos, blob) -> None:
    tenant = tenant_con_eventos["tenant"]

    estado, cuerpo = await _exportar(
        {"formato": "csv", "filtros": {"actor_tipo": "agente", "q": "deploy"}}
    )

    assert estado == 202, cuerpo
    fila = await _procesar(tenant, cuerpo["export_id"])
    assert fila["estado"] == "lista"
    assert fila["filtros"] == {"actor_tipo": "agente", "q": "deploy"}
    filas = list(csv.DictReader(io.StringIO(blob[0]["contenido"].decode("utf-8"))))
    # Solo los del agente que mencionan «deploy»: no el evento humano ni el de la organización.
    assert sorted(f["evento"] for f in filas) == ["build_listo", "deploy_iniciado"]
    assert blob[0]["content_type"] == "text/csv"
    assert blob[0]["ref"] == f"{tenant}/exports/{cuerpo['export_id']}.csv"


@pytest.mark.integracion
async def test_csv_de_un_ticket_y_rango_de_fechas(tenant_con_eventos, blob) -> None:
    tenant, numero = tenant_con_eventos["tenant"], tenant_con_eventos["numero"]
    ahora = datetime.now(UTC)

    _, cuerpo = await _exportar(
        {
            "formato": "csv",
            "ticket_id": numero,
            "filtros": {
                "etapa": "build",
                "desde": (ahora - timedelta(hours=1)).isoformat(),
                "hasta": (ahora + timedelta(hours=1)).isoformat(),
            },
        }
    )
    fila = await _procesar(tenant, cuerpo["export_id"])

    # La tabla guarda el id interno (FK a core.tickets); el CSV, el número de los eventos.
    async with sesion_sistema() as db:
        interno = await uno(
            db,
            "SELECT id FROM core.tickets WHERE tenant_id=:t AND numero=:n",
            {"t": tenant, "n": numero},
        )
    assert fila["ticket_id"] == interno["id"]
    filas = list(csv.DictReader(io.StringIO(blob[0]["contenido"].decode("utf-8"))))
    assert [(f["evento"], f["ticket_id"]) for f in filas] == [("build_listo", numero)]

    # Fuera del rango: ninguno.
    blob.clear()
    _, cuerpo = await _exportar(
        {"formato": "csv", "filtros": {"hasta": (ahora - timedelta(days=1)).isoformat()}}
    )
    await _procesar(tenant, cuerpo["export_id"])
    assert blob[0]["contenido"] == b""


@pytest.mark.integracion
async def test_pdf_real_del_expediente(tenant_con_eventos, blob) -> None:
    tenant, numero = tenant_con_eventos["tenant"], tenant_con_eventos["numero"]

    _, cuerpo = await _exportar({"formato": "pdf", "ticket_id": numero})
    fila = await _procesar(tenant, cuerpo["export_id"])

    assert fila["estado"] == "lista"
    assert blob[0]["content_type"] == "application/pdf"
    assert blob[0]["contenido"].startswith(b"%PDF-")
    assert blob[0]["ref"].endswith(".pdf")


@pytest.mark.integracion
async def test_un_ticket_inexistente_responde_404(tenant_con_eventos) -> None:
    # Antes el número iba directo a la FK de core.tickets(id) y respondía 500.
    estado, cuerpo = await _exportar({"formato": "pdf", "ticket_id": "KC-999999"})

    assert estado == 404, cuerpo
    assert cuerpo["error"]["codigo"] == "RECURSO_NO_ENCONTRADO"


@pytest.mark.integracion
async def test_la_cuarta_exportacion_simultanea_responde_429(tenant_con_eventos) -> None:
    for _ in range(3):
        estado, _ = await _exportar({"formato": "csv"})
        assert estado == 202

    estado, cuerpo = await _exportar({"formato": "csv"})

    assert estado == 429, cuerpo
    assert cuerpo["error"]["codigo"] == "EXPORTS_CONCURRENTES"


@pytest.mark.integracion
@pytest.mark.parametrize(
    "filtros",
    [
        {"columna": "x"},
        {"actor_tipo": "root"},
        {"desde": "2026-11-20T00:00:00Z", "hasta": "2026-11-01T00:00:00Z"},
        {"q": "x" * 201},
    ],
)
async def test_filtros_invalidos_responden_422(tenant_con_eventos, filtros) -> None:
    estado, cuerpo = await _exportar({"formato": "csv", "filtros": filtros})

    assert estado == 422, cuerpo
    assert cuerpo["error"]["codigo"] == "VALIDACION_ENTRADA"
