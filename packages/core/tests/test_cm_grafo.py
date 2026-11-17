"""Prueba de contrato de cm-grafo-v1, la API de consulta del grafo (KC-CM-09-4).

Falla si:
- el contrato publicado (contratos/cm-grafo-v1.json) no es el que generan los modelos;
- el contrato cambia sin publicar una versión nueva en contratos/historial/cm-grafo-v1/, o una
  versión publicada se modifica;
- entre dos versiones publicadas hay un cambio incompatible (en entrada o salida, o en el catálogo
  de operaciones) que no está anunciado en contratos/cambios-anunciados.yaml;
- algún objeto de entrada o salida admite propiedades adicionales.

Si falla tras un cambio de modelos: `python -m kallicode_core.cm_grafo` (sube antes
cm_grafo.VERSION según la política del contrato) y, si es incompatible, anúncialo.
"""

from __future__ import annotations

import copy
import inspect
import itertools
import json
import typing
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from kallicode_core import cm_grafo
from kallicode_core.cm_grafo import (
    OPERACIONES,
    ConsultaGrafo,
    cambios_incompatibles,
    generar_contrato,
)
from kallicode_core.cm_grafo_doble import ConsultaGrafoDoble, GrafoEnMemoria
from kallicode_core.contratos_compat import comparar_esquemas, sin_propiedades_adicionales

CONTRATO = generar_contrato()


# ---------------------------------------------------------------------------
# Congelado y publicado
# ---------------------------------------------------------------------------
def test_el_contrato_publicado_es_el_que_generan_los_modelos() -> None:
    publicado = json.loads(cm_grafo.RUTA_CONTRATO.read_text(encoding="utf-8"))
    assert publicado == CONTRATO, (
        "cm-grafo-v1 cambió: sube cm_grafo.VERSION según x-politica y ejecuta "
        "`python -m kallicode_core.cm_grafo`; si el cambio es incompatible, anúncialo en "
        "contratos/cambios-anunciados.yaml"
    )
    assert cm_grafo.RUTA_CONTRATO.read_text(encoding="utf-8") == cm_grafo.serializar_contrato()


def test_la_version_vigente_esta_publicada_e_inmutable() -> None:
    publicadas = dict(cm_grafo.versiones_publicadas())
    assert cm_grafo.VERSION in publicadas, (
        f"x-version {cm_grafo.VERSION} sin publicar en {cm_grafo.RUTA_HISTORIAL}"
    )
    assert publicadas[cm_grafo.VERSION] == CONTRATO, (
        f"la versión publicada {cm_grafo.VERSION} no se puede modificar: sube x-version"
    )
    ultima = cm_grafo.versiones_publicadas()[-1][0]
    assert ultima == cm_grafo.VERSION, "contratos/cm-grafo-v1.json debe ser la última publicada"


def _anunciadas() -> set[str]:
    anuncios = yaml.safe_load(cm_grafo.RUTA_ANUNCIOS.read_text(encoding="utf-8"))
    return {
        str(c.get("contrato_version"))
        for c in anuncios.get("changes", [])
        if cm_grafo.NOMBRE_CONTRATO in (c.get("surfaces") or []) and c.get("breaking") is True
    }


def _rupturas_sin_anunciar(
    publicadas: list[tuple[str, dict[str, Any]]], anunciadas: set[str]
) -> list[str]:
    errores: list[str] = []
    for (v_base, base), (v_nueva, nueva) in itertools.pairwise(publicadas):
        if nueva["x-version"] != v_nueva or base["x-version"] != v_base:
            errores.append(f"{v_nueva}: el archivo no coincide con su x-version")
        rupturas = cambios_incompatibles(base, nueva)
        if rupturas and v_nueva not in anunciadas:
            errores.append(
                f"{v_base} -> {v_nueva} rompe el contrato sin anunciarlo "
                f"(breaking: true, contrato_version: {v_nueva}): {rupturas}"
            )
    return errores


def test_entre_versiones_publicadas_no_hay_rupturas_sin_anunciar() -> None:
    assert _rupturas_sin_anunciar(cm_grafo.versiones_publicadas(), _anunciadas()) == []


def test_una_version_incompatible_solo_pasa_si_esta_anunciada() -> None:
    nueva = copy.deepcopy(CONTRATO)
    nueva["x-version"] = "1.1.0"
    nueva["operaciones"].pop("vecinos")
    publicadas = [("1.0.0", CONTRATO), ("1.1.0", nueva)]
    errores = _rupturas_sin_anunciar(publicadas, set())
    assert len(errores) == 1 and "vecinos: operación retirada" in errores[0]
    assert _rupturas_sin_anunciar(publicadas, {"1.1.0"}) == []
    assert _rupturas_sin_anunciar([("1.0.0", CONTRATO), ("1.2.0", nueva)], {"1.2.0"}) == [
        "1.2.0: el archivo no coincide con su x-version"
    ]


