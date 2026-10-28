"""Índices documentados en el plan (KC-DB-06-1).

Compara los índices explícitos de la base (los creados con CREATE INDEX; los que respaldan una
PRIMARY KEY o UNIQUE se justifican por su restricción) con los declarados en
docs/plan-indices.md. Falla si la base tiene un índice que el plan no documenta, y también si el
plan cita uno que ya no existe: un índice nuevo debe añadirse al plan en la misma migración.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integracion

PLAN = Path(__file__).resolve().parents[4] / "docs" / "plan-indices.md"
ESQUEMAS = ("core", "vec", "audit", "cm")
ESTADOS = {"activo", "candidato_a_retiro"}
CATEGORIAS = {
    "consulta",
    "soporte-fk",
    "unicidad",
    "funcion-sql",
    "vectorial",
    "reserva",
    "redundante",
    "sin-consulta",
}
# Una celda de la tabla termina en un «|» sin escapar (los «\|» del texto son parte de la celda).
_CELDAS = re.compile(r"(?<!\\)\|")
_ID_CONSULTA = re.compile(r"Q-\d{3}")


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _seccion(texto: str, nombre: str) -> str:
    inicio = texto.index(f"<!-- {nombre}:inicio -->")
    fin = texto.index(f"<!-- {nombre}:fin -->")
    return texto[inicio:fin]


def _filas(seccion: str) -> list[list[str]]:
    filas = []
    for linea in seccion.splitlines():
        if not linea.startswith("| "):
            continue
        celdas = [c.strip() for c in _CELDAS.split(linea.strip())[1:-1]]
        filas.append(celdas)
    return filas


@pytest.fixture(scope="module")
def plan() -> list[dict[str, str]]:
    """Las filas de la tabla del plan, como diccionarios."""
    filas = _filas(_seccion(PLAN.read_text(encoding="utf-8"), "plan-indices"))
    columnas = ["indice", "tabla", "claves", "categoria", "justificacion", "consultas", "estado"]
    datos = []
    for celdas in filas:
        if not celdas[0].startswith("`"):  # cabecera y separador
            continue
        assert len(celdas) == len(columnas), f"fila mal formada en el plan: {celdas}"
        fila = dict(zip(columnas, (c.strip("`") for c in celdas), strict=True))
        datos.append(fila)
    return datos


@pytest.fixture(scope="module")
def ids_del_inventario() -> set[str]:
    filas = _filas(_seccion(PLAN.read_text(encoding="utf-8"), "inventario"))
    return {f[0] for f in filas if _ID_CONSULTA.fullmatch(f[0])}


@pytest.fixture(scope="module")
def indices_de_la_base() -> set[str]:
    url = os.getenv("KC_DATABASE_URL_ADMIN") or os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_ADMIN no definida")
    try:
        conn = psycopg.connect(_normalizar_dsn(url))
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname || '.' || c.relname
              FROM pg_index i
              JOIN pg_class c ON c.oid = i.indexrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = ANY(%s)
               AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)
               -- Los índices de cada partición (KC-DB-07-1) son hijos del declarado en la tabla padre:
               -- se documenta el padre y las particiones nuevas no exigen tocar el plan cada mes.
               AND NOT c.relispartition
            """,
            (list(ESQUEMAS),),
        )
        return {r[0] for r in cur.fetchall()}


def test_todo_indice_explicito_de_la_base_esta_documentado_en_el_plan(
    plan: list[dict[str, str]], indices_de_la_base: set[str]
) -> None:
    documentados = {f["indice"] for f in plan}
    sin_documentar = sorted(indices_de_la_base - documentados)
    assert not sin_documentar, (
        "Índices que existen en la base y no están en docs/plan-indices.md "
        f"(añádelos a la tabla de la sección 4): {sin_documentar}"
    )


def test_el_plan_no_cita_indices_que_ya_no_existen(
    plan: list[dict[str, str]], indices_de_la_base: set[str]
) -> None:
    obsoletos = sorted({f["indice"] for f in plan} - indices_de_la_base)
    assert not obsoletos, (
        f"El plan cita índices que no existen en la base (quítalos de la tabla): {obsoletos}"
    )


def test_el_plan_no_repite_indices(plan: list[dict[str, str]]) -> None:
    nombres = [f["indice"] for f in plan]
    repetidos = sorted({n for n in nombres if nombres.count(n) > 1})
    assert plan, "no se encontró ninguna fila de índices en el plan"
    assert not repetidos, f"índices repetidos en el plan: {repetidos}"


def test_cada_fila_tiene_categoria_justificacion_y_estado_validos(
    plan: list[dict[str, str]],
) -> None:
    invalidas = []
    for f in plan:
        if f["categoria"] not in CATEGORIAS:
            invalidas.append((f["indice"], f"categoría desconocida: {f['categoria']}"))
        if f["estado"] not in ESTADOS:
            invalidas.append((f["indice"], f"estado desconocido: {f['estado']}"))
        if len(f["justificacion"]) < 15:
            invalidas.append((f["indice"], "justificación vacía o demasiado corta"))
    assert not invalidas, invalidas


def test_los_indices_de_categoria_consulta_citan_consultas_del_inventario(
    plan: list[dict[str, str]], ids_del_inventario: set[str]
) -> None:
    assert ids_del_inventario, "no se encontró el inventario provisional (Anexo A)"
    problemas = []
    for f in plan:
        if f["categoria"] != "consulta" or f["estado"] != "activo":
            continue
        citadas = set(_ID_CONSULTA.findall(f["consultas"]))
        if not citadas:
            problemas.append((f["indice"], "no cita ninguna consulta"))
        elif citadas - ids_del_inventario:
            problemas.append(
                (
                    f["indice"],
                    f"cita consultas inexistentes: {sorted(citadas - ids_del_inventario)}",
                )
            )
    assert not problemas, problemas


def test_los_candidatos_a_retiro_tienen_su_justificacion(plan: list[dict[str, str]]) -> None:
    candidatos = [f for f in plan if f["estado"] == "candidato_a_retiro"]
    assert candidatos, "el plan no marca ningún candidato a retiro"
    cortos = [f["indice"] for f in candidatos if len(f["justificacion"]) < 30]
    assert not cortos, f"candidatos a retiro sin una justificación suficiente: {cortos}"
    # Un candidato no puede apoyarse en una consulta: si la tuviera, no sería candidato.
    con_consulta = [f["indice"] for f in candidatos if _ID_CONSULTA.search(f["consultas"])]
    assert not con_consulta, f"candidatos a retiro que citan consultas: {con_consulta}"


def test_el_inventario_provisional_tiene_identificadores_unicos(
    ids_del_inventario: set[str],
) -> None:
    filas = _filas(_seccion(PLAN.read_text(encoding="utf-8"), "inventario"))
    ids = [f[0] for f in filas if _ID_CONSULTA.fullmatch(f[0])]
    assert len(ids) == len(set(ids)) > 0, "identificadores de consulta repetidos o ausentes"
    assert ids_del_inventario == set(ids)
