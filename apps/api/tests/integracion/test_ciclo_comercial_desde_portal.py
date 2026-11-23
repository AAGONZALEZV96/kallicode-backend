"""E2E del ciclo comercial (KC-PT-09-6), contra el backend real.

Recorre lo mismo que el portal: con la cuota de LOC agotada y el almacenamiento al 80 %, mira el
resumen, compara los planes, solicita la mejora con un comentario y la ve abierta; el equipo
comercial la aprueba (/internal/v1) y el portal vuelve a mirar. Las solicitudes salen de
ciclo_comercial_desde_portal.json, el mismo archivo con el que
kallicode-portal/src/vistas/consumo/RecorridoCicloComercial.test.tsx recorre la interfaz
respondiendo con lo grabado aquí.

- Cada respuesta debe mantener la forma de la grabada (mismas claves y tipos JSON; puede haber
  claves nuevas), así un cambio del backend que rompería al portal falla aquí.
- Con KC_REGRABAR_CICLO_COMERCIAL=1 se regraban las respuestas.
- Todo corre en una transacción que se revierte al final (como tests/integracion/test_planes.py).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api import deps
from kallicode_api.main_interna import app as app_interna
from kallicode_api.routers import organization, plans, usage
from kallicode_core.comercial import ciclo_de
from kallicode_core.seguridad import emitir_access_token, emitir_svc_token
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]

AQUI = Path(__file__).parent
CONTRATO_PATH = AQUI / "ciclo_comercial_desde_portal.json"
CONTRATO = json.loads(CONTRATO_PATH.read_text(encoding="utf-8"))
REGRABAR = os.environ.get("KC_REGRABAR_CICLO_COMERCIAL") == "1"
MIB = 1024 * 1024
COMENTARIO = "Necesitamos más LOC este mes"


def _forma(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {k: _forma(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_forma(valor[0])] if valor else []
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, int | float):
        return "number"
    if isinstance(valor, str):
        return "string"
    return "null"


def _diferencias(grabada: Any, real: Any, ruta: str = "$") -> list[str]:
    """Lo que la respuesta real perdió o cambió de tipo respecto de la grabada."""
    if isinstance(grabada, dict):
        if not isinstance(real, dict):
            return [f"{ruta}: era un objeto y ahora es {real!r}"]
        return [
            d
            for clave, valor in grabada.items()
            for d in (
                _diferencias(valor, real[clave], f"{ruta}.{clave}")
                if clave in real
                else [f"{ruta}.{clave}: ya no viene"]
            )
        ]
    if isinstance(grabada, list):
        if not isinstance(real, list):
            return [f"{ruta}: era una lista y ahora es {real!r}"]
        return _diferencias(grabada[0], real[0], f"{ruta}[0]") if grabada and real else []
    # Un null grabado admite cualquier valor (p. ej. «desde» con o sin cola).
    if grabada != "null" and grabada != real:
        return [f"{ruta}: era {grabada} y ahora es {real}"]
    return []


def _rellenar(valor: Any, valores: dict[str, str]) -> Any:
    if isinstance(valor, str):
        return re.sub(r"\{(\w+)\}", lambda m: valores[m.group(1)], valor)
    if isinstance(valor, dict):
        return {k: _rellenar(v, valores) for k, v in valor.items()}
    return valor


@pytest.fixture
async def organizacion(db_admin, monkeypatch, redis_doble):
    """Starter con la cuota de LOC agotada, el almacenamiento al 83 % y 2 tickets en cola."""
    db, tenant = db_admin, f"org_ciclo_{uuid.uuid4().hex[:10]}"
    for sql in (
        "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Ciclo comercial e2e')",
        """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
           VALUES (:t, :t, 'starter', current_date - 15, current_date + 15)""",
        """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
           VALUES (:t, :t, :t || '@example.test', 'Owner', 'owner')""",
        "INSERT INTO core.production_lines (tenant_id, numero) VALUES (:t, 1)",
        # 85 adjuntos de 50 MiB: 4250 de 5120 MiB (83 %).
        """INSERT INTO core.ticket_attachments (id, tenant_id, nombre_archivo, content_type,
               tamano_bytes, blob_ref)
           SELECT :t || '_att_' || n, :t, 'video.mp4', 'video/mp4', 50 * 1048576, 'e2e/' || n
             FROM generate_series(1, 85) n""",
        """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa,
               creado_en)
           SELECT :t || '_tk_' || n, :t, 'En cola ' || n, 'x', 'bug', 'media',
                  'en_cola_por_cuota', now() - (n || ' days')::interval
             FROM generate_series(1, 2) n""",
    ):
        await db.execute(text(sql), {"t": tenant})
    await db.execute(
        text(
            """INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, netas, motivo_ajuste)
               VALUES (:t, :c, 'ajuste', 3000, 'Consumo de prueba')"""
        ),
        {"t": tenant, "c": await ciclo_de(db, tenant)},
    )

    @asynccontextmanager
    async def sesion(*args, **kwargs):
        async with db.begin_nested():
            yield db

    for modulo in (plans, organization, usage, deps):
        if hasattr(modulo, "sesion_de"):
            monkeypatch.setattr(modulo, "sesion_de", sesion)
    return tenant


async def test_recorrido_del_ciclo_comercial_con_lo_que_pide_el_portal(
    client: AsyncClient, organizacion
) -> None:
    tenant = organizacion
    usuario = {"Authorization": f"Bearer {emitir_access_token(tenant, tenant, 'owner')}"}
    valores: dict[str, str] = {}
    respuestas: dict[str, dict] = {}

    async with AsyncClient(
        transport=ASGITransport(app=app_interna), base_url="http://test"
    ) as interna:
        for s in CONTRATO["solicitudes"]:
            if s.get("interna"):
                # El equipo comercial aprueba (no es una solicitud del portal).
                cabeceras = {
                    "Authorization": f"Bearer {emitir_svc_token('comercial', tenant)}",
                    "Idempotency-Key": uuid.uuid4().hex,
                }
                r = await interna.request(
                    s["metodo"], _rellenar(s["ruta"], valores), headers=cabeceras
                )
            else:
                r = await client.request(
                    s["metodo"], s["ruta"], json=s.get("cuerpo"), headers=usuario
                )
            assert r.status_code in (200, 202), f"{s['paso']}: {r.status_code} {r.text}"
            respuestas[s["paso"]] = {"status": r.status_code, "cuerpo": r.json()}
            if s["paso"] == "solicitar":
                valores["solicitud_id"] = r.json()["solicitud_id"]

    # Antes: cuota agotada, almacenamiento al 80 % y 2 tickets esperando.
    antes = respuestas["resumen-agotado"]["cuerpo"]
    assert (antes["plan"]["codigo"], antes["umbral"]) == ("starter", "agotado")
    assert antes["almacenamiento"]["umbral"] == "aviso_80"
    assert antes["almacenamiento"]["porcentaje"] == 83.0
    assert antes["cola"]["tickets"] == 2
    assert respuestas["solicitud-sin-abrir"]["cuerpo"] is None
    planes = {p["codigo"]: p for p in respuestas["planes"]["cuerpo"]["planes"]}
    assert [c for c, p in planes.items() if p["actual"]] == ["starter"]
    assert planes["growth"]["delta"]["loc_mes"] == 5000

    # La solicitud: urgente porque se pidió con la cuota agotada.
    assert respuestas["solicitar"]["status"] == 202
    assert respuestas["solicitar"]["cuerpo"]["estado"] == "registrada"
    abierta = respuestas["solicitud-abierta"]["cuerpo"]
    assert (abierta["plan_deseado"], abierta["estado"], abierta["urgente"]) == (
        "growth",
        "registrada",
        True,
    )
    assert respuestas["resumen-tras-solicitar"]["cuerpo"]["umbral"] == "agotado"

    # Aprobada: growth, la cuota ya no está agotada y el almacenamiento vuelve a normal...
    assert respuestas["aprobar"]["cuerpo"]["estado"] == "aplicada"
    despues = respuestas["resumen-tras-mejora"]["cuerpo"]
    assert (despues["plan"]["codigo"], despues["umbral"]) == ("growth", "normal")
    assert despues["loc"] == {"consumidas": 3000, "restantes": 5000, "porcentaje": 37.5}
    assert despues["almacenamiento"]["umbral"] == "normal"
    # ...pero los tickets en cola siguen esperando la renovación: aplicar la mejora no los libera.
    assert despues["cola"]["tickets"] == 2
    assert respuestas["solicitud-aplicada"]["cuerpo"]["estado"] == "aplicada"
    planes = {p["codigo"]: p for p in respuestas["planes-tras-mejora"]["cuerpo"]["planes"]}
    assert [c for c, p in planes.items() if p["actual"]] == ["growth"]

    if REGRABAR:
        CONTRATO_PATH.write_text(
            json.dumps(
                {**CONTRATO, "valores": valores, "respuestas": respuestas},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return

    assert "respuestas" in CONTRATO, "Falta grabar: correr con KC_REGRABAR_CICLO_COMERCIAL=1"
    problemas = [
        f"{paso}: {d}"
        for paso, grabada in CONTRATO["respuestas"].items()
        for d in (
            [f"status {respuestas[paso]['status']} (grabado {grabada['status']})"]
            if respuestas[paso]["status"] != grabada["status"]
            else _diferencias(_forma(grabada["cuerpo"]), _forma(respuestas[paso]["cuerpo"]))
        )
    ]
    assert problemas == [], "El backend ya no responde lo que el portal espera:\n" + "\n".join(
        problemas
    )