def test_el_contrato_esta_anunciado_a_backend_y_testing() -> None:
    anuncios = yaml.safe_load(cm_grafo.RUTA_ANUNCIOS.read_text(encoding="utf-8"))
    alta = [c for c in anuncios["changes"] if cm_grafo.NOMBRE_CONTRATO in c.get("surfaces", [])]
    assert alta, "falta el alta de cm-grafo-v1 en contratos/cambios-anunciados.yaml"
    assert {"backend", "testing"} <= set(alta[0]["consumer_approval"])


def test_ningun_objeto_admite_propiedades_adicionales() -> None:
    assert cm_grafo.objetos_abiertos(CONTRATO) == []


def test_politica_y_presupuestos_declarados() -> None:
    politica = CONTRATO["x-politica"]
    assert {"versionado", "convivencia", "publicacion", "aprobacion"} <= set(politica)
    assert "al menos un sprint" in politica["convivencia"]
    for nombre, op in CONTRATO["operaciones"].items():
        latencia = op["latencia"]
        assert latencia["p95_ms"] > 0 and latencia["condiciones"], nombre
        archivo, funcion = latencia["verificado_por"].split("::")
        fuente = (cm_grafo._RAIZ / archivo).read_text(encoding="utf-8")
        assert f"def {funcion}(" in fuente, f"{nombre}: el benchmark {funcion} no existe"
    assert cm_grafo.presupuesto_p95_ms("impacto") == 500
    assert "inventario_interfaz" in CONTRATO["x-fuera-de-v1"]


# ---------------------------------------------------------------------------
# Protocolo
# ---------------------------------------------------------------------------
def test_cada_operacion_es_un_metodo_del_protocolo_con_sus_tipos() -> None:
    metodos = {
        n for n, _ in inspect.getmembers(ConsultaGrafo, inspect.isfunction) if not n.startswith("_")
    }
    assert metodos == {o.nombre for o in OPERACIONES}
    for o in OPERACIONES:
        hints = typing.get_type_hints(getattr(ConsultaGrafo, o.nombre))
        assert hints["consulta"] is o.entrada, o.nombre
        assert hints["return"] == o.salida, o.nombre


def test_el_doble_cumple_el_protocolo() -> None:
    doble = ConsultaGrafoDoble(GrafoEnMemoria([], [], []))
    assert isinstance(doble, ConsultaGrafo)


# ---------------------------------------------------------------------------
# Detector de cambios incompatibles: mutaciones del contrato real
# ---------------------------------------------------------------------------
def _mutar(cambio: Any) -> list[str]:
    actual = copy.deepcopy(CONTRATO)
    cambio(actual)
    return cambios_incompatibles(CONTRATO, actual)


def _entrada(c: dict[str, Any], op: str = "impacto") -> dict[str, Any]:
    return typing.cast(dict[str, Any], c["operaciones"][op]["entrada"])


def _salida(c: dict[str, Any], op: str = "impacto") -> dict[str, Any]:
    return typing.cast(dict[str, Any], c["operaciones"][op]["salida"])


def _nodo_alcanzado(c: dict[str, Any]) -> dict[str, Any]:
    return typing.cast(dict[str, Any], _salida(c)["$defs"]["NodoAlcanzado"])


