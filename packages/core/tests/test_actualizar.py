"""KC-BE-07-5 (cierra BE-F36): helper actualizar() con allowlist de columnas."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from kallicode_core.db import actualizar, sesion_sistema, uno
from sqlalchemy import text


class SesionEspia:
    """Doble de AsyncSession: guarda el SQL y los parámetros de cada execute()."""

    def __init__(self, filas: int = 1) -> None:
        self.llamadas: list[tuple[str, dict]] = []
        self.filas = filas

    async def execute(self, sql, parametros=None):
        self.llamadas.append((str(sql), dict(parametros or {})))
        return SimpleNamespace(rowcount=self.filas)


PERMITIDAS = frozenset({"nombre", "idioma"})


async def test_un_solo_update_con_todas_las_columnas_y_valores_como_parametros() -> None:
    db = SesionEspia(filas=1)

    filas = await actualizar(
        db,
        "core.users",
        {"nombre": "Ana", "idioma": "en"},
        permitidas=PERMITIDAS,
        donde={"id": "u_1", "tenant_id": "org_1"},
    )

    assert filas == 1
    assert db.llamadas == [
        (
            "UPDATE core.users SET nombre = :v_nombre, idioma = :v_idioma "
            "WHERE id = :w_id AND tenant_id = :w_tenant_id",
            {"v_nombre": "Ana", "v_idioma": "en", "w_id": "u_1", "w_tenant_id": "org_1"},
        )
    ]


async def test_un_valor_malicioso_viaja_como_parametro_y_no_toca_el_sql() -> None:
    db = SesionEspia()
    malicioso = "x'; DROP TABLE core.users; --"

    await actualizar(
        db, "core.users", {"nombre": malicioso}, permitidas=PERMITIDAS, donde={"id": "u_1"}
    )

    sql, parametros = db.llamadas[0]
    assert "DROP" not in sql
    assert parametros["v_nombre"] == malicioso


async def test_misma_columna_en_cambios_y_en_donde_no_choca() -> None:
    db = SesionEspia()

    await actualizar(
        db, "core.users", {"nombre": "Nuevo"}, permitidas=PERMITIDAS, donde={"nombre": "Viejo"}
    )

    assert db.llamadas[0][1] == {"v_nombre": "Nuevo", "w_nombre": "Viejo"}


async def test_sin_cambios_no_ejecuta_nada() -> None:
    db = SesionEspia()

    assert await actualizar(db, "core.users", {}, permitidas=PERMITIDAS, donde={"id": "u_1"}) == 0
    assert db.llamadas == []


@pytest.mark.parametrize("columna", ["password_hash", "rol", "tenant_id"])
async def test_una_columna_fuera_de_la_lista_se_rechaza_sin_ejecutar(columna: str) -> None:
    db = SesionEspia()

    with pytest.raises(ValueError, match=f"Columnas no permitidas en core.users: {columna}"):
        await actualizar(
            db,
            "core.users",
            {"nombre": "Ana", columna: "x"},
            permitidas=PERMITIDAS,
            donde={"id": "u"},
        )
    assert db.llamadas == []


@pytest.mark.parametrize(
    ("tabla", "cambios", "permitidas", "donde"),
    [
        # Aunque alguien lo agregue a la lista, un identificador raro no llega al SQL.
        ("core.users", {"nombre = 'x' --": 1}, {"nombre = 'x' --"}, {"id": "u"}),
        ("core.users", {"Nombre": 1}, {"Nombre"}, {"id": "u"}),
        ("core.users; DROP TABLE core.users", {"nombre": 1}, PERMITIDAS, {"id": "u"}),
        ("a.b.c", {"nombre": 1}, PERMITIDAS, {"id": "u"}),
        ("core.users", {"nombre": 1}, PERMITIDAS, {"id = id OR 1": "u"}),
    ],
)
async def test_identificadores_invalidos_se_rechazan(tabla, cambios, permitidas, donde) -> None:
    db = SesionEspia()

    with pytest.raises(ValueError, match="Identificador SQL inválido"):
        await actualizar(db, tabla, cambios, permitidas=frozenset(permitidas), donde=donde)
    assert db.llamadas == []


async def test_sin_filtro_no_actualiza_la_tabla_entera() -> None:
    db = SesionEspia()

    with pytest.raises(ValueError, match="exige un filtro"):
        await actualizar(db, "core.users", {"nombre": "Ana"}, permitidas=PERMITIDAS, donde={})
    assert db.llamadas == []


@pytest.mark.integracion
async def test_contra_postgres_actualiza_solo_la_fila_filtrada() -> None:
    a, b = f"org_upd_{uuid.uuid4().hex[:8]}", f"org_upd_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        for org in (a, b):
            await db.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Original')"),
                {"id": org},
            )

        filas = await actualizar(
            db,
            "core.organizations",
            {"nombre": "Cambiada", "idioma": "pt"},
            permitidas=frozenset({"nombre", "idioma", "timezone"}),
            donde={"id": a},
        )

        assert filas == 1
        fila_a = await uno(
            db, "SELECT nombre, idioma FROM core.organizations WHERE id=:i", {"i": a}
        )
        fila_b = await uno(db, "SELECT nombre FROM core.organizations WHERE id=:i", {"i": b})
        assert fila_a == {"nombre": "Cambiada", "idioma": "pt"}
        assert fila_b == {"nombre": "Original"}
        await db.rollback()
