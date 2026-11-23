"""Capa de consulta de CodeMapping por la API interna y su degradación controlada (KC-CM-11-5).

Sin base de datos: la sesión es falsa y las operaciones las resuelve el doble del contrato
(`kallicode_core.cm_grafo_doble`), que tiene la misma semántica que PostgreSQL. La paridad con la
base real está en packages/codemapping/tests/test_resiliencia.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import ServicioActual, servicio_actual
from kallicode_api.main_interna import app
from kallicode_api.routers import codemapping_grafo
from kallicode_core.cm_grafo import OPERACIONES
from kallicode_core.cm_grafo_doble import ConsultaGrafoDoble, GrafoEnMemoria
from kallicode_core.cm_impacto import TIEMPO_MAXIMO_MS
from kallicode_core.errors import RETRY_AFTER_DEPENDENCIA_S
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TENANT = "org_cm_consulta"
BASE = "/internal/v1" + codemapping_grafo.PREFIJO

CUERPOS: dict[str, dict[str, Any]] = {
    "resolver_nodos": {"ids": ["emitir", "no_existe"]},
    "semillas": {"ficheros": ["src/billing/invoice.py"]},
    "impacto": {"nodos": ["calc_iva"], "k": 2},
    "dependencias": {"nodos": ["emitir"], "k": 2},
    "subgrafo": {"nodos": ["emitir"], "k": 1},
    "vecinos": {"nodo": "emitir"},
    "dependencias_ocultas": {"nodos": ["emitir"], "k": 2},
    "buscar_hibrido": {"embedding": [0.0] * 1024, "k": 5},
    "resolver_nuggets": {"variable": "nuggets", "nodos": ["emitir"]},
    "resolver_incidentes": {"nodos": ["emitir"]},
}


def _grafo() -> GrafoEnMemoria:
    return GrafoEnMemoria(
        ficheros=[("f1", "src/billing/invoice.py", ["emitir", "calc_iva"])],
        tablas=["dbt_facturas"],
        aristas=[("CALLS", "emitir", "calc_iva"), ("USES_TABLE", "emitir", "dbt_facturas")],
    )


class _SesionDoble:
    """`ConsultaGrafoSesion` con las operaciones del doble: misma firma, async."""

    def __init__(self, _db: object, **_opciones: object) -> None:
        self._doble = ConsultaGrafoDoble(_grafo())

    def __getattr__(self, nombre: str) -> Callable[[Any], Any]:
        metodo = getattr(self._doble, nombre)

        async def _async(consulta: Any) -> Any:
            return metodo(consulta)

        return _async


TIEMPOS_PEDIDOS: list[int | None] = []


@asynccontextmanager
async def _sesion_falsa(
    _svc: ServicioActual, *, statement_timeout_ms: int | None = None
) -> AsyncIterator[object]:
    TIEMPOS_PEDIDOS.append(statement_timeout_ms)
    yield object()


def _sesion_que_falla(exc: BaseException):
    @asynccontextmanager
    async def _sesion(_svc: ServicioActual, **_opciones: object) -> AsyncIterator[object]:
        raise exc
        yield  # pragma: no cover

    return _sesion


@pytest.fixture(autouse=True)
def _servicio() -> Any:
    app.dependency_overrides[servicio_actual] = lambda: ServicioActual(
        servicio="orquestador", tenant_id=TENANT, linea=1
    )
    yield
    app.dependency_overrides.pop(servicio_actual, None)


def _cliente(*, levantar: bool = True) -> AsyncClient:
    transporte = ASGITransport(app=app, raise_app_exceptions=levantar)
    return AsyncClient(transport=transporte, base_url="http://test")


def test_cada_operacion_del_contrato_tiene_su_ruta() -> None:
    assert set(codemapping_grafo.RUTAS_OPERACIONES) == {o.nombre for o in OPERACIONES}
    rutas = {p for p in app.openapi()["paths"] if p.startswith(BASE) and p != f"{BASE}/batch"}
    assert rutas == {BASE + r for r in codemapping_grafo.RUTAS_OPERACIONES.values()}


@pytest.mark.asyncio
@pytest.mark.parametrize("operacion", sorted(CUERPOS))
async def test_cada_operacion_responde_como_el_contrato(
    operacion: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codemapping_grafo, "sesion_de", _sesion_falsa)
    monkeypatch.setattr(codemapping_grafo, "ConsultaGrafoSesion", _SesionDoble)
    esperado = getattr(ConsultaGrafoDoble(_grafo()), operacion)(
        next(o.entrada for o in OPERACIONES if o.nombre == operacion).model_validate(
            CUERPOS[operacion]
        )
    )
    async with _cliente() as cliente:
        resp = await cliente.post(
            BASE + codemapping_grafo.RUTAS_OPERACIONES[operacion], json=CUERPOS[operacion]
        )
    assert resp.status_code == 200, resp.text
    if isinstance(esperado, list):
        assert resp.json() == [e.model_dump(mode="json") for e in esperado]
    else:
        assert resp.json() == esperado.model_dump(mode="json")


@pytest.mark.asyncio
async def test_impacto_devuelve_quien_depende_del_area(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codemapping_grafo, "sesion_de", _sesion_falsa)
    monkeypatch.setattr(codemapping_grafo, "ConsultaGrafoSesion", _SesionDoble)
    async with _cliente() as cliente:
        resp = await cliente.post(f"{BASE}/impacto", json={"nodos": ["calc_iva"], "k": 2})
    ids = {n["id"] for n in resp.json()["nodos"]}
    assert {"calc_iva", "emitir"} <= ids


@pytest.mark.asyncio
async def test_entrada_invalida_es_422_del_catalogo() -> None:
    async with _cliente() as cliente:
        resp = await cliente.post(f"{BASE}/impacto", json={"k": 3})
    assert resp.status_code == 422
    assert resp.json()["error"]["codigo"] == "VALIDACION_ENTRADA"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caida",
    [
        sa_exc.OperationalError("SELECT 1", {}, Exception("the database system is shutting down")),
        sa_exc.InterfaceError("SELECT 1", {}, Exception("connection is closed")),
    ],
    ids=["operational", "interface"],
)
@pytest.mark.parametrize("operacion", sorted(CUERPOS))
async def test_base_caida_es_503_tipificado_con_retry_after(
    operacion: str, caida: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codemapping_grafo, "sesion_de", _sesion_que_falla(caida))
    async with _cliente() as cliente:
        resp = await cliente.post(
            BASE + codemapping_grafo.RUTAS_OPERACIONES[operacion], json=CUERPOS[operacion]
        )
    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["codigo"] == "DEPENDENCIA_NO_DISPONIBLE"
    assert error["detalle"] == {"servicio": "base de datos"}
    assert resp.headers["Retry-After"] == str(RETRY_AFTER_DEPENDENCIA_S)


@pytest.mark.asyncio
async def test_postgres_inalcanzable_con_asyncpg_real_es_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La caída real: asyncpg contra un puerto cerrado lanza ConnectionRefusedError sin envolver."""
    motor = create_async_engine("postgresql+asyncpg://u:p@127.0.0.1:1/x")

    @asynccontextmanager
    async def _sesion_real(_svc: ServicioActual, **_opciones: object) -> AsyncIterator[object]:
        async with motor.connect() as conn:
            await conn.execute(text("SELECT 1"))
            yield conn

    monkeypatch.setattr(codemapping_grafo, "sesion_de", _sesion_real)
    try:
        async with _cliente() as cliente:
            resp = await cliente.post(f"{BASE}/impacto", json={"nodos": ["x"], "k": 3})
    finally:
        await motor.dispose()
    assert resp.status_code == 503
    assert resp.json()["error"]["codigo"] == "DEPENDENCIA_NO_DISPONIBLE"


@pytest.mark.asyncio
async def test_un_connectionerror_ajeno_a_la_base_no_se_disfraza_de_caida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        codemapping_grafo, "sesion_de", _sesion_que_falla(ConnectionRefusedError("otro servicio"))
    )
    async with _cliente(levantar=False) as cliente:
        resp = await cliente.post(f"{BASE}/impacto", json={"nodos": ["x"], "k": 3})
    assert resp.status_code == 500


@pytest.mark.asyncio
@pytest.mark.parametrize("operacion", sorted(CUERPOS))
async def test_cada_operacion_pide_el_statement_timeout_en_la_sesion(
    operacion: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KC-CM-11-6: el tiempo máximo va en el mismo viaje que el tenant, no en otra sentencia."""
    monkeypatch.setattr(codemapping_grafo, "sesion_de", _sesion_falsa)
    monkeypatch.setattr(codemapping_grafo, "ConsultaGrafoSesion", _SesionDoble)
    TIEMPOS_PEDIDOS.clear()
    async with _cliente() as cliente:
        await cliente.post(
            BASE + codemapping_grafo.RUTAS_OPERACIONES[operacion], json=CUERPOS[operacion]
        )
    assert TIEMPOS_PEDIDOS == [TIEMPO_MAXIMO_MS]