INCOMPATIBLES = {
    "retirar-operacion": lambda c: c["operaciones"].pop("vecinos"),
    "cambiar-funcion": lambda c: c["operaciones"]["impacto"].update(funcion="x.otra"),
    "subir-presupuesto": lambda c: c["operaciones"]["impacto"]["latencia"].update(p95_ms=501),
    "retirar-campo-entrada": lambda c: _entrada(c)["properties"].pop("k"),
    "campo-obligatorio-nuevo": lambda c: (
        _entrada(c)["properties"].update(modo={"type": "string"}),
        _entrada(c).setdefault("required", []).append("modo"),
    ),
    "volver-obligatorio": lambda c: _entrada(c).setdefault("required", []).append("k"),
    "estrechar-cota-entrada": lambda c: _entrada(c)["properties"]["k"].update(maximum=4),
    "cambiar-default": lambda c: _entrada(c)["properties"]["k"].update(default=2),
    "cambiar-tipo-entrada": lambda c: _entrada(c)["properties"]["k"].update(type="string"),
    "quitar-valor-enum-entrada": lambda c: _entrada(c)["$defs"]["TipoArista"]["enum"].remove(
        "CALLS"
    ),
    "campo-nuevo-salida": lambda c: _nodo_alcanzado(c)["properties"].update(
        peso={"type": "number"}
    ),
    "retirar-campo-salida": lambda c: _nodo_alcanzado(c)["properties"].pop("kind"),
    "dejar-de-garantizar": lambda c: _nodo_alcanzado(c)["required"].remove("kind"),
    "salida-anulable": lambda c: _nodo_alcanzado(c)["properties"].update(
        kind={"anyOf": [{"type": "string"}, {"type": "null"}]}
    ),
    "ensanchar-cota-salida": lambda c: _nodo_alcanzado(c)["properties"]["distancia"].update(
        maximum=6
    ),
    "valor-enum-nuevo-salida": lambda c: _salida(c)["$defs"]["TipoArista"]["enum"].append("NUEVO"),
    "salida-abierta": lambda c: _nodo_alcanzado(c).update(additionalProperties=True),
    "elementos-de-lista": lambda c: _salida(c)["properties"]["semillas"].update(
        items={"type": "integer"}
    ),
}


@pytest.mark.parametrize("nombre", sorted(INCOMPATIBLES))
def test_detecta_cambios_incompatibles(nombre: str) -> None:
    assert _mutar(INCOMPATIBLES[nombre]), f"{nombre} no se detecta como incompatible"


COMPATIBLES = {
    "operacion-nueva": lambda c: c["operaciones"].update(nueva=c["operaciones"]["impacto"]),
    "bajar-presupuesto": lambda c: c["operaciones"]["impacto"]["latencia"].update(p95_ms=100),
    "campo-opcional-entrada": lambda c: _entrada(c)["properties"].update(
        modo={"type": "string", "default": "x"}
    ),
    "relajar-cota-entrada": lambda c: _entrada(c)["properties"]["k"].update(maximum=6),
    "valor-enum-nuevo-entrada": lambda c: _entrada(c)["$defs"]["TipoArista"]["enum"].append(
        "NUEVO"
    ),
    "estrechar-cota-salida": lambda c: _nodo_alcanzado(c)["properties"]["distancia"].update(
        maximum=4
    ),
    "descripciones": lambda c: _entrada(c)["properties"]["k"].update(description="otra"),
    "version-y-politica": lambda c: c.update({"x-version": "1.1.0", "x-politica": {}}),
}


@pytest.mark.parametrize("nombre", sorted(COMPATIBLES))
def test_no_marca_cambios_compatibles(nombre: str) -> None:
    assert _mutar(COMPATIBLES[nombre]) == [], nombre


def test_comparar_esquemas_resuelve_refs_y_variantes() -> None:
    base = {
        "$defs": {"X": {"type": "object", "properties": {"a": {"type": "integer"}}}},
        "anyOf": [{"$ref": "#/$defs/X"}, {"type": "null"}],
    }
    actual = copy.deepcopy(base)
    actual["$defs"]["X"]["properties"]["a"] = {"type": "string"}
    assert comparar_esquemas(base, actual, "salida")
    assert comparar_esquemas(base, base, "entrada") == []
    # quitar el null de una entrada: deja de aceptar null
    sin_null = {"$defs": base["$defs"], "$ref": "#/$defs/X"}
    assert any("null" in c for c in comparar_esquemas(base, sin_null, "entrada"))
    # composición de varias variantes distinta
    varias = {"anyOf": [{"type": "integer"}, {"type": "string", "maxLength": 3}]}
    otra = {"anyOf": [{"type": "integer"}, {"type": "string", "maxLength": 2}]}
    assert comparar_esquemas(varias, otra, "entrada")


