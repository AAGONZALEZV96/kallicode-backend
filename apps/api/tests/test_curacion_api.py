"""API de curación de CodeMapping y selectores de UI (KC-CM-07-3 / 07-4 / 07-5).

Sin base de datos: la sesión del tenant y los helpers de SQL se sustituyen por dobles que registran
la consulta. Lo que se verifica aquí es el contrato HTTP, los roles, el saneo y que cada consulta
vaya filtrada por tenant. El aislamiento real entre tenants lo cubre RLS (tests de integración).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from kallicode_api.routers import curation
from kallicode_api.servicios.curacion import normalizar_item, sanear_json, sanear_texto

from .conftest import auth_headers

TENANT = "org_a"
AHORA = datetime(2026, 10, 21, tzinfo=UTC)


def _fila(**extra: Any) -> dict[str, Any]:
    base = {
        "id": "cur_1",
        "tipo": "enlace",
        "estado": "pendiente",
        "payload": {
            "tipo": "enlace",
            "src_id": "def_1",
            "dst_id": "Invoice",
            "confidence": 0.62,
            "umbral": 0.75,
            "snapshot_id": "snap_1",
            "evidencia": {"motivo": "similar pero los términos difieren"},
        },
        "revisado_por": None,
        "revisado_en": None,
        "creado_en": AHORA,
    }
    base.update(extra)
    return base


class FakeDb:
    """Registra las consultas y responde con lo que el test haya programado."""

    def __init__(self) -> None:
        self.consultas: list[tuple[str, dict[str, Any]]] = []
        self.respuestas_uno: list[Any] = []
        self.respuestas_todos: list[Any] = []
        self.eventos: list[dict[str, Any]] = []


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    f = FakeDb()

    @asynccontextmanager
    async def sesion(_identidad: Any):
        yield object()

    async def uno(_db: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
        f.consultas.append((sql, params or {}))
        return f.respuestas_uno.pop(0)

    async def todos(_db: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
        f.consultas.append((sql, params or {}))
        return f.respuestas_todos.pop(0)

    async def evento(_db: Any, tenant: str, **kw: Any) -> str:
        f.eventos.append({"tenant": tenant, **kw})
        return "sello"

    monkeypatch.setattr(curation, "sesion_de", sesion)
    monkeypatch.setattr(curation, "uno", uno)
    monkeypatch.setattr(curation, "todos", todos)
    monkeypatch.setattr(curation, "registrar_evento", evento)
    return f


# --------------------------------------------------------------------------
# Servicio: normalización y saneo (KC-CM-07-4)
# --------------------------------------------------------------------------
def test_normaliza_un_enlace_con_la_forma_del_trigger_y_del_cargador() -> None:
    item = normalizar_item(_fila())
    assert (item["origen"], item["destino"], item["confianza"]) == ("def_1", "Invoice", 0.62)
    assert item["motivo"] == "similar pero los términos difieren"
    assert item["snapshot_id"] == "snap_1" and "payload" not in item


def test_normaliza_un_concepto_del_trigger_sin_evidencia() -> None:
    fila = _fila(
        tipo="concepto",
        payload={"id": "con_1", "name_en": "Invoice", "confidence": 0.5, "umbral": 0.75},
    )
    item = normalizar_item(fila)
    assert (item["origen"], item["destino"]) == ("con_1", "Invoice")
    assert item["motivo"] == "Confianza 0.50 inferior al umbral 0.75"


def test_payload_corrupto_no_rompe_la_normalizacion() -> None:
    item = normalizar_item(_fila(payload="{no es json"))
    assert item["confianza"] is None and item["origen"] == ""
    assert normalizar_item(_fila(payload='{"confidence": "alta"}'))["confianza"] is None


def test_el_texto_del_llm_sale_saneado() -> None:
    fila = _fila()
    fila["payload"]["evidencia"]["motivo"] = "mal\u202e\x00" + "x" * 900
    fila["payload"]["dst_id"] = "Inv\u200boice"
    item = normalizar_item(fila, con_payload=True)
    assert "\u202e" not in item["motivo"] and len(item["motivo"]) <= 500
    assert item["destino"] == "Invoice"
    assert "\u202e" not in item["payload"]["evidencia"]["motivo"]


def test_sanear_json_recorre_anidados_y_acota_la_profundidad() -> None:
    assert sanear_json({"a\u200b": ["x\x00", {"b": "y\u202e"}], "n": 3}) == {
        "a": ["x", {"b": "y"}],
        "n": 3,
    }
    anidado: Any = "hoja"
    for _ in range(20):
        anidado = {"k": anidado}
    assert "hoja" not in str(sanear_json(anidado))
    assert sanear_texto("a" * 5000, 10) == "a" * 10


# --------------------------------------------------------------------------
# GET /codemapping/curation
# --------------------------------------------------------------------------
async def test_listar_filtra_por_tenant_y_pagina(client, fake: FakeDb) -> None:
    fake.respuestas_uno = [{"n": 1}]
    fake.respuestas_todos = [[_fila()]]
    auth_headers(TENANT, "viewer")

    r = await client.get(
        "/api/v1/codemapping/curation",
        params={
            "estado": "pendiente",
            "tipo": "enlace",
            "snapshot_id": "snap_1",
            "confianza_min": 0.4,
            "confianza_max": 0.7,
            "limite": 10,
            "desde": 20,
        },
    )

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["total"] == 1 and cuerpo["items"][0]["id"] == "cur_1"
    assert (cuerpo["limite"], cuerpo["desde"]) == (10, 20)
    sql, params = fake.consultas[1]
    assert "tenant_id = :t" in sql and params["t"] == TENANT
    assert params["estado"] == "pendiente" and params["snap"] == "snap_1"
    assert params["cmin"] == 0.4 and params["cmax"] == 0.7
    assert params["lim"] == 10 and params["off"] == 20
    assert "ASC NULLS LAST" in sql  # primero los más dudosos


@pytest.mark.parametrize(
    "params",
    [
        {"estado": "borrado"},
        {"tipo": "otro"},
        {"confianza_min": 1.5},
        {"limite": 0},
        {"limite": 101},
        {"desde": -1},
    ],
)
async def test_listar_rechaza_filtros_invalidos(client, fake: FakeDb, params) -> None:
    auth_headers(TENANT)
    r = await client.get("/api/v1/codemapping/curation", params=params)
    assert r.status_code == 422
    assert fake.consultas == []  # no se llegó a la base


async def test_listar_sin_token_es_401(client) -> None:
    r = await client.get("/api/v1/codemapping/curation")
    assert r.status_code == 401


# --------------------------------------------------------------------------
# GET /codemapping/curation/{id}
# --------------------------------------------------------------------------
async def test_detalle_devuelve_payload_saneado(client, fake: FakeDb) -> None:
    fila = _fila()
    fila["payload"]["evidencia"]["raw_link"] = {"reasoning": "x\u202e"}
    fake.respuestas_uno = [fila]
    auth_headers(TENANT)

    r = await client.get("/api/v1/codemapping/curation/cur_1")

    assert r.status_code == 200
    assert r.json()["payload"]["evidencia"]["raw_link"]["reasoning"] == "x"
    assert fake.consultas[0][1] == {"t": TENANT, "i": "cur_1"}


async def test_detalle_inexistente_o_de_otro_tenant_es_404(client, fake: FakeDb) -> None:
    fake.respuestas_uno = [None]
    auth_headers(TENANT)
    r = await client.get("/api/v1/codemapping/curation/cur_ajeno")
    assert r.status_code == 404 and r.json()["error"]["codigo"] == "RECURSO_NO_ENCONTRADO"


# --------------------------------------------------------------------------
# POST /codemapping/curation/{id}/decision
# --------------------------------------------------------------------------
@pytest.mark.parametrize("rol", ["owner", "admin", "architect", "approver"])
@pytest.mark.parametrize("decision", ["aprobado", "rechazado"])
async def test_decision_resuelve_audita_y_registra_al_revisor(
    client, fake: FakeDb, rol: str, decision: str
) -> None:
    fake.respuestas_uno = [_fila(estado=decision, revisado_por=f"usuario-{rol}")]
    auth_headers(TENANT, rol)

    r = await client.post(
        "/api/v1/codemapping/curation/cur_1/decision",
        json={"decision": decision, "comentario": "ok\u202e"},
    )

    assert r.status_code == 200 and r.json()["estado"] == decision
    sql, params = fake.consultas[0]
    assert "estado = 'pendiente'" in sql  # solo se resuelve lo pendiente
    assert params["u"] == f"usuario-{rol}" and params["t"] == TENANT
    assert params["c"] == "ok"  # el comentario también se sanea
    assert fake.eventos[0]["evento"] == "curacion_resuelta"
    assert fake.eventos[0]["actor_id"] == f"usuario-{rol}"
    assert fake.eventos[0]["datos"] == {"item_id": "cur_1", "decision": decision}


@pytest.mark.parametrize("rol", ["member", "viewer"])
async def test_decision_exige_rol_de_revision(client, fake: FakeDb, rol: str) -> None:
    auth_headers(TENANT, rol)
    r = await client.post(
        "/api/v1/codemapping/curation/cur_1/decision", json={"decision": "aprobado"}
    )
    assert r.status_code == 403
    assert fake.consultas == [] and fake.eventos == []


async def test_decision_sobre_elemento_ya_resuelto_es_409(client, fake: FakeDb) -> None:
    fake.respuestas_uno = [None, {"estado": "aprobado"}]
    auth_headers(TENANT, "approver")
    r = await client.post(
        "/api/v1/codemapping/curation/cur_1/decision", json={"decision": "rechazado"}
    )
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "TRANSICION_INVALIDA"
    assert fake.eventos == []  # sin cambio no hay evento de auditoría


async def test_decision_sobre_inexistente_es_404(client, fake: FakeDb) -> None:
    fake.respuestas_uno = [None, None]
    auth_headers(TENANT, "admin")
    r = await client.post(
        "/api/v1/codemapping/curation/nada/decision", json={"decision": "aprobado"}
    )
    assert r.status_code == 404


@pytest.mark.parametrize("decision", ["aprobado", "rechazado"])
async def test_decision_sobre_nugget_pasa_por_resolver_nugget(
    client, fake: FakeDb, decision: str
) -> None:
    """KC-CM-08-3: un nugget se resuelve con cm.resolver_nugget, no con un UPDATE de la cola."""
    resuelta = _fila(
        id="cq_ng",
        tipo="nugget",
        estado=decision,
        revisado_por="usuario-approver",
        payload={"nugget_id": "ng_1", "regla": "Una factura pagada no se anula."},
    )
    fake.respuestas_uno = [
        None,  # el UPDATE directo excluye los nuggets
        {"estado": "pendiente", "tipo": "nugget", "nugget_id": "ng_1"},
        {"id": "ng_1"},
        resuelta,
    ]
    auth_headers(TENANT, "approver")

    r = await client.post(
        "/api/v1/codemapping/curation/cq_ng/decision",
        json={"decision": decision, "comentario": "Coincide\u202e con el manual"},
    )

    assert r.status_code == 200 and r.json()["estado"] == decision
    update_sql, _ = fake.consultas[0]
    assert "tipo <> 'nugget'" in update_sql
    resolver_sql, params = fake.consultas[2]
    assert "cm.resolver_nugget(" in resolver_sql
    assert params == {
        "n": "ng_1",
        "u": "usuario-approver",
        "e": decision,
        "c": "Coincide con el manual",
    }
    assert fake.eventos[0]["datos"] == {
        "item_id": "cq_ng",
        "decision": decision,
        "nugget_id": "ng_1",
    }


async def test_decision_sobre_nugget_ya_resuelto_es_409(client, fake: FakeDb) -> None:
    fake.respuestas_uno = [None, {"estado": "aprobado", "tipo": "nugget", "nugget_id": "ng_1"}]
    auth_headers(TENANT, "approver")
    r = await client.post(
        "/api/v1/codemapping/curation/cq_ng/decision", json={"decision": "rechazado"}
    )
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "TRANSICION_INVALIDA"
    assert not any("resolver_nugget" in sql for sql, _ in fake.consultas)
    assert fake.eventos == []


@pytest.mark.parametrize(
    "cuerpo", [{}, {"decision": "pendiente"}, {"decision": "aprobado", "comentario": "x" * 501}]
)
async def test_decision_valida_el_cuerpo(client, fake: FakeDb, cuerpo) -> None:
    auth_headers(TENANT, "admin")
    r = await client.post("/api/v1/codemapping/curation/cur_1/decision", json=cuerpo)
    assert r.status_code == 422 and fake.consultas == []


# --------------------------------------------------------------------------
# GET /codemapping/curation/selectores (KC-CM-07-5)
# --------------------------------------------------------------------------
async def test_selectores_completa_estados_y_tipos_y_trae_snapshots(client, fake: FakeDb) -> None:
    fake.respuestas_todos = [
        [{"estado": "pendiente", "n": 7}],
        [{"tipo": "enlace", "n": 6}, {"tipo": "concepto", "n": 1}],
        [
            {
                "id": "snap_1",
                "estado": "completo",
                "rama": "main\u202e",
                "commit_sha": "abc",
                "iniciado_en": AHORA,
                "pendientes": 7,
            }
        ],
    ]
    fake.respuestas_uno = [{"minima": 0.31, "maxima": 0.74}, {"umbral": 0.75}]
    auth_headers(TENANT, "viewer")

    r = await client.get("/api/v1/codemapping/curation/selectores")

    assert r.status_code == 200
    s = r.json()
    assert s["estados"] == [
        {"valor": "pendiente", "total": 7},
        {"valor": "aprobado", "total": 0},
        {"valor": "rechazado", "total": 0},
    ]
    assert s["tipos"] == [
        {"valor": "concepto", "total": 1},
        {"valor": "enlace", "total": 6},
        {"valor": "nugget", "total": 0},
    ]
    assert s["snapshots"][0]["rama"] == "main" and s["snapshots"][0]["pendientes"] == 7
    assert s["confianza"] == {"minima": 0.31, "maxima": 0.74} and s["umbral"] == 0.75
    assert "snapshots" in s and "LIMIT 50" in fake.consultas[2][0]


async def test_selectores_con_cola_vacia(client, fake: FakeDb) -> None:
    fake.respuestas_todos = [[], [], []]
    fake.respuestas_uno = [{"minima": None, "maxima": None}, {"umbral": 0.75}]
    auth_headers(TENANT)
    r = await client.get("/api/v1/codemapping/curation/selectores")
    s = r.json()
    assert r.status_code == 200 and s["confianza"] == {"minima": None, "maxima": None}
    assert all(e["total"] == 0 for e in s["estados"] + s["tipos"]) and s["snapshots"] == []


async def test_selectores_no_se_confunde_con_un_id(client, fake: FakeDb) -> None:
    """/selectores debe resolverse antes que /{item_id}."""
    fake.respuestas_todos = [[], [], []]
    fake.respuestas_uno = [{"minima": None, "maxima": None}, {"umbral": 0.75}]
    auth_headers(TENANT)
    r = await client.get("/api/v1/codemapping/curation/selectores")
    assert "estados" in r.json()
