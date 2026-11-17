"""Cliente psycopg de la capa de incidentes sin base de datos (KC-CM-09-2).

Traducción de parámetros, transacciones, idempotencia del embedding y armado del incidente a
partir del expediente. La semántica real se prueba contra PostgreSQL en tests/test_incidentes.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from kallicode_codemapping import cm_queries, incidentes
from kallicode_core import cm_incidentes
from kallicode_core.cm_incidentes import ConsultaIncidentesIn, RegistroIncidenteIn

_INVERSO = {sql: nombre for nombre, sql in cm_queries.CONSULTAS_INCIDENTES.items()}
_FILA_INCIDENTE = {
    "id": "inc_1",
    "ticket": "KC-0001",
    "origen": "ticket",
    "resumen": "IVA duplicado",
    "causa": "AC-1: total incorrecto",
    "correccion": "https://pr/1",
    "creado_en": datetime(2026, 11, 1, tzinfo=UTC),
    "distancia": 1,
    "similitud": 0.9,
}


class _Cursor:
    def __init__(self, loader: _Loader) -> None:
        self.loader = loader
        self.description: list[Any] = []
        self._filas: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        nombre = _INVERSO.get(sql, "otra")
        self.loader.ejecutadas.append((nombre, params))
        filas = self.loader.respuestas.get(nombre, [])
        columnas = list(filas[0]) if filas else []
        self.description = [SimpleNamespace(name=c) for c in columnas]
        self._filas = [tuple(f.values()) for f in filas]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._filas


class _Loader:
    def __init__(self, **respuestas: list[dict[str, Any]]) -> None:
        self.respuestas = respuestas
        self.ejecutadas: list[tuple[str, Any]] = []
        self.fases: list[str] = []

    @contextmanager
    def fase(self, nombre: str) -> Iterator[_Cursor]:
        self.fases.append(nombre)
        yield _Cursor(self)

    def nombres(self) -> list[str]:
        return [n for n, _ in self.ejecutadas]

    def params(self, nombre: str) -> Any:
        return next(p for n, p in self.ejecutadas if n == nombre)


class _Embebedor:
    def __init__(self, vectores: int = 1) -> None:
        self.llamadas: list[list[str]] = []
        self.vectores = vectores

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.llamadas.append(texts)
        return [[0.5] * cm_incidentes.DIMENSION_EMBEDDING for _ in range(self.vectores)]


def _registro(**kw: Any) -> RegistroIncidenteIn:
    base: dict[str, Any] = {
        "origen": "ticket",
        "ticket_id": "tk_1",
        "resumen": "IVA duplicado",
        "nodos": ["dbt_facturas"],
        "ficheros": ["src/billing/invoice.py"],
    }
    return RegistroIncidenteIn(**{**base, **kw})


def test_consultas_psycopg_derivadas_de_core() -> None:
    assert set(cm_queries.CONSULTAS_INCIDENTES) == set(cm_incidentes.CONSULTAS)
    for nombre, psy in cm_queries.CONSULTAS_INCIDENTES.items():
        assert re.sub(r"%\((\w+)\)s", r":\1", psy) == cm_incidentes.CONSULTAS[nombre], nombre
        assert "%" not in re.sub(r"%\(\w+\)s", "", psy), nombre


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def test_registro_enlaza_los_nodos_existentes_y_embebe() -> None:
    loader = _Loader(
        nodos_afectados=[{"id": "dbt_facturas", "kind": "db_table"}, {"id": "f1", "kind": "file"}],
        upsert_incidente=[{"id": "inc_1", "creado": True}],
    )
    emb = _Embebedor()
    r = incidentes.registrar_incidente(loader, _registro(causa="total mal"), embebedor=emb)

    assert (r.id, r.creado, r.embebido) == ("inc_1", True, True)
    assert r.nodos_enlazados == ["dbt_facturas", "f1"]
    assert loader.fases == ["incidentes", "incidentes_embedding"]
    assert loader.nombres() == [
        "nodos_afectados",
        "upsert_incidente",
        "borrar_enlaces",
        "insertar_enlaces",
        "hash_embedding",
        "upsert_embedding",
    ]
    upsert = loader.params("upsert_incidente")
    assert upsert["id"].startswith("inc_") and upsert["nodos_afectados"] == '["dbt_facturas", "f1"]'
    assert loader.params("insertar_enlaces") == {"id": "inc_1", "nodos": ["dbt_facturas", "f1"]}
    assert emb.llamadas == [["IVA duplicado total mal"]]
    emb_params = loader.params("upsert_embedding")
    assert emb_params["texto_hash"] == cm_incidentes.hash_texto("IVA duplicado total mal", "bge-m3")
    assert emb_params["embedding"].startswith("[0.5,")


def test_registro_repetido_no_re_embebe_si_el_texto_no_cambio() -> None:
    registro = _registro()
    huella = cm_incidentes.hash_texto(registro.texto_embedding(), "bge-m3")
    loader = _Loader(
        upsert_incidente=[{"id": "inc_1", "creado": False}],
        hash_embedding=[{"texto_hash": huella}],
    )
    emb = _Embebedor()
    r = incidentes.registrar_incidente(loader, registro, embebedor=emb)
    assert (r.creado, r.embebido, r.nodos_enlazados) == (False, False, [])
    assert emb.llamadas == [] and loader.fases == ["incidentes"]


def test_registro_sin_embebedor_y_embebedor_defectuoso() -> None:
    loader = _Loader(upsert_incidente=[{"id": "inc_1", "creado": True}])
    assert not incidentes.registrar_incidente(loader, _registro()).embebido
    with pytest.raises(ValueError, match="2 vectores"):
        incidentes.registrar_incidente(loader, _registro(), embebedor=_Embebedor(vectores=2))


@pytest.mark.parametrize(
    ("texto", "maximo", "esperado"),
    [
        (None, 10, None),
        ("  \n ", 10, None),
        ("corto", 10, "corto"),
        ("una frase bastante larga", 12, "una frase…"),
        ("sinespacioslargo", 8, "sinespa…"),
    ],
)
def test_recortar(texto: str | None, maximo: int, esperado: str | None) -> None:
    r = incidentes.recortar(texto, maximo)
    assert r == esperado
    assert r is None or len(r) <= maximo


# ---------------------------------------------------------------------------
# Expediente
# ---------------------------------------------------------------------------
def _ticket(**kw: Any) -> dict[str, Any]:
    base = {
        "id": "tk_1",
        "numero": "KC-0007",
        "tipo": "bug",
        "titulo": "La factura\nduplica el IVA",
        "impacto": {"modulos": ["src/billing/"], "tablas": ["Facturas", "public.lineas"]},
        "pr_url": "https://git/pr/7",
    }
    return {**base, **kw}


def test_registro_desde_expediente_arma_causa_correccion_y_nodos() -> None:
    loader = _Loader(
        ticket_expediente=[_ticket()],
        fallos_qa=[
            {"criterio_id": "AC-1", "extracto_fallo": "total\n= 121"},
            {"criterio_id": "AC-2", "extracto_fallo": None},
        ],
        ficheros_de_modulos=[{"path": "src/billing/invoice.py"}],
        tablas_por_nombre=[{"id": "dbt_facturas"}],
    )
    r = incidentes.registro_desde_expediente(loader, "tk_1", nodos=["sym_x"])

    assert (r.origen, r.ticket_id, r.evidencia_ref) == ("ticket", "tk_1", "ticket:KC-0007")
    assert r.resumen == "La factura duplica el IVA"
    assert r.causa == "AC-1: total = 121"
    assert r.correccion == "https://git/pr/7"
    assert r.nodos == ["dbt_facturas", "sym_x"]
    assert r.ficheros == ["src/billing/invoice.py"]
    assert loader.params("ficheros_de_modulos")["patrones"] == ["src/billing/%"]
    assert loader.params("tablas_por_nombre") == {"tablas": ["facturas", "public.lineas"]}
    assert loader.fases == ["lectura"]


def test_expediente_sin_impacto_ni_fallos_y_correccion_explicita() -> None:
    loader = _Loader(ticket_expediente=[_ticket(impacto=None, pr_url=None, numero=None)])
    r = incidentes.registro_desde_expediente(loader, "tk_1", correccion="Se valida el IVA")
    assert (r.causa, r.correccion, r.nodos, r.ficheros) == (None, "Se valida el IVA", [], [])
    assert r.evidencia_ref == "ticket:tk_1"
    assert loader.nombres() == ["ticket_expediente", "fallos_qa"]


def test_expediente_de_ticket_inexistente() -> None:
    with pytest.raises(LookupError, match="tk_x"):
        incidentes.registro_desde_expediente(_Loader(), "tk_x")


def test_registrar_desde_expediente_encadena_lectura_y_registro() -> None:
    loader = _Loader(
        ticket_expediente=[_ticket(impacto={})],
        upsert_incidente=[{"id": "inc_9", "creado": True}],
    )
    r = incidentes.registrar_desde_expediente(loader, "tk_1")
    assert r.id == "inc_9" and loader.fases == ["lectura", "incidentes"]
    assert loader.params("upsert_incidente")["ticket_id"] == "tk_1"


# ---------------------------------------------------------------------------
# Resolutor de {{incidents}}
# ---------------------------------------------------------------------------
def test_resolver_sin_area_no_abre_transaccion() -> None:
    loader = _Loader()
    r = cm_queries.resolver_incidentes(loader, ConsultaIncidentesIn())
    assert r.texto == "" and loader.fases == []


def test_contexto_incidentes_embebe_el_texto_y_rellena_la_variable() -> None:
    loader = _Loader(incidentes_area=[_FILA_INCIDENTE])
    emb = _Embebedor()
    v = cm_queries.contexto_incidentes(
        loader, ficheros=["src/billing/invoice.py"], texto="IVA", embebedor=emb
    )
    assert v == {
        "incidents": "- [KC-0001] IVA duplicado — cause: AC-1: total incorrecto — fix: https://pr/1"
    }
    assert emb.llamadas == [["IVA"]]
    params = loader.params("incidentes_area")
    assert params["embedding"].startswith("[0.5,") and params["ficheros"] == [
        "src/billing/invoice.py"
    ]
    assert loader.nombres()[0] == "otra"  # statement_timeout antes de la consulta


def test_contexto_incidentes_vacio_y_sin_embebedor() -> None:
    loader = _Loader()
    assert cm_queries.contexto_incidentes(loader, nodos=["a"], texto="x") == {"incidents": ""}
    assert loader.params("incidentes_area")["embedding"] is None
    emb = _Embebedor()
    assert cm_queries.contexto_incidentes(_Loader(), texto="x", embebedor=emb) == {"incidents": ""}
    assert emb.llamadas == []  # sin área no se embebe ni se consulta
    with pytest.raises(ValueError, match="2 vectores"):
        cm_queries.contexto_incidentes(
            _Loader(), nodos=["a"], texto="x", embebedor=_Embebedor(vectores=2)
        )