def test_comparar_esquemas_formas_raras() -> None:
    # enumeración frente a valor libre, en ambos sentidos
    libre, enum = {"type": "string"}, {"type": "string", "enum": ["a"]}
    assert comparar_esquemas(libre, enum, "entrada")
    assert comparar_esquemas(enum, libre, "salida")
    assert comparar_esquemas(libre, enum, "salida") == []
    # lista que deja de tener esquema de elementos; formato; cota inferior
    assert comparar_esquemas({"items": {"type": "string"}}, {"type": "array"}, "salida")
    assert comparar_esquemas({"type": "string"}, {"type": "string", "format": "date"}, "entrada")
    assert comparar_esquemas({"minLength": 1}, {"minLength": 2}, "entrada")
    assert comparar_esquemas({"minLength": 1}, {"minLength": 0}, "salida")
    # sin tipo (any) y referencias cíclicas no cuelgan
    ciclo = {"$defs": {"A": {"$ref": "#/$defs/A"}}, "$ref": "#/$defs/A"}
    assert comparar_esquemas(ciclo, ciclo, "salida") == []
    # entrada que deja de aceptar propiedades adicionales
    abierto = {"type": "object", "properties": {}}
    cerrado = {"type": "object", "properties": {}, "additionalProperties": False}
    assert comparar_esquemas(abierto, cerrado, "entrada")
    assert comparar_esquemas(cerrado, abierto, "entrada") == []


def test_sin_propiedades_adicionales_detecta_objetos_abiertos() -> None:
    esquema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"type": "object", "properties": {}}},
        "$defs": {"Y": {"properties": {"a": {"type": "string"}}}},
    }
    assert sin_propiedades_adicionales(esquema) == ["$.properties.x", "$.$defs.Y"]


def test_versiones_y_publicacion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert cm_grafo.clave_version("1.10.0") > cm_grafo.clave_version("1.9.3")
    with pytest.raises(ValueError):
        cm_grafo.clave_version("v1")
    monkeypatch.setattr(cm_grafo, "RUTA_HISTORIAL", tmp_path / "historial")
    monkeypatch.setattr(cm_grafo, "RUTA_CONTRATO", tmp_path / "cm-grafo-v1.json")
    escritos = cm_grafo.publicar()
    assert [p.name for p in escritos] == ["cm-grafo-v1.json", f"{cm_grafo.VERSION}.json"]
    assert cm_grafo.publicar() == escritos  # republicar lo mismo es idempotente
    modificado = copy.deepcopy(CONTRATO)
    modificado["title"] = "otro"
    with pytest.raises(RuntimeError, match="ya está publicada"):
        cm_grafo.publicar(modificado)
    assert [v for v, _ in cm_grafo.versiones_publicadas(tmp_path / "historial")] == [
        cm_grafo.VERSION
    ]


# ---------------------------------------------------------------------------
# El doble produce respuestas válidas según el contrato publicado
# ---------------------------------------------------------------------------
def test_las_respuestas_del_doble_validan_contra_el_contrato() -> None:
    from kallicode_core.cm_busqueda import BusquedaHibridaIn
    from kallicode_core.cm_consulta import ConsultaNuggetsIn
    from kallicode_core.cm_grafo_doble import IncidenteDoble
    from kallicode_core.cm_impacto import ConsultaGrafoIn, ResolverNodosIn, VecinosIn
    from kallicode_core.cm_incidentes import ConsultaIncidentesIn

    grafo = GrafoEnMemoria(
        [("f1", "src/a.py", ["a", "b"])],
        ["t1"],
        [("CALLS", "a", "b"), ("USES_TABLE", "b", "t1"), ("DEFINES", "f1", "a")],
    )
    doble = ConsultaGrafoDoble(grafo)
    doble.registrar_incidente(IncidenteDoble(id="inc_1", resumen="Fallo", nodos=("b",)))
    area = ConsultaGrafoIn(ficheros=["src/a.py"], k=2)
    llamadas = {
        "resolver_nodos": ResolverNodosIn(ids=["a", "zz"]),
        "semillas": area,
        "impacto": area,
        "dependencias": area,
        "subgrafo": area,
        "vecinos": VecinosIn(nodo="a"),
        "dependencias_ocultas": area,
        "buscar_hibrido": BusquedaHibridaIn(embedding=[0.1] * 1024),
        "resolver_nuggets": ConsultaNuggetsIn(variable="nuggets", nodos=["a"]),
        "resolver_incidentes": ConsultaIncidentesIn(nodos=["a"]),
    }
    assert set(llamadas) == set(cm_grafo.OPERACIONES_POR_NOMBRE)
    for nombre, consulta in llamadas.items():
        op = CONTRATO["operaciones"][nombre]
        jsonschema.validate(consulta.model_dump(mode="json"), op["entrada"])
        respuesta = getattr(doble, nombre)(consulta)
        dump = (
            [r.model_dump(mode="json") for r in respuesta]
            if isinstance(respuesta, list)
            else respuesta.model_dump(mode="json")
        )
        jsonschema.validate(dump, op["salida"])
    assert [
        i.id for i in doble.resolver_incidentes(llamadas["resolver_incidentes"]).incidentes
    ] == ["inc_1"]
