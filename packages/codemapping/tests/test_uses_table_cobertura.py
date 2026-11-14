"""Cobertura de USES_TABLE: SQL crudo con CTE/alias/esquema y detectores por ORM (KC-CM-09-3).

Cierra CM-F17: find_table_usage buscaba SQL con una regex de ventana fija, sin CTE, alias ni
esquema, y era ciega a los ORM. Sin base de datos: el esquema del cliente es el de
``fixtures/orm_ref/esperado.json``, igual que lo devolvería extract_schema.

Criterios:
1. Fixture de 60 accesos (30 SQL + 30 ORM): de la línea base a >= 85 %, con recuento explícito.
2. Ninguna tabla no confirmada por el esquema entra al grafo.
3. Alias y nombres cualificados por esquema resuelven a la tabla correcta (CTE, alias encadenados).
4. La cobertura se publica en las stats (ver también test/test_contrato_stats_ingesta.py).
5. Regresión de CM-F17 para Prisma, TypeORM y SQLAlchemy.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from kallicode_codemapping.detectores_orm import analizar_archivo, lower_camel, snake_case
from kallicode_codemapping.extract_sql import (
    IndiceTablas,
    TablaRef,
    detectar_uso_tablas,
    extraer_tablas_sql,
    find_table_usage,
)
from kallicode_codemapping.graph_loader import _id_tabla, clave_tabla
from kallicode_codemapping.ingest_repo import LANG_BY_EXT, SourceFile

FIXTURE = Path(__file__).parent / "fixtures" / "orm_ref"
UMBRAL = 0.85
ORMS = ("prisma", "typeorm", "sqlalchemy")


@cache
def _esperado() -> dict[str, Any]:
    datos: dict[str, Any] = json.loads((FIXTURE / "esperado.json").read_text(encoding="utf-8"))
    return datos


def _tablas() -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "esquema": t["esquema"], "columns": []} for t in _esperado()["tablas"]
    ]


def _fuentes() -> list[SourceFile]:
    return [
        SourceFile(
            path=p.relative_to(FIXTURE).as_posix(),
            language=LANG_BY_EXT[p.suffix],
            sha256="fixture",
            content=p.read_text(encoding="utf-8"),
        )
        for p in sorted(FIXTURE.rglob("*"))
        if p.suffix in LANG_BY_EXT
    ]


def _accesos(via: str | None = None) -> set[tuple[str, str]]:
    return {
        (a["file"], a["tabla"]) for a in _esperado()["accesos"] if via is None or a["via"] == via
    }


def _aristas(usage: list[dict[str, str]]) -> set[tuple[str, str]]:
    return {(u["file"], f"{u['esquema']}.{u['table']}") for u in usage}


def _sf(path: str, content: str) -> SourceFile:
    return SourceFile(
        path=path, language=LANG_BY_EXT[Path(path).suffix], sha256="h", content=content
    )


# Copia congelada del detector anterior a KC-CM-09-3 (extract_sql.py:107-123 en develop 78d51ca):
# la línea base de CM-F17. Solo se usa para medir; no tiene esquema, así que se le concede el
# acierto si el nombre de la tabla coincide.
_SQL_STMT_V0 = re.compile(r"(?is)\b(select|insert\s+into|update|delete\s+from)\b[^;\"']{0,400}")
_TABLE_REF_V0 = re.compile(r"(?i)\b(?:from|join|into|update)\s+[`\"]?(\w+)[`\"]?")


def _linea_base(files: list[SourceFile], tablas: list[dict[str, Any]]) -> set[tuple[str, str]]:
    conocidas = {str(t["name"]).lower() for t in tablas}
    encontrados: set[tuple[str, str]] = set()
    for sf in files:
        for stmt in _SQL_STMT_V0.finditer(sf.content):
            for ref in _TABLE_REF_V0.finditer(stmt.group(0)):
                if ref.group(1).lower() in conocidas:
                    encontrados.add((sf.path, ref.group(1).lower()))
    return encontrados


def _aciertos_linea_base(esperados: set[tuple[str, str]]) -> set[tuple[str, str]]:
    base = _linea_base(_fuentes(), _tablas())
    return {(f, t) for f, t in esperados if (f, t.split(".", 1)[1]) in base}


# --------------------------------------------------------------------------
# Criterio 1 y 5: cobertura sobre el repositorio de referencia multilingüe
# --------------------------------------------------------------------------
def test_el_fixture_tiene_sesenta_accesos_treinta_sql_y_treinta_orm() -> None:
    assert len(_accesos()) == 60
    assert len(_accesos("sql")) == 30
    assert {via: len(_accesos(via)) for via in ORMS} == {
        "prisma": 10,
        "typeorm": 10,
        "sqlalchemy": 10,
    }


def test_la_cobertura_sube_de_la_linea_base_a_mas_del_85_por_ciento() -> None:
    esperados = _accesos()
    detectados = _aristas(detectar_uso_tablas(_fuentes(), _tablas()).usage) & esperados
    base = _aciertos_linea_base(esperados)

    # Recuento explícito: línea base 9/60 (15 %) -> 59/60 (98,3 %).
    assert len(base) == 9
    assert len(base) / len(esperados) <= 0.45
    assert len(detectados) == 59
    assert len(detectados) / len(esperados) >= UMBRAL
    # El único acceso no detectado es el nombre de tabla dinámico (f-string), a propósito.
    assert esperados - detectados == {("python/informes_facturacion.py", "public.events")}


def test_recuento_por_via_sql_y_orm() -> None:
    detectados = _aristas(detectar_uso_tablas(_fuentes(), _tablas()).usage)
    recuento = {via: len(_accesos(via) & detectados) for via in ("sql", *ORMS)}
    assert recuento == {"sql": 29, "prisma": 10, "typeorm": 10, "sqlalchemy": 10}


@pytest.mark.parametrize("orm", ORMS)
def test_regresion_cm_f17_el_orm_ya_no_es_invisible(orm: str) -> None:
    """CM-F17: la línea base no veía ningún acceso por ORM; ahora se detectan los diez."""
    esperados = _accesos(orm)
    detectados = _aristas(detectar_uso_tablas(_fuentes(), _tablas()).usage)
    assert _aciertos_linea_base(esperados) == set()
    assert esperados <= detectados


def test_sin_falsos_positivos_en_el_fixture() -> None:
    aristas = _aristas(detectar_uso_tablas(_fuentes(), _tablas()).usage)
    assert aristas - _accesos() == set()
    # La línea base sí tenía dos: la CTE "products" y "customers" ambigua de trampas.sql.
    assert {f for f, _t in _linea_base(_fuentes(), _tablas())} >= {"sql/trampas.sql"}


def test_las_stats_publican_la_cobertura_por_detector() -> None:
    stats = detectar_uso_tablas(_fuentes(), _tablas()).stats()
    assert stats["aristas"] == 59
    assert stats["por_detector"] == {
        # SQL: 29 aciertos + tabla_que_no_existe + customers ambigua (generate_series y la
        # CTE no cuentan como accesos). ORM: 10 + modelo trampa declarado y usado.
        "sql": {"detectados": 31, "resueltos": 29},
        "prisma": {"detectados": 12, "resueltos": 10},
        "typeorm": {"detectados": 12, "resueltos": 10},
        "sqlalchemy": {"detectados": 12, "resueltos": 10},
    }
    assert (stats["accesos_detectados"], stats["accesos_resueltos"]) == (67, 59)
    assert stats["cobertura"] == round(59 / 67, 4)


def test_find_table_usage_conserva_su_firma_y_devuelve_las_mismas_aristas() -> None:
    assert (
        find_table_usage(_fuentes(), _tablas()) == detectar_uso_tablas(_fuentes(), _tablas()).usage
    )


# --------------------------------------------------------------------------
# Criterio 2: ninguna tabla no confirmada por el esquema entra al grafo
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("path", "codigo"),
    [
        ("a.sql", "SELECT * FROM tabla_inventada JOIN otra_inventada USING (id);"),
        ("a.py", 'db.execute("SELECT * FROM tabla_inventada")'),
        ("m.py", 'class X(Base):\n    __tablename__ = "tabla_inventada"\n'),
        ("s.prisma", 'model Fantasma {\n  id Int @id\n  @@map("tabla_inventada")\n}\n'),
        ("e.ts", '@Entity("tabla_inventada")\nexport class Fantasma {}\n'),
        ("E.java", '@Entity @Table(name = "tabla_inventada")\npublic class Fantasma {}\n'),
    ],
)
def test_una_coincidencia_inventada_no_entra_al_grafo(path: str, codigo: str) -> None:
    resultado = detectar_uso_tablas([_sf(path, codigo)], [{"name": "users", "esquema": "public"}])
    assert resultado.usage == []
    stats = resultado.stats()
    assert stats["accesos_detectados"] >= 1
    assert stats["accesos_resueltos"] == 0
    assert stats["cobertura"] == 0.0


def test_el_esquema_equivocado_tampoco_entra() -> None:
    resultado = detectar_uso_tablas(
        [_sf("a.sql", "SELECT * FROM billing.users")], [{"name": "users", "esquema": "public"}]
    )
    assert resultado.usage == []


def test_sin_tablas_conocidas_no_hay_aristas_ni_cobertura() -> None:
    resultado = detectar_uso_tablas([_sf("a.py", "x = 1")], [])
    assert resultado.usage == []
    assert resultado.stats()["cobertura"] is None


# --------------------------------------------------------------------------
# Criterio 3: alias, CTE y nombres cualificados por esquema
# --------------------------------------------------------------------------
def test_cte_encadenadas_con_alias_resuelven_a_las_tablas_reales() -> None:
    sql = """
        WITH a AS (SELECT * FROM billing.invoices i),
             b (id, total) AS MATERIALIZED (SELECT x.id, x.total FROM a x JOIN app.customers c ON c.id = x.id)
        UPDATE c SET saldo = b.total
          FROM app.customers c
          JOIN b ON b.id = c.id
    """
    assert set(extraer_tablas_sql(sql)) == {
        TablaRef("billing", "invoices"),
        TablaRef("app", "customers"),
    }


def test_una_cte_sombrea_a_la_tabla_del_mismo_nombre_solo_en_su_sentencia() -> None:
    sql = "WITH users AS (SELECT 1) SELECT * FROM users; SELECT * FROM users; SELECT * FROM public.users"
    assert extraer_tablas_sql(sql) == [TablaRef(None, "users"), TablaRef("public", "users")]


def test_destinos_de_update_y_delete_que_son_alias() -> None:
    assert set(extraer_tablas_sql("DELETE o FROM orders o JOIN users u ON u.id = o.user_id")) == {
        TablaRef(None, "orders"),
        TablaRef(None, "users"),
    }
    assert extraer_tablas_sql("UPDATE orders SET a = 1") == [TablaRef(None, "orders")]
    assert extraer_tablas_sql("UPDATE app.orders AS o SET a = 1") == [TablaRef("app", "orders")]


@pytest.mark.parametrize(
    ("sql", "esperado"),
    [
        ('SELECT * FROM "Billing"."Invoices"', [TablaRef("billing", "invoices")]),
        ("SELECT * FROM `shop`.`orders`", [TablaRef("shop", "orders")]),
        ("SELECT * FROM [dbo].[orders]", [TablaRef("dbo", "orders")]),
        ("SELECT * FROM erp.billing.invoices", [TablaRef("billing", "invoices")]),
        (
            "SELECT * FROM a, b.c AS x, (SELECT 1) s, d",
            [TablaRef(None, "a"), TablaRef("b", "c"), TablaRef(None, "d")],
        ),
        (
            "INSERT INTO billing.refunds (id) SELECT id FROM billing.payments",
            [TablaRef("billing", "refunds"), TablaRef("billing", "payments")],
        ),
        ("TRUNCATE TABLE ONLY public.sessions", [TablaRef("public", "sessions")]),
        (
            "MERGE INTO stock s USING movimientos m ON s.id = m.id",
            [TablaRef(None, "stock"), TablaRef(None, "movimientos")],
        ),
        ("SELECT * FROM a JOIN b USING (id)", [TablaRef(None, "a"), TablaRef(None, "b")]),
        ("SELECT * FROM ONLY t, LATERAL unnest(t.xs) u", [TablaRef(None, "t")]),
        (
            "SELECT * FROM t WHERE x IN (SELECT y FROM u)",
            [TablaRef(None, "t"), TablaRef(None, "u")],
        ),
    ],
)
def test_nombres_cualificados_citados_y_listas(sql: str, esperado: list[TablaRef]) -> None:
    assert extraer_tablas_sql(sql) == esperado


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT extract(year FROM created_at) FROM generate_series(1, 3) g",
        "SELECT trim(both ' ' FROM name), substring(x FROM 2)",
        "SELECT a IS DISTINCT FROM b",
        "SELECT * FROM ?",
        "SELECT 1 FOR UPDATE",
        "INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = 2 RETURNING a",
        "SELECT 'FROM users' AS texto -- FROM users\n /* JOIN users */",
        "SELECT x INTO variable FROM",
    ],
)
def test_lo_que_no_es_una_tabla_no_se_reporta(sql: str) -> None:
    assert [
        r for r in extraer_tablas_sql(sql) if r.nombre in {"users", "variable", "generate_series"}
    ] == []
    if "INSERT" in sql:
        assert extraer_tablas_sql(sql) == [TablaRef(None, "t")]


def test_resolucion_por_esquema_con_nombres_repetidos() -> None:
    indice = IndiceTablas(
        [
            {"name": "events", "esquema": "public"},
            {"name": "Events", "esquema": "audit"},
            {"name": "customers", "esquema": "app"},
            {"name": "customers", "esquema": "billing"},
            {"name": "legacy"},  # checkpoints antiguos sin esquema: public
        ]
    )
    assert indice.resolver("AUDIT", "EVENTS") == ("audit", "events")
    assert indice.resolver(None, "events") == ("public", "events")  # search_path por defecto
    assert indice.resolver(None, "customers") is None  # ambigua y ninguna en public
    assert indice.resolver("billing", "customers") == ("billing", "customers")
    assert indice.resolver(None, "legacy") == ("public", "legacy")
    assert indice.resolver("app", "events") is None


# --------------------------------------------------------------------------
# Extracción de SQL de los literales (AST)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("path", "codigo", "tablas"),
    [
        ("a.py", 'q = ("SELECT * "\n     "FROM users u")', {"users"}),
        ("a.py", 'q = "SELECT * FROM " + tabla + " JOIN orders o ON 1=1"', {"orders"}),
        ("a.py", 'q = f"SELECT * FROM orders WHERE id = {pid}"', {"orders"}),
        ("a.py", 'q = base + " FROM orders"', {"orders"}),
        ("a.py", 'q = "SELECT *\\nFROM\\torders"', {"orders"}),
        ("a.ts", "const q = `SELECT * FROM orders WHERE a = ${b}`;", {"orders"}),
        ("a.ts", 'run(db.query(`SELECT * FROM users`) + " " + "x");', {"users"}),
        ("a.js", "const q = 'SELECT * ' + 'FROM orders';", {"orders"}),
        ("A.java", 'class A { String q = """\n  SELECT * FROM orders\n  """; }', {"orders"}),
        (
            "A.java",
            'class A { String q = "SELECT * FROM orders o " + "JOIN users u ON 1=1"; }',
            {"orders", "users"},
        ),
        ("a.py", 'msg = "Selecciona un archivo de la lista"', set()),
    ],
)
def test_literales_sql_en_cada_lenguaje(path: str, codigo: str, tablas: set[str]) -> None:
    h = analizar_archivo(path, LANG_BY_EXT[Path(path).suffix], codigo)
    assert {r.nombre for texto in h.sql for r in extraer_tablas_sql(texto)} == tablas


def test_lenguaje_sin_soporte_no_produce_hallazgos() -> None:
    h = analizar_archivo("a.rb", "ruby", 'User.find_by_sql("SELECT * FROM users")')
    assert (h.sql, h.modelos, h.usos) == ([], [], [])


# --------------------------------------------------------------------------
# Detectores por ORM
# --------------------------------------------------------------------------
_TABLAS = [
    {"name": "users", "esquema": "public"},
    {"name": "user_profile", "esquema": "public"},
    {"name": "invoices", "esquema": "billing"},
    {"name": "tax_rate", "esquema": "public"},
    {"name": "post", "esquema": "public"},
]


def _usos(*fuentes: tuple[str, str]) -> set[tuple[str, str]]:
    return _aristas(detectar_uso_tablas([_sf(p, c) for p, c in fuentes], _TABLAS).usage)


def test_prisma_resuelve_el_modelo_por_la_propiedad_del_cliente() -> None:
    schema = 'model UserProfile {\n  id Int @id\n  @@map("user_profile")\n}\n// model Users {}\n'
    uso = "await prisma.userProfile.findMany();\nawait cache.users.findMany();"
    assert _usos(("db/schema.prisma", schema), ("src/a.ts", uso)) == {
        ("db/schema.prisma", "public.user_profile"),
        ("src/a.ts", "public.user_profile"),
    }


def test_typeorm_nombre_por_defecto_y_entidad_sin_importar() -> None:
    entidad = "import { Entity } from 'typeorm';\n@Entity()\nexport class UserProfile {}\n"
    con_import = "import { UserProfile } from './e';\nds.getRepository(UserProfile);"
    sin_import = "ds.getRepository(UserProfile);"  # otro UserProfile: no se asume
    assert _usos(("e.ts", entidad), ("a.ts", con_import), ("b.ts", sin_import)) == {
        ("e.ts", "public.user_profile"),
        ("a.ts", "public.user_profile"),
    }


def test_sqlalchemy_flask_y_modelo_abstracto() -> None:
    modelos = (
        "class Post(db.Model):\n    id = db.Column(db.Integer)\n\n"
        "class Base(db.Model):\n    __abstract__ = True\n"
    )
    uso = "from .m import Post\nPost.query.all()\n"
    assert _usos(("m.py", modelos), ("v.py", uso)) == {
        ("m.py", "public.post"),
        ("v.py", "public.post"),
    }


def test_sqlalchemy_table_dentro_de_la_clase() -> None:
    modelo = 'class Invoice(Base):\n    __table__ = Table("invoices", md, schema="billing")\n'
    assert _usos(("m.py", modelo)) == {("m.py", "billing.invoices")}


def test_jpa_entidad_repositorio_class_literal_y_jpql() -> None:
    entidad = (
        '@Entity\n@Table(name = "invoices", schema = "billing")\npublic class Invoice {}\n'
        "@Entity public class TaxRate {}\n"
    )
    repo = "interface InvoiceRepo extends JpaRepository<Invoice, Long> {}"
    servicio = "class S { void f() { em.find(TaxRate.class, 1); } }"
    jpql = 'class Q { String q = "SELECT i FROM Invoice i WHERE i.total > 0"; }'
    assert _usos(
        ("Invoice.java", entidad),
        ("InvoiceRepo.java", repo),
        ("S.java", servicio),
        ("Q.java", jpql),
    ) == {
        ("Invoice.java", "billing.invoices"),
        ("Invoice.java", "public.tax_rate"),
        ("InvoiceRepo.java", "billing.invoices"),
        ("S.java", "public.tax_rate"),
        ("Q.java", "billing.invoices"),
    }


def test_modelo_con_el_mismo_nombre_en_dos_tablas_es_ambiguo() -> None:
    a = 'class User(Base):\n    __tablename__ = "users"\n'
    b = 'class User(Base):\n    __tablename__ = "user_profile"\n'
    uso = "from x import User\nselect(User)\n"
    assert ("v.py", "public.users") not in _usos(("a.py", a), ("b.py", b), ("v.py", uso))


@pytest.mark.parametrize(
    ("nombre", "snake", "camel"),
    [
        ("UserProfile", "user_profile", "userProfile"),
        ("HTTPLog", "http_log", "hTTPLog"),
        ("A", "a", "a"),
    ],
)
def test_convenciones_de_nombres(nombre: str, snake: str, camel: str) -> None:
    assert (snake_case(nombre), lower_camel(nombre)) == (snake, camel)


# --------------------------------------------------------------------------
# Loader: claves esquema.tabla (sin colisiones entre esquemas)
# --------------------------------------------------------------------------
def test_ids_de_tabla_por_esquema_y_compatibilidad_sin_esquema() -> None:
    ids = {
        clave_tabla("app", "Customers"): "id-app",
        clave_tabla("billing", "customers"): "id-billing",
        clave_tabla(None, "users"): "id-users",
    }
    assert _id_tabla(ids, "APP", "customers") == "id-app"
    assert _id_tabla(ids, "billing", "customers") == "id-billing"
    assert _id_tabla(ids, None, "users") == "id-users"  # uso antiguo sin esquema, única
    assert _id_tabla(ids, None, "customers") is None  # ambigua
    assert _id_tabla(ids, "public", "customers") is None
