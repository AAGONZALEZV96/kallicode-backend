"""vec.definition_embeddings consolidada contra cm.nodes (KC-DB-04-4, cierra DB-F18).

Requiere db/migrations/022 aplicada. Un grupo de pruebas por criterio de
aceptación: la FK compuesta con cascada, de_capa_chk y la conversión con acta,
y el aislamiento entre tenants.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integracion

MIGRACION = Path(__file__).resolve().parents[4] / "db" / "migrations" / "022_vec_fk_cm.sql"
KINDS = {"file", "symbol", "db_table", "db_column", "definition", "concept"}

_VECTOR = str([0.1] * 1024)


async def _snapshot_y_archivo(s, snap: str = "snap_v") -> str:
    await s.execute(
        text(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
            "VALUES (:i, core.current_tenant(), 'abc', 'main')"
        ),
        {"i": snap},
    )
    await s.execute(
        text(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES ('file_v', core.current_tenant(), 'src/v.py', 'python', 'sha', :s)"
        ),
        {"s": snap},
    )
    return "file_v"


async def _simbolo(s, nombre: str) -> str:
    sid = f"sym_{nombre}"
    await s.execute(
        text(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) VALUES (:i, core.current_tenant(), 'file_v', 'function', :n, 1, 2, 'f()')"
        ),
        {"i": sid, "n": nombre},
    )
    return sid


async def _embedding(s, nodo_ref: str, capa: str = "symbol", tenant: str | None = None) -> None:
    await s.execute(
        text(
            "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, "
            "descripcion, embedding, texto_hash) VALUES (:id, COALESCE(:t, core.current_tenant()), "
            ":n, :c, 'n', 'd', CAST(:e AS vector), 'h')"
        ),
        {"id": f"de_{uuid.uuid4().hex[:12]}", "t": tenant, "n": nodo_ref, "c": capa, "e": _VECTOR},
    )


async def _borrar_tenant(t: str) -> None:
    async with db.sesion_sistema() as admin:
        for tabla in (
            "vec.definition_embeddings",
            "cm.concepts",
            "cm.symbols",
            "cm.files",
            "cm.nodes",
            "cm.repo_snapshots",
        ):
            await admin.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": t})
        await admin.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": t})


@pytest.fixture
async def tenant() -> AsyncIterator[str]:
    t = f"org_vec_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba vec')"),
            {"t": t},
        )
    try:
        yield t
    finally:
        await _borrar_tenant(t)


@pytest.fixture
async def dos_tenants() -> AsyncIterator[tuple[str, str]]:
    a, b = f"org_vca_{uuid.uuid4().hex[:10]}", f"org_vcb_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        for t in (a, b):
            await admin.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba vec')"),
                {"t": t},
            )
    try:
        yield a, b
    finally:
        for t in (a, b):
            await _borrar_tenant(t)


# --- criterio 1: de_nodo_fk compuesta y borrar un nodo borra su embedding ----
async def test_de_nodo_fk_es_compuesta_con_cascada_y_validada() -> None:
    async with db.sesion_sistema() as admin:
        fila = (
            await admin.execute(
                text(
                    "SELECT confrelid::regclass::text, confdeltype::text, convalidated, "
                    "       (SELECT array_agg(attname ORDER BY k.ord) "
                    "          FROM unnest(conkey) WITH ORDINALITY k(n, ord) "
                    "          JOIN pg_attribute a ON a.attrelid = conrelid AND a.attnum = k.n), "
                    "       (SELECT array_agg(attname ORDER BY k.ord) "
                    "          FROM unnest(confkey) WITH ORDINALITY k(n, ord) "
                    "          JOIN pg_attribute a ON a.attrelid = confrelid AND a.attnum = k.n) "
                    "  FROM pg_constraint WHERE conname = 'de_nodo_fk' "
                    "   AND conrelid = 'vec.definition_embeddings'::regclass"
                )
            )
        ).one()
    destino, on_delete, validada, columnas, columnas_destino = fila
    assert destino == "cm.nodes"
    assert on_delete == "c"  # ON DELETE CASCADE
    assert validada is True
    assert columnas == ["tenant_id", "nodo_ref"]
    assert columnas_destino == ["tenant_id", "id"]


async def test_borrar_un_nodo_elimina_su_embedding_en_la_misma_transaccion(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot_y_archivo(s)
        sym = await _simbolo(s, "a")
        await _embedding(s, sym)
    async with db.sesion_tenant(tenant) as s:
        assert (
            await s.execute(text("SELECT count(*) FROM vec.definition_embeddings"))
        ).scalar_one() == 1

    async with db.sesion_tenant(tenant) as s:
        await s.execute(text("DELETE FROM cm.symbols WHERE id = 'sym_a'"))
        # Sin haber confirmado aún: dentro de la misma transacción el embedding ya no existe.
        restantes: int = (
            await s.execute(text("SELECT count(*) FROM vec.definition_embeddings"))
        ).scalar_one()
        assert restantes == 0
        assert (
            await s.execute(text("SELECT count(*) FROM cm.nodes WHERE id = 'sym_a'"))
        ).scalar_one() == 0


async def test_borrar_el_archivo_arrastra_simbolos_nodos_y_embeddings(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot_y_archivo(s)
        for n in ("a", "b"):
            await _embedding(s, await _simbolo(s, n))
        await _embedding(s, "file_v", capa="file")
    async with db.sesion_tenant(tenant) as s:
        await s.execute(text("DELETE FROM cm.files WHERE id = 'file_v'"))
        assert (
            await s.execute(text("SELECT count(*) FROM vec.definition_embeddings"))
        ).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM cm.nodes"))).scalar_one() == 0


async def test_un_embedding_sin_nodo_se_rechaza(tenant: str) -> None:
    with pytest.raises(IntegrityError, match="de_nodo_fk"):
        async with db.sesion_tenant(tenant) as s:
            await _embedding(s, "no_existe")


# --- criterio 2: de_capa_chk con los kinds y conversión con acta -------------
async def test_de_capa_chk_admite_exactamente_los_kinds_y_rechaza_los_antiguos(tenant: str) -> None:
    async with db.sesion_sistema() as admin:
        catalogo = {
            r[0] for r in (await admin.execute(text("SELECT kind FROM cm.node_types"))).all()
        }
        definicion: str = (
            await admin.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'de_capa_chk' AND conrelid = 'vec.definition_embeddings'::regclass"
                )
            )
        ).scalar_one()
    assert catalogo == KINDS
    assert all(f"'{k}'" in definicion for k in KINDS)
    assert not any(f"'{v}'" in definicion for v in ("definicion", "negocio", "codigo"))

    async with db.sesion_tenant(tenant) as s:
        await _snapshot_y_archivo(s)
        sym = await _simbolo(s, "a")
    for antigua in ("definicion", "negocio", "codigo"):
        with pytest.raises(IntegrityError, match="de_capa_chk"):
            async with db.sesion_tenant(tenant) as s:
                await _embedding(s, sym, capa=antigua)


def _seccion_de_conversion() -> str:
    sql = MIGRACION.read_text(encoding="utf-8")
    ini = sql.index("INSERT INTO vec.acta_conversion_capa (migracion, capa_antigua, capa_nueva,")
    fin = sql.index("-- 3. Restricciones nuevas")
    return sql[ini : sql.rindex("-- ---", ini, fin)]


async def test_la_conversion_cambia_la_capa_por_el_kind_y_deja_acta(tenant: str) -> None:
    """Re-ejecuta la sección de conversión de la propia migración sobre datos
    antiguos (capa vieja, un huérfano) dentro de una transacción que se revierte."""
    async with db.sesion_tenant(tenant) as s:
        await _snapshot_y_archivo(s)
        sym = await _simbolo(s, "a")
        await s.execute(
            text(
                "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion) "
                "VALUES ('con_v', core.current_tenant(), 'c', 'c', 'd')"
            )
        )

    class _Revertir(Exception):
        pass

    resultado: dict = {}
    with pytest.raises(_Revertir):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text("ALTER TABLE vec.definition_embeddings DROP CONSTRAINT de_capa_chk")
            )
            await admin.execute(
                text("ALTER TABLE vec.definition_embeddings DROP CONSTRAINT de_nodo_fk")
            )
            for nodo, capa_vieja in (
                (sym, "codigo"),
                ("con_v", "negocio"),
                ("fantasma", "definicion"),
            ):
                await admin.execute(
                    text(
                        "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, "
                        "descripcion, embedding, texto_hash) VALUES (:id, :t, :n, :c, 'n', 'd', "
                        "CAST(:e AS vector), 'h')"
                    ),
                    {
                        "id": f"de_{uuid.uuid4().hex[:12]}",
                        "t": tenant,
                        "n": nodo,
                        "c": capa_vieja,
                        "e": _VECTOR,
                    },
                )
            await admin.execute(text("DELETE FROM vec.acta_conversion_capa"))
            for sentencia in _seccion_de_conversion().split(";\n"):
                if sentencia.strip().strip("-").strip():
                    await admin.execute(text(sentencia))
            resultado["capas"] = dict(
                (
                    await admin.execute(
                        text(
                            "SELECT nodo_ref, capa FROM vec.definition_embeddings WHERE tenant_id = :t"
                        ),
                        {"t": tenant},
                    )
                ).all()
            )
            resultado["acta"] = (
                await admin.execute(
                    text(
                        "SELECT capa_antigua, capa_nueva, filas_convertidas, filas_huerfanas, ids_huerfanos "
                        "FROM vec.acta_conversion_capa ORDER BY capa_antigua"
                    )
                )
            ).all()
            raise _Revertir

    assert resultado["capas"] == {sym: "symbol", "con_v": "concept"}  # el huérfano se eliminó
    acta = {fila[0]: fila[1:] for fila in resultado["acta"]}
    assert acta["codigo"][:3] == ("symbol", 1, 0)
    assert acta["negocio"][:3] == ("concept", 1, 0)
    assert acta["definicion"][:3] == (None, 0, 1)
    assert acta["definicion"][3][0]["nodo_ref"] == "fantasma"

    # El rollback devolvió las restricciones.
    async with db.sesion_sistema() as admin:
        existen = (
            await admin.execute(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid = 'vec.definition_embeddings'::regclass "
                    "AND conname IN ('de_capa_chk', 'de_nodo_fk')"
                )
            )
        ).scalar_one()
    assert existen == 2


# --- criterio 3: ningún embedding apunta a un nodo de otro tenant -------------
async def test_un_embedding_no_puede_apuntar_a_un_nodo_de_otro_tenant(
    dos_tenants: tuple[str, str],
) -> None:
    a, b = dos_tenants
    async with db.sesion_tenant(b) as s:
        await _snapshot_y_archivo(s)
        sym_b = await _simbolo(s, "de_b")

    # El tenant A intenta colgar un embedding del nodo del tenant B.
    with pytest.raises(IntegrityError, match="de_nodo_fk"):
        async with db.sesion_tenant(a) as s:
            await _embedding(s, sym_b)
    # Ni siquiera la sesión de sistema (sin RLS) puede cruzar tenants.
    with pytest.raises(IntegrityError, match="de_nodo_fk"):
        async with db.sesion_sistema() as admin:
            await _embedding(admin, sym_b, tenant=a)

    async with db.sesion_tenant(b) as s:
        assert (
            await s.execute(text("SELECT count(*) FROM vec.definition_embeddings"))
        ).scalar_one() == 0


async def test_cm_nodes_se_sincroniza_y_no_admite_ids_repetidos_entre_kinds(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await _snapshot_y_archivo(s)
        sym = await _simbolo(s, "a")
        kinds: dict[str, str] = dict((await s.execute(text("SELECT id, kind FROM cm.nodes"))).all())
        assert kinds == {"file_v": "file", sym: "symbol"}

    # Un concepto con el id de un símbolo existente viola la PK de cm.nodes.
    with pytest.raises(IntegrityError, match="nodes_pkey"):
        async with db.sesion_tenant(tenant) as s:
            await s.execute(
                text(
                    "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion) "
                    "VALUES (:i, core.current_tenant(), 'c', 'c', 'd')"
                ),
                {"i": sym},
            )
