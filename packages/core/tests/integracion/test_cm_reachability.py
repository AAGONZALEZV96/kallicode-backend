"""Límites de recorrido y cierre transitivo materializado en cm.reachability (KC-DB-11-2).

Requiere db/migrations/059 aplicada. Criterios de aceptación:
  1. cm.reachability con PK (tenant_id, src_id, dst_id), índice inverso y saltos 1..3, refrescada
     en el cambio de snapshot (test_estructura_..., test_el_check_..., test_publicar_...,
     test_retirar_..., test_una_arista_nueva_...).
  2. cm.impacto usa la tabla cuando está vigente y cae al CTE cuando no, con el mismo resultado,
     que además coincide con SQL_IMPACTO / SQL_DEPENDENCIAS de kallicode_core.cm_impacto
     (test_las_dos_rutas_..., test_grafo_aleatorio_...).
  3. El límite de nodos visitados produce un resultado truncado explícito y detiene el recorrido
     (test_el_limite_de_visitas_..., test_el_limite_detiene_...).
  4. La política de cm.nodes y cm.edges sigue siendo exactamente tenant_id = core.current_tenant()
     (test_las_politicas_...), y todo WITH RECURSIVE de cm lleva CYCLE.
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from kallicode_core import cm_impacto, db
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion

TIPOS_DEP = ["CALLS", "HAS_COLUMN", "REFERENCES", "USES_TABLE"]
POLITICA = "(tenant_id = core.current_tenant())"

_TABLAS_CM = (
    "edges",
    "reachability",
    "reachability_estado",
    "snapshot_vigente",
    "curation_queue",
    "symbols",
    "files",
    "repo_snapshots",
)

Fila = tuple[str, str, int, str | None]


async def _org(prefijo: str) -> str:
    t = f"org_{prefijo}_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba reach')"),
            {"t": t},
        )
    return t


async def _borrar_org(t: str) -> None:
    async with db.sesion_tenant(t) as s:
        for tabla in _TABLAS_CM:
            await s.execute(text(f"DELETE FROM cm.{tabla}"))
    async with db.sesion_sistema() as admin:
        await admin.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": t})


@pytest.fixture
async def tenant() -> AsyncIterator[str]:
    t = await _org("reach")
    try:
        yield t
    finally:
        await _borrar_org(t)


@pytest.fixture
async def otro_tenant() -> AsyncIterator[str]:
    t = await _org("reachb")
    try:
        yield t
    finally:
        await _borrar_org(t)


# --- helpers de datos -------------------------------------------------------
async def _snapshot(s: AsyncSession, snap: str) -> None:
    await s.execute(
        text(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
            "VALUES (:i, core.current_tenant(), 'abc123', 'main')"
        ),
        {"i": snap},
    )


async def _fichero(s: AsyncSession, fid: str, snap: str | None = None) -> None:
    """Fichero de un snapshot; sin snapshot dado, crea uno propio que no se publica."""
    if snap is None:
        snap = f"snap_{fid}"
        await _snapshot(s, snap)
    await s.execute(
        text(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES (:i, core.current_tenant(), :p, 'python', 'sha', :s)"
        ),
        {"i": fid, "p": f"src/{fid}.py", "s": snap},
    )


async def _simbolos(s: AsyncSession, fid: str, ids: Sequence[str]) -> None:
    await s.execute(
        text(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) SELECT x, core.current_tenant(), :f, 'function', x, 1, 2, 'def f()' "
            "FROM unnest(CAST(:ids AS text[])) AS x"
        ),
        {"f": fid, "ids": list(ids)},
    )


async def _aristas(
    s: AsyncSession,
    aristas: Sequence[tuple[str, str, str]],
    snap: str | None = None,
    src_kind: str = "symbol",
) -> None:
    """(src, tipo, dst): src depende de dst."""
    await s.execute(
        text(
            "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, "
            "snapshot_id) SELECT core.current_tenant(), a.tipo, :k, a.src, 'symbol', a.dst, :s "
            "FROM unnest(CAST(:src AS text[]), CAST(:tipo AS text[]), CAST(:dst AS text[])) "
            "AS a(src, tipo, dst)"
        ),
        {
            "src": [a[0] for a in aristas],
            "tipo": [a[1] for a in aristas],
            "dst": [a[2] for a in aristas],
            "k": src_kind,
            "s": snap,
        },
    )


async def _refrescar(s: AsyncSession, presupuesto: int | None = None) -> tuple[str, int, int]:
    sql = (
        "SELECT * FROM cm.refrescar_reachability()"
        if presupuesto is None
        else "SELECT * FROM cm.refrescar_reachability(CAST(:p AS bigint))"
    )
    fila = (await s.execute(text(sql), {"p": presupuesto})).one()
    return fila[0], fila[1], fila[2]


async def _estado(s: AsyncSession) -> tuple[str, str | None]:
    fila = (await s.execute(text("SELECT estado, snapshot_id FROM cm.reachability_estado"))).one()
    return fila[0], fila[1]


async def _impacto(
    s: AsyncSession,
    nodos: Sequence[str] = (),
    *,
    ficheros: Sequence[str] = (),
    k: int = 3,
    tipos: Sequence[str] = TIPOS_DEP,
    direccion: str = "entrante",
    max_visitados: int = 5000,
    ruta: str = "auto",
) -> tuple[list[Fila], set[str], set[bool]]:
    filas = (
        await s.execute(
            text(
                "SELECT id, kind, distancia, via_tipo, fuente, truncado FROM cm.impacto("
                "CAST(:n AS text[]), CAST(:f AS text[]), CAST(:k AS integer), "
                "CAST(:t AS text[]), CAST(:d AS text), CAST(:m AS integer), CAST(:r AS text))"
            ),
            {
                "n": list(nodos),
                "f": list(ficheros),
                "k": k,
                "t": list(tipos),
                "d": direccion,
                "m": max_visitados,
                "r": ruta,
            },
        )
    ).all()
    return (
        [(f[0], f[1], f[2], f[3]) for f in filas],
        {f[4] for f in filas},
        {f[5] for f in filas},
    )


async def _python(
    s: AsyncSession, direccion: str, nodos: Sequence[str], k: int, ficheros: Sequence[str] = ()
) -> list[Fila]:
    """La misma consulta con la SQL vigente de kallicode_core.cm_impacto (contrato cm-impacto-v1)."""
    consulta = cm_impacto.ConsultaGrafoIn(nodos=list(nodos), ficheros=list(ficheros), k=k)
    sql = cm_impacto.SQL_IMPACTO if direccion == "entrante" else cm_impacto.SQL_DEPENDENCIAS
    params = cm_impacto.parametros_recorrido(consulta, direccion)  # type: ignore[arg-type]
    filas = (await s.execute(text(sql), params)).all()
    return [(f[0], f[1], f[2], f[3]) for f in filas]


# Grafo de referencia (src depende de dst). Recoge los casos que pueden separar las rutas:
#   cadena b->a, c->b, d->c, e->d (e queda a 4 saltos por la cadena, a 3 por el atajo)
#   atajo c->a REFERENCES (c a 1 salto por REFERENCES y a 2 por CALLS)
#   empate f->a por CALLS y por REFERENCES (via_tipo = CALLS, el menor en "C")
#   ciclo a->c y autorreferencia g->g
#   nodo colgante: x->i->a con i fuera de cm.nodes (se recorre, no se devuelve)
#   DEFINES fichero->a, fuera de los tipos de dependencia
_SIMBOLOS = ["a", "b", "c", "d", "e", "f", "g", "h", "x"]
_ARISTAS = [
    ("b", "CALLS", "a"),
    ("c", "CALLS", "b"),
    ("d", "CALLS", "c"),
    ("e", "CALLS", "d"),
    ("c", "REFERENCES", "a"),
    ("f", "CALLS", "a"),
    ("f", "REFERENCES", "a"),
    ("a", "CALLS", "c"),
    ("g", "CALLS", "g"),
    ("g", "CALLS", "a"),
    ("h", "USES_TABLE", "d"),
    ("x", "CALLS", "i"),
    ("i", "CALLS", "a"),
]


async def _grafo_referencia(s: AsyncSession) -> None:
    await _fichero(s, "fich_ref")
    await _simbolos(s, "fich_ref", _SIMBOLOS)
    await _aristas(s, _ARISTAS)
    await _aristas(s, [("fich_ref", "DEFINES", "a")], src_kind="file")


# --- criterio 1: estructura y refresco en el cambio de snapshot ---------------
async def test_estructura_de_la_tabla_pk_indice_inverso_y_check() -> None:
    async with db.sesion_sistema() as s:
        pk = (
            await s.execute(
                text(
                    "SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_constraint c "
                    "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(n, ord) "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n "
                    "WHERE c.conrelid = 'cm.reachability'::regclass AND c.contype = 'p'"
                )
            )
        ).scalar_one()
        assert pk == ["tenant_id", "src_id", "dst_id"]

        indice = (
            await s.execute(text("SELECT pg_get_indexdef('cm.reachability_inversa_ix'::regclass)"))
        ).scalar_one()
        assert "(tenant_id, dst_id, src_id)" in indice
        assert "INCLUDE (saltos, via_src)" in indice

        check = (
            await s.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'cm.reachability'::regclass AND conname = 'reach_saltos_chk'"
                )
            )
        ).scalar_one()
        assert "saltos >= 1" in check and "saltos <= 3" in check


@pytest.mark.parametrize(
    ("src", "dst", "saltos"),
    [("a", "b", 0), ("a", "b", 4), ("a", "a", 1)],
)
async def test_el_check_rechaza_saltos_fuera_de_1_a_3_y_autoalcance(
    tenant: str, src: str, dst: str, saltos: int
) -> None:
    with pytest.raises(DBAPIError, match="check constraint"):
        async with db.sesion_tenant(tenant) as s:
            await s.execute(
                text(
                    "INSERT INTO cm.reachability (tenant_id, src_id, dst_id, saltos, via_src, "
                    "via_dst) VALUES (core.current_tenant(), :a, :b, :n, 'CALLS', 'CALLS')"
                ),
                {"a": src, "b": dst, "n": saltos},
            )


async def test_publicar_un_snapshot_deja_la_tabla_vigente_y_retirar_la_rehace(
    tenant: str,
) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_a")
        await _fichero(s, "fa", "snap_a")
        await _simbolos(s, "fa", ["a1", "a2", "a3"])
        await _aristas(s, [("a2", "CALLS", "a1"), ("a3", "CALLS", "a2")], snap="snap_a")
        await s.execute(text("SELECT cm.publicar_snapshot('snap_a', 4, 2)"))
        assert await _estado(s) == ("vigente", "snap_a")
        filas, fuente, _ = await _impacto(s, ["a1"])
        assert fuente == {"materializada"}
        assert [f[0] for f in filas] == ["a1", "a2", "a3"]

    async with db.sesion_tenant(tenant) as s:
        await _snapshot(s, "snap_b")
        await _fichero(s, "fb", "snap_b")
        await _simbolos(s, "fb", ["b1", "b2"])
        await _aristas(s, [("b2", "CALLS", "b1")], snap="snap_b")
        # Mientras se carga, la tabla queda obsoleta: el impacto usa el CTE.
        assert (await _estado(s))[0] == "obsoleta"
        assert (await _impacto(s, ["a1"]))[1] == {"recursiva"}
        await s.execute(text("SELECT cm.publicar_snapshot('snap_b', 3, 1)"))
        assert await _estado(s) == ("vigente", "snap_b")

    async with db.sesion_tenant(tenant) as s:
        await s.execute(text("SELECT * FROM cm.retirar_snapshot('snap_a')"))
        assert await _estado(s) == ("vigente", "snap_b")
        pares = (
            await s.execute(text("SELECT src_id, dst_id FROM cm.reachability ORDER BY 1"))
        ).all()
        assert [tuple(p) for p in pares] == [("b2", "b1")]
        filas, fuente, _ = await _impacto(s, ["b1"])
        assert fuente == {"materializada"}
        assert [f[0] for f in filas] == ["b1", "b2"]


async def test_una_arista_nueva_fuera_del_snapshot_invalida_la_tabla(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _grafo_referencia(s)
        assert (await _refrescar(s))[0] == "vigente"
        antes = (await _impacto(s, ["a"]))[0]

        await _aristas(s, [("e", "CALLS", "a")])
        assert (await _estado(s))[0] == "obsoleta"
        filas, fuente, _ = await _impacto(s, ["a"])
        # Cae al CTE y ve la arista nueva: e pasa de 3 saltos a 1.
        assert fuente == {"recursiva"}
        assert ("e", "symbol", 1, "CALLS") in filas and filas != antes

    with pytest.raises(DBAPIError, match="no es utilizable"):
        async with db.sesion_tenant(tenant) as s:
            await _impacto(s, ["a"], ruta="materializada")


async def test_un_grafo_que_supera_el_presupuesto_queda_excedido_y_usa_el_cte(
    tenant: str,
) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _grafo_referencia(s)
        estado, filas, caminos = await _refrescar(s, presupuesto=5)
        assert (estado, filas) == ("excedida", 0)
        assert caminos == 6  # el recorrido se detiene en presupuesto + 1
        assert (await s.execute(text("SELECT count(*) FROM cm.reachability"))).scalar_one() == 0
        assert (await _impacto(s, ["a"]))[1] == {"recursiva"}


# --- criterio 2: las dos rutas dan el mismo resultado ------------------------
@pytest.mark.parametrize("direccion", ["entrante", "saliente"])
@pytest.mark.parametrize("k", [1, 2, 3])
@pytest.mark.parametrize("semillas", [["a"], ["a", "d"], ["c"], ["g"], ["x", "e"]])
async def test_las_dos_rutas_y_la_consulta_vigente_dan_el_mismo_resultado(
    tenant: str, direccion: str, k: int, semillas: list[str]
) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _grafo_referencia(s)
        await _refrescar(s)
        tabla, f_tabla, t_tabla = await _impacto(
            s, semillas, k=k, direccion=direccion, ruta="materializada"
        )
        cte, f_cte, t_cte = await _impacto(s, semillas, k=k, direccion=direccion, ruta="recursiva")
        python = await _python(s, direccion, semillas, k)

    assert (f_tabla, f_cte) == ({"materializada"}, {"recursiva"})
    assert t_tabla == t_cte == {False}
    assert tabla == cte == python


async def test_casos_del_grafo_de_referencia(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _grafo_referencia(s)
        await _refrescar(s)
        filas, fuente, _ = await _impacto(s, ["a"])
    assert fuente == {"materializada"}
    assert filas == [
        ("a", "symbol", 0, None),
        ("b", "symbol", 1, "CALLS"),
        ("c", "symbol", 1, "REFERENCES"),  # atajo: 1 salto, no 2 por b
        ("f", "symbol", 1, "CALLS"),  # empate CALLS / REFERENCES: el menor en "C"
        ("g", "symbol", 1, "CALLS"),  # la autorreferencia no lo duplica
        ("d", "symbol", 2, "CALLS"),
        ("x", "symbol", 2, "CALLS"),  # a través del nodo colgante i, que no se devuelve
        ("e", "symbol", 3, "CALLS"),  # e -> d -> c -> a
        ("h", "symbol", 3, "USES_TABLE"),
    ]


async def test_las_semillas_por_fichero_y_fuera_de_la_tabla_usan_el_cte(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _grafo_referencia(s)
        await _refrescar(s)
        por_fichero = await _impacto(s, ficheros=["src/fich_ref.py"])
        assert por_fichero[1] == {"materializada"}
        assert por_fichero[0] == await _python(s, "entrante", [], 3, ["src/fich_ref.py"])
        # k = 4, sentido doble u otros tipos no están materializados: CTE.
        assert (await _impacto(s, ["a"], k=4))[1] == {"recursiva"}
        assert (await _impacto(s, ["a"], direccion="ambas"))[1] == {"recursiva"}
        assert (await _impacto(s, ["a"], tipos=["CALLS"]))[1] == {"recursiva"}
        # El orden de los tipos no cambia la ruta.
        assert (await _impacto(s, ["a"], tipos=list(reversed(TIPOS_DEP))))[1] == {"materializada"}


async def test_grafo_aleatorio_las_rutas_coinciden_en_cien_consultas(tenant: str) -> None:
    rnd = random.Random(1102)
    nodos = [f"n{i:03d}" for i in range(80)]
    tipos = TIPOS_DEP + ["DEFINES", "RELATES"]
    aristas = {(rnd.choice(nodos), rnd.choice(tipos), rnd.choice(nodos)) for _ in range(260)}
    async with db.sesion_tenant(tenant) as s:
        await _fichero(s, "fich_rnd")
        await _simbolos(s, "fich_rnd", nodos)
        await _aristas(s, sorted(aristas))
        assert (await _refrescar(s))[0] == "vigente"
        for _ in range(100):
            semillas = rnd.sample(nodos, rnd.randint(1, 3))
            k = rnd.randint(1, 3)
            direccion = rnd.choice(["entrante", "saliente"])
            tabla = await _impacto(s, semillas, k=k, direccion=direccion, ruta="materializada")
            cte = await _impacto(s, semillas, k=k, direccion=direccion, ruta="recursiva")
            assert tabla[0] == cte[0], (semillas, k, direccion)
            assert tabla[0] == await _python(s, direccion, semillas, k)


# --- criterio 3: límite de visitas con truncado explícito ---------------------
async def _estrella(s: AsyncSession, n: int) -> None:
    hojas = [f"h{i:03d}" for i in range(n)]
    await _fichero(s, "fich_hub")
    await _simbolos(s, "fich_hub", ["hub", *hojas])
    await _aristas(s, [(h, "CALLS", "hub") for h in hojas])


@pytest.mark.parametrize("ruta", ["materializada", "recursiva"])
async def test_el_limite_de_visitas_devuelve_un_resultado_truncado_explicito(
    tenant: str, ruta: str
) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _estrella(s, 50)
        await _refrescar(s)
        filas, fuente, truncado = await _impacto(s, ["hub"], max_visitados=10, ruta=ruta)
        assert fuente == {ruta}
        assert truncado == {True}
        assert len(filas) == 10
        assert filas[0] == ("hub", "symbol", 0, None)  # los más cercanos primero

        completo, _, sin_truncar = await _impacto(s, ["hub"], max_visitados=51, ruta=ruta)
        assert sin_truncar == {False}
        assert len(completo) == 51


async def test_el_limite_detiene_el_recorrido_de_un_grafo_denso(tenant: str) -> None:
    """30 nodos completamente conectados a k = 5: sin límite son ~2·10^7 caminos simples."""
    nodos = [f"d{i:02d}" for i in range(30)]
    async with db.sesion_tenant(tenant) as s:
        await _fichero(s, "fich_denso")
        await _simbolos(s, "fich_denso", nodos)
        await _aristas(s, [(a, "CALLS", b) for a in nodos for b in nodos if a != b])
        await s.execute(text("SET LOCAL statement_timeout = '10s'"))
        inicio = time.monotonic()
        filas, fuente, truncado = await _impacto(
            s, ["d00"], k=5, direccion="ambas", max_visitados=1000
        )
        assert time.monotonic() - inicio < 10
    assert fuente == {"recursiva"}
    assert truncado == {True}
    assert len({f[0] for f in filas}) == len(filas) == 30


@pytest.mark.parametrize(
    ("parametro", "valor"),
    [("k", 0), ("k", 6), ("max_visitados", 0), ("direccion", "lateral"), ("ruta", "otra")],
)
async def test_los_limites_de_profundidad_y_parametros_se_validan(
    tenant: str, parametro: str, valor: Any
) -> None:
    with pytest.raises(DBAPIError, match="fuera de rango|inválida"):
        async with db.sesion_tenant(tenant) as s:
            await _impacto(s, ["a"], **{parametro: valor})


# --- aislamiento ----------------------------------------------------------------
async def test_la_tabla_y_el_impacto_respetan_el_tenant(tenant: str, otro_tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _grafo_referencia(s)
        await _refrescar(s)
    async with db.sesion_tenant(otro_tenant) as s:
        assert (await s.execute(text("SELECT count(*) FROM cm.reachability"))).scalar_one() == 0
        assert (
            await s.execute(text("SELECT count(*) FROM cm.reachability_estado"))
        ).scalar_one() == 0
        filas, _, _ = await _impacto(s, ["a"])
        assert filas == []
        # Refrescar el otro tenant no toca la tabla del primero.
        assert await _refrescar(s) == ("vigente", 0, 0)
    async with db.sesion_tenant(tenant) as s:
        assert (await _estado(s))[0] == "vigente"
        assert (await s.execute(text("SELECT count(*) FROM cm.reachability"))).scalar_one() > 0


# --- criterio 4: políticas triviales y CYCLE ------------------------------------
@pytest.mark.parametrize("tabla", ["nodes", "edges", "reachability", "reachability_estado"])
async def test_las_politicas_son_exactamente_tenant_id_igual_al_tenant_actual(tabla: str) -> None:
    async with db.sesion_sistema() as s:
        politicas = (
            await s.execute(
                text(
                    "SELECT policyname, permissive, cmd, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'cm' AND tablename = :t"
                ),
                {"t": tabla},
            )
        ).all()
        forzada = (
            await s.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:r AS regclass)"
                ),
                {"r": f"cm.{tabla}"},
            )
        ).scalar_one()
    assert forzada
    assert len(politicas) == 1, politicas
    nombre, permisiva, cmd, qual, with_check = politicas[0]
    assert (nombre, permisiva, cmd) == ("tenant_isolation", "PERMISSIVE", "ALL")
    assert qual == POLITICA
    assert with_check == POLITICA
    for expr in (qual, with_check):
        assert "SELECT" not in expr.upper() and "EXISTS" not in expr.upper()


async def test_todo_recorrido_recursivo_de_cm_lleva_cycle() -> None:
    async with db.sesion_sistema() as s:
        funciones = (
            await s.execute(
                text(
                    "SELECT p.proname, p.prosrc FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'cm' AND p.prosrc ILIKE '%WITH RECURSIVE%'"
                )
            )
        ).all()
    nombres = {f[0] for f in funciones}
    assert {"impacto", "refrescar_reachability"} <= nombres
    for nombre, cuerpo in funciones:
        assert cuerpo.upper().count("WITH RECURSIVE") <= cuerpo.upper().count(") CYCLE "), nombre
