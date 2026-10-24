"""Búsquedas vectoriales de la API con los parámetros HNSW por transacción (KC-DB-08-1).

`dedup-preview` e `impact-preview` aplican `vec.configurar_busqueda()` en la transacción de la consulta y
reordenan fuera del escaneo iterativo. Comprueba que la consulta sigue devolviendo lo correcto, que no
mezcla tenants y que el SET LOCAL no se filtra a otras consultas de la misma conexión.
"""

from __future__ import annotations

import math

import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_api.routers import tickets as router_tickets
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

pytestmark = pytest.mark.integracion

DIM = 1024


def auth_headers(tenant_id: str, rol: str = "admin") -> dict[str, str]:
    """Simula un usuario autenticado del tenant con ese rol (como apps/api/tests/conftest.py)."""
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=f"usuario-{rol}", tenant_id=tenant_id, rol=rol, restringido=False
    )
    return {}


@pytest.fixture(autouse=True)
def _sin_usuario_simulado():
    yield
    app.dependency_overrides.pop(usuario_actual, None)


def _vector(n: int) -> list[float]:
    """Vector unitario con el peso en la componente `n` (dos vectores distintos son ortogonales)."""
    v = [0.0] * DIM
    v[n] = 1.0
    return v


def _con_similitud(base: list[float], otro: list[float], coseno: float) -> list[float]:
    resto = math.sqrt(1 - coseno * coseno)
    return [coseno * a + resto * b for a, b in zip(base, otro, strict=True)]


@pytest.fixture
def embeber_fijo(monkeypatch: pytest.MonkeyPatch):
    """El servicio de embeddings se sustituye por un vector fijo: la prueba es de la consulta, no del modelo."""

    def _fijar(vec: list[float]) -> None:
        async def _falso(_texto: str) -> list[float]:
            return vec

        monkeypatch.setattr(router_tickets, "_embeber", _falso)

    return _fijar


async def _sembrar_embedding(tenant: str, ticket: str, vec: list[float]) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text(
                "INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding, texto_hash) "
                "VALUES (:tk, :t, CAST(:e AS vector), 'h')"
            ),
            {"tk": ticket, "t": tenant, "e": str(vec)},
        )
        await db.commit()


async def _id_del_ticket(numero: str, tenant: str) -> str:
    async with sesion_sistema() as db:
        fila = (
            await db.execute(
                text("SELECT id FROM core.tickets WHERE numero = :n AND tenant_id = :t"),
                {"n": numero, "t": tenant},
            )
        ).one()
    return fila[0]


async def _limpiar(tenants: tuple[str, ...]) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text("DELETE FROM vec.ticket_embeddings WHERE tenant_id = ANY(:ts)"),
            {"ts": list(tenants)},
        )
        await db.commit()


async def test_dedup_preview_devuelve_los_similares_del_tenant_ordenados_y_no_los_de_otro(
    client, seed_dos_tenants, crear_ticket, embeber_fijo
) -> None:
    a, b = seed_dos_tenants
    consulta = _vector(0)
    cercano = await crear_ticket(tenant_id=a, titulo="Cercano a la consulta")
    lejano = await crear_ticket(tenant_id=a, titulo="Algo parecido, menos")
    ajeno = await crear_ticket(tenant_id=b, titulo="Idéntico pero de otro tenant")
    try:
        await _sembrar_embedding(
            a,
            await _id_del_ticket(cercano["numero"], a),
            _con_similitud(consulta, _vector(1), 0.95),
        )
        await _sembrar_embedding(
            a, await _id_del_ticket(lejano["numero"], a), _con_similitud(consulta, _vector(2), 0.80)
        )
        await _sembrar_embedding(
            b, await _id_del_ticket(ajeno["numero"], b), consulta
        )  # coseno 1,0, pero de B
        embeber_fijo(consulta)

        resp = await client.post(
            "/api/v1/tickets/dedup-preview",
            json={"texto": "Texto de la consulta de deduplicación", "k": 5},
            headers=auth_headers(a),
        )
        assert resp.status_code == 200, resp.text
        similares = resp.json()["similares"]
        # Los números (KC-1001...) se repiten entre tenants: se comparan los títulos.
        titulos = [s["titulo"] for s in similares]
        assert "Idéntico pero de otro tenant" not in titulos, (
            "la búsqueda no puede devolver un ticket de otro tenant"
        )
        assert titulos == ["Cercano a la consulta", "Algo parecido, menos"], (
            "deben venir ordenados por similitud"
        )
        assert [float(s["similitud"]) for s in similares] == [0.95, 0.8]
    finally:
        await _limpiar((a, b))


async def test_impact_preview_sin_definiciones_responde_vacio(
    client, seed_dos_tenants, embeber_fijo
) -> None:
    """La consulta (con configurar_busqueda y la reordenación) es válida aunque no haya definiciones."""
    a, _ = seed_dos_tenants
    embeber_fijo(_vector(0))
    resp = await client.post(
        "/api/v1/tickets/impact-preview",
        json={"texto": "Texto de la consulta de impacto"},
        headers=auth_headers(a),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["modulos"] == []


async def test_los_parametros_hnsw_no_se_filtran_fuera_de_la_transaccion(
    client, seed_dos_tenants, embeber_fijo
) -> None:
    """Tras una búsqueda, ninguna sesión nueva trae los valores del caso de uso (era SET LOCAL)."""
    a, _ = seed_dos_tenants
    embeber_fijo(_vector(0))
    await client.post(
        "/api/v1/tickets/dedup-preview",
        json={"texto": "Texto de la consulta de deduplicación"},
        headers=auth_headers(a),
    )
    async with sesion_sistema() as db:
        await db.execute(text("SELECT '[1]'::vector"))  # carga pgvector: registra hnsw.*
        ef = (await db.execute(text("SHOW hnsw.ef_search"))).scalar()
    assert ef == "40", "el ef_search del caso de uso no puede quedar en la conexión"
