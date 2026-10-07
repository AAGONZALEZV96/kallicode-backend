"""Refresco por snapshot y cola de curación de cm (KC-DB-04-3, cierra CM-F05).

Requiere db/migrations/019 aplicada. Un criterio de aceptación por grupo de
pruebas: cambio atómico, retirada sin huérfanos, umbral de confianza y
estadísticas del snapshot.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion

_TABLAS_CM = (
    "snapshot_vigente",
    "edges",
    "curation_queue",
    "concepts",
    "definitions",
    "symbols",
    "files",
    "db_columns",
    "db_tables",
    "repo_snapshots",
)


@pytest.fixture
async def tenant() -> AsyncIterator[str]:
    t = f"org_snap_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba snapshots')"),
            {"t": t},
        )
    try:
        yield t
    finally:
        async with db.sesion_tenant(t) as s:
            for tabla in _TABLAS_CM:
                await s.execute(text(f"DELETE FROM cm.{tabla}"))
        async with db.sesion_sistema() as admin:
            await admin.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": t})


# --- helpers de datos -------------------------------------------------------
async def _snapshot(s: AsyncSession, snap: str) -> None:
    await s.execute(
        text(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
            "VALUES (:i, core.current_tenant(), 'abc123', 'main')"
        ),
        {"i": snap},
    )


async def _archivo(s: AsyncSession, snap: str, nombre: str) -> str:
    fid = f"file_{nombre}"
    await s.execute(
        text(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES (:i, core.current_tenant(), :p, 'python', 'sha', :s)"
        ),
        {"i": fid, "p": f"src/{nombre}.py", "s": snap},
    )
    return fid


async def _simbolo(s: AsyncSession, fid: str, nombre: str) -> str:
    sid = f"sym_{nombre}"
    await s.execute(
        text(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) VALUES (:i, core.current_tenant(), :f, 'function', :n, 1, 2, 'def f()')"
        ),
        {"i": sid, "f": fid, "n": nombre},
    )
    return sid


async def _arista(
    s: AsyncSession, snap: str, src: str, dst: str, confianza: float | None = None
) -> None:
    props = "'{}'::jsonb" if confianza is None else f"'{{\"confidence\": {confianza}}}'::jsonb"
    await s.execute(
        text(
            "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, props, "
            f"snapshot_id) VALUES (core.current_tenant(), 'CALLS', 'symbol', :a, 'symbol', :b, "
            f"{props}, :s)"
        ),
        {"a": src, "b": dst, "s": snap},
    )


async def _vigente(s: AsyncSession) -> tuple[str, str]:
    fila = (
        await s.execute(
            text(
                "SELECT v.snapshot_id, s.estado FROM cm.snapshot_vigente v "
                "JOIN cm.repo_snapshots s "
                "ON s.tenant_id = v.tenant_id AND s.id = v.snapshot_id"
            )
        )
    ).one()
    return fila[0], fila[1]


async def _publicar(s: AsyncSession, snap: str, nodos: int, aristas: int) -> str | None:
    return (
        await s.execute(
            text("SELECT cm.publicar_snapshot(:s, :n, :a)"),
            {"s": snap, "n": nodos, "a": aristas},
        )
    ).scalar_one()


async def _contar(s: AsyncSession, tabla: str) -> int:
    return (await s.execute(text(f"SELECT count(*) FROM cm.{tabla}"))).scalar_one()


# --- criterio 1: el cambio de snapshot vigente es atómico --------------------
async def test_publicar_es_atomico_con_dos_sesiones_concurrentes(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        await _archivo(s, "snap_a", "a1")
        assert await _publicar(s, "snap_a", 1, 0) is None
        await _snapshot(s, "snap_b")
        await _archivo(s, "snap_b", "b1")

    async with db.sesion_tenant(tenant) as escritor:
        assert await _publicar(escritor, "snap_b", 1, 0) == "snap_a"
        # El escritor aún no confirma: el lector sigue viendo el estado anterior completo.
        async with db.sesion_tenant(tenant) as lector:
            assert await _vigente(lector) == ("snap_a", "completo")

    async with db.sesion_tenant(tenant) as lector:
        assert await _vigente(lector) == ("snap_b", "completo")
        estados = (
            await lector.execute(text("SELECT id, estado FROM cm.repo_snapshots ORDER BY id"))
        ).all()
        assert [tuple(e) for e in estados] == [("snap_a", "retirado"), ("snap_b", "completo")]


async def test_ningun_lector_ve_estado_intermedio_durante_varios_cambios(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        for i in range(6):
            await _snapshot(s, f"snap_{i}")
            await _archivo(s, f"snap_{i}", f"f{i}")
        await _publicar(s, "snap_0", 1, 0)

    hecho = asyncio.Event()
    observados: list[tuple[str, str]] = []

    async def lector() -> None:
        while not hecho.is_set():
            async with db.sesion_tenant(tenant) as s:
                observados.append(await _vigente(s))

    async def escritor() -> None:
        for i in range(1, 6):
            async with db.sesion_tenant(tenant) as s:
                await _publicar(s, f"snap_{i}", 1, 0)
        hecho.set()

    await asyncio.gather(lector(), lector(), escritor())
    assert observados
    assert {estado for _, estado in observados} == {"completo"}


async def test_recuentos_incorrectos_abortan_y_el_puntero_no_se_mueve(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        await _archivo(s, "snap_a", "a1")
        await _publicar(s, "snap_a", 1, 0)
        await _snapshot(s, "snap_b")
        await _archivo(s, "snap_b", "b1")

    with pytest.raises(DBAPIError, match="no coinciden"):
        async with db.sesion_tenant(tenant) as s:
            await _publicar(s, "snap_b", 99, 0)

    async with db.sesion_tenant(tenant) as s:
        assert await _vigente(s) == ("snap_a", "completo")
        estado_b: str = (
            await s.execute(text("SELECT estado FROM cm.repo_snapshots WHERE id = 'snap_b'"))
        ).scalar_one()
        assert estado_b == "en_progreso"


# --- criterio 2: retirar el snapshot antiguo no deja huérfanos --------------
_HUERFANAS = (
    "SELECT count(*) FROM cm.edges "
    "WHERE NOT (cm._nodo_existe(src_kind, src_id) AND cm._nodo_existe(dst_kind, dst_id))"
)


async def test_retirar_snapshot_antiguo_borra_nodos_y_aristas_sin_huerfanos(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        fa1 = await _archivo(s, "snap_a", "a1")
        fa2 = await _archivo(s, "snap_a", "a2")
        sa1 = await _simbolo(s, fa1, "sa1")
        sa2 = await _simbolo(s, fa2, "sa2")
        await _arista(s, "snap_a", sa1, sa2)
        await _publicar(s, "snap_a", 4, 1)

        await _snapshot(s, "snap_b")
        fb1 = await _archivo(s, "snap_b", "b1")
        sb1 = await _simbolo(s, fb1, "sb1")
        # Arista del snapshot nuevo que apunta a un nodo que solo existe en el viejo.
        await _arista(s, "snap_b", sb1, sa2)
        await _publicar(s, "snap_b", 2, 1)

    async with db.sesion_tenant(tenant) as s:
        antes = {t: await _contar(s, t) for t in ("files", "symbols", "edges")}
        assert antes == {"files": 3, "symbols": 3, "edges": 2}

        nodos, aristas = (
            await s.execute(text("SELECT * FROM cm.retirar_snapshot('snap_a')"))
        ).one()
        assert (nodos, aristas) == (4, 2)

        despues = {t: await _contar(s, t) for t in ("files", "symbols", "edges")}
        assert despues == {"files": 1, "symbols": 1, "edges": 0}
        assert (await s.execute(text(_HUERFANAS))).scalar_one() == 0


async def test_no_se_puede_retirar_el_snapshot_vigente(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        await _archivo(s, "snap_a", "a1")
        await _publicar(s, "snap_a", 1, 0)

    with pytest.raises(DBAPIError, match="solo se retira"):
        async with db.sesion_tenant(tenant) as s:
            await s.execute(text("SELECT * FROM cm.retirar_snapshot('snap_a')"))


# --- criterio 3: confianza < 0,75 se rechaza y se encola ---------------------
async def test_arista_bajo_el_umbral_se_rechaza_y_se_encola(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        fa = await _archivo(s, "snap_a", "a1")
        x = await _simbolo(s, fa, "x")
        y = await _simbolo(s, fa, "y")
        z = await _simbolo(s, fa, "z")

        await _arista(s, "snap_a", x, y, confianza=0.749)
        assert await _contar(s, "edges") == 0
        cola = (
            await s.execute(
                text("SELECT tipo, estado, payload FROM cm.curation_queue ORDER BY creado_en")
            )
        ).all()
        assert len(cola) == 1
        assert (cola[0][0], cola[0][1]) == ("enlace", "pendiente")
        assert cola[0][2]["src_id"] == x and cola[0][2]["confidence"] == 0.749

        await _arista(s, "snap_a", x, z, confianza=0.750)  # justo en el umbral: entra
        assert await _contar(s, "edges") == 1
        assert await _contar(s, "curation_queue") == 1

        await _arista(s, "snap_a", y, z)  # sin confianza declarada: entra
        assert await _contar(s, "edges") == 2
        assert await _contar(s, "curation_queue") == 1


async def test_arista_rechazada_no_duplica_la_cola_pendiente(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        fa = await _archivo(s, "snap_a", "a1")
        x = await _simbolo(s, fa, "x")
        y = await _simbolo(s, fa, "y")
        await _arista(s, "snap_a", x, y, confianza=0.5)
        await _arista(s, "snap_a", x, y, confianza=0.6)
        assert await _contar(s, "curation_queue") == 1


async def test_concepto_bajo_el_umbral_se_rechaza_y_se_encola(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        for nombre, conf in (("bajo", 0.749), ("limite", 0.750)):
            await s.execute(
                text(
                    "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion, "
                    "confianza, snapshot_id) VALUES (:i, core.current_tenant(), :n, :n, 'd', :c, "
                    "'snap_a')"
                ),
                {"i": f"con_{nombre}", "n": nombre, "c": conf},
            )
        assert await _contar(s, "concepts") == 1
        assert (await s.execute(text("SELECT id FROM cm.concepts"))).scalar_one() == "con_limite"
        cola = (await s.execute(text("SELECT tipo, payload FROM cm.curation_queue"))).all()
        assert len(cola) == 1
        assert cola[0][0] == "concepto" and cola[0][1]["id"] == "con_bajo"


# --- criterio 4: estadísticas del snapshot por kind ---------------------------
async def test_stats_snapshot_cuenta_nodos_y_aristas_por_kind(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        await _snapshot(s, "snap_b")
        fa = await _archivo(s, "snap_a", "a1")
        x = await _simbolo(s, fa, "x")
        y = await _simbolo(s, fa, "y")
        await _arista(s, "snap_a", x, y)
        await s.execute(
            text(
                "INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre, snapshot_id) "
                "VALUES ('dbt_1', core.current_tenant(), 'public', 't', 'snap_a')"
            )
        )
        await s.execute(
            text(
                "INSERT INTO cm.db_columns (id, tenant_id, table_id, nombre, tipo, nullable) "
                "VALUES ('dbc_1', core.current_tenant(), 'dbt_1', 'c', 'int', false)"
            )
        )
        await _archivo(s, "snap_b", "b1")

        filas = (
            await s.execute(text("SELECT kind, nodos, aristas FROM cm.stats_snapshot('snap_a')"))
        ).all()
        stats = {k: (n, a) for k, n, a in filas}
        assert stats == {
            "concept": (0, 0),
            "db_column": (1, 0),
            "db_table": (1, 0),
            "definition": (0, 0),
            "file": (1, 0),
            "symbol": (2, 1),
        }
        # El otro snapshot no se mezcla.
        otros = (
            await s.execute(
                text("SELECT sum(nodos), sum(aristas) FROM cm.stats_snapshot('snap_b')")
            )
        ).one()
        assert tuple(otros) == (1, 0)
