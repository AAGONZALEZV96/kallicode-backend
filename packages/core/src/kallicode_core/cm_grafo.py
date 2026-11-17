"""Contrato cm-grafo-v1: la API de consulta del grafo de CodeMapping, congelada (KC-CM-09-4).

Backend la consume en triage, diseño y build; Testing, en la generación de pruebas. La API es la
librería `kallicode_codemapping.cm_queries` (no hay rutas HTTP): este módulo es su contrato único.

- `ConsultaGrafo`: el protocolo que implementan el cliente real
  (`kallicode_codemapping.consulta_grafo.ConsultaGrafoPostgres`) y el doble en memoria
  (`kallicode_core.cm_grafo_doble.ConsultaGrafoDoble`). Una operación = un método que recibe un
  modelo de entrada y devuelve uno de salida.
- `OPERACIONES`: catálogo con función, entrada, salida y presupuesto de latencia (p95) de cada
  operación, y el benchmark del CI que lo verifica. Los benchmarks leen el presupuesto de aquí.
- `generar_contrato()`: el JSON Schema publicado en `contratos/cm-grafo-v1.json`, con
  `additionalProperties: false` en todo objeto y la política de versionado y convivencia.
- `cambios_incompatibles()`: la prueba de contrato. Cada versión publicada queda inmutable en
  `contratos/historial/cm-grafo-v1/<x-version>.json`; entre dos versiones publicadas, un cambio
  incompatible solo pasa si está anunciado en `contratos/cambios-anunciados.yaml`.

Reutiliza sin cambiarlos los contratos ya congelados cm-consulta-v1 ({{nuggets}}) y
cm-incidentes-v1 ({{incidents}}), y congela por primera vez cm-impacto-v1 (recorridos, KC-CM-09-1) y
cm-busqueda-v1 (búsqueda híbrida, KC-CM-08-4).

Regenerar tras un cambio (y publicar la versión, sin sobrescribir una ya publicada):

    python -m kallicode_core.cm_grafo
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import TypeAdapter

from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut
from kallicode_core.cm_consulta import ConsultaNuggetsIn, ConsultaNuggetsOut
from kallicode_core.cm_impacto import (
    Arista,
    ConsultaGrafoIn,
    DependenciasOcultasOut,
    NodoGrafo,
    RecorridoOut,
    ResolverNodosIn,
    SubgrafoOut,
    VecinosIn,
)
from kallicode_core.cm_incidentes import ConsultaIncidentesIn, ConsultaIncidentesOut
from kallicode_core.contratos_compat import comparar_esquemas, sin_propiedades_adicionales

NOMBRE_CONTRATO = "cm-grafo-v1"
VERSION = "1.0.0"
_RAIZ = Path(__file__).resolve().parents[4]
RUTA_CONTRATO = _RAIZ / "contratos" / f"{NOMBRE_CONTRATO}.json"
RUTA_HISTORIAL = _RAIZ / "contratos" / "historial" / NOMBRE_CONTRATO
RUTA_ANUNCIOS = _RAIZ / "contratos" / "cambios-anunciados.yaml"


# ---------------------------------------------------------------------------
# Protocolo
# ---------------------------------------------------------------------------
@runtime_checkable
class ConsultaGrafo(Protocol):
    """La API de consulta del grafo, para el tenant de la sesión del implementador."""

    def resolver_nodos(self, consulta: ResolverNodosIn) -> list[NodoGrafo]: ...
    def semillas(self, consulta: ConsultaGrafoIn) -> list[NodoGrafo]: ...
    def impacto(self, consulta: ConsultaGrafoIn) -> RecorridoOut: ...
    def dependencias(self, consulta: ConsultaGrafoIn) -> RecorridoOut: ...
    def subgrafo(self, consulta: ConsultaGrafoIn) -> SubgrafoOut: ...
    def vecinos(self, consulta: VecinosIn) -> list[Arista]: ...
    def dependencias_ocultas(self, consulta: ConsultaGrafoIn) -> DependenciasOcultasOut: ...
    def buscar_hibrido(self, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut: ...
    def resolver_nuggets(self, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut: ...
    def resolver_incidentes(self, consulta: ConsultaIncidentesIn) -> ConsultaIncidentesOut: ...


# ---------------------------------------------------------------------------
# Catálogo de operaciones
# ---------------------------------------------------------------------------
_BENCH_GRAFO = (
    "packages/codemapping/tests/test_contrato_consulta.py::test_latencia_p95_por_operacion"
)


@dataclass(frozen=True)
class Operacion:
    nombre: str  # = método de ConsultaGrafo
    funcion: str  # función de kallicode_codemapping.cm_queries que la implementa
    descripcion: str
    entrada: Any
    salida: Any
    contrato_origen: str
    presupuesto_p95_ms: int
    condiciones: str
    verificado_por: str


_GRAFO_SINTETICO = (
    "grafo sintético determinista (~4.350 nodos, ~21.000 aristas, con ciclos), k = 3, "
    "una semilla, PostgreSQL 16 del CI"
)

OPERACIONES: tuple[Operacion, ...] = (
    Operacion(
        "resolver_nodos",
        "resolver_nodos",
        "Ids que existen en el tenant, con su kind (orden por id).",
        ResolverNodosIn,
        list[NodoGrafo],
        "cm-impacto-v1",
        500,
        "grafo sintético, 1 id",
        _BENCH_GRAFO,
    ),
    Operacion(
        "semillas",
        "semillas",
        "Nodos de partida de un área: los nodos pedidos y cada fichero con sus símbolos.",
        ConsultaGrafoIn,
        list[NodoGrafo],
        "cm-impacto-v1",
        500,
        "grafo sintético, 1 fichero",
        _BENCH_GRAFO,
    ),
    Operacion(
        "impacto",
        "impacto",
        "Quién depende del área hasta k saltos (aristas en sentido inverso, CYCLE).",
        ConsultaGrafoIn,
        RecorridoOut,
        "cm-impacto-v1",
        500,
        _GRAFO_SINTETICO,
        _BENCH_GRAFO,
    ),
    Operacion(
        "dependencias",
        "dependencias",
        "De qué depende el área hasta k saltos (aristas hacia delante, CYCLE).",
        ConsultaGrafoIn,
        RecorridoOut,
        "cm-impacto-v1",
        500,
        _GRAFO_SINTETICO,
        _BENCH_GRAFO,
    ),
    Operacion(
        "subgrafo",
        "subgrafo",
        "Vecindad en ambos sentidos hasta k saltos y las aristas entre sus nodos.",
        ConsultaGrafoIn,
        SubgrafoOut,
        "cm-impacto-v1",
        500,
        _GRAFO_SINTETICO,
        _BENCH_GRAFO,
    ),
    Operacion(
        "vecinos",
        "vecinos",
        "Aristas a un salto de un nodo, filtradas por sentido y tipo.",
        VecinosIn,
        list[Arista],
        "cm-impacto-v1",
        500,
        "grafo sintético, un nodo, ambos sentidos",
        _BENCH_GRAFO,
    ),
    Operacion(
        "dependencias_ocultas",
        "dependencias_ocultas",
        "Nodos acoplados por una tabla compartida (CALLS* -> USES_TABLE <-) sin llamada.",
        ConsultaGrafoIn,
        DependenciasOcultasOut,
        "cm-impacto-v1",
        500,
        _GRAFO_SINTETICO,
        _BENCH_GRAFO,
    ),
    Operacion(
        "buscar_hibrido",
        "buscar_hibrido",
        "Top-k por similitud (HNSW) en definiciones, documentación y nuggets, con filtros.",
        BusquedaHibridaIn,
        BusquedaHibridaOut,
        "cm-busqueda-v1",
        400,
        "200.000 vectores, tenant del 1 % del corpus, iterative_scan (benchmark semanal)",
        "packages/codemapping/tests/bench_busqueda_hibrida.py::test_p95_bajo_400_ms_con_200000_vectores",
    ),
    Operacion(
        "resolver_nuggets",
        "resolver_nuggets",
        "Resolución de {{nuggets}}, {{module_conventions}} y {{nuggets_for_file}}.",
        ConsultaNuggetsIn,
        ConsultaNuggetsOut,
        "cm-consulta-v1",
        200,
        "grafo de referencia (tests/fixtures/repo_ref) con 2 x 2000 nuggets",
        "packages/codemapping/tests/test_cm_queries.py::test_latencia_p95_bajo_200ms_en_el_grafo_de_referencia",
    ),
    Operacion(
        "resolver_incidentes",
        "resolver_incidentes",
        "Resolución de {{incidents}}: incidentes a <= 2 saltos del área, recortados.",
        ConsultaIncidentesIn,
        ConsultaIncidentesOut,
        "cm-incidentes-v1",
        200,
        "grafo sintético con 2.000 incidentes enlazados, saltos = 2, con embedding",
        _BENCH_GRAFO,
    ),
)

OPERACIONES_POR_NOMBRE: dict[str, Operacion] = {o.nombre: o for o in OPERACIONES}


def presupuesto_p95_ms(nombre: str) -> int:
    """Presupuesto de latencia p95 declarado en el contrato (fuente única para los benchmarks)."""
    return OPERACIONES_POR_NOMBRE[nombre].presupuesto_p95_ms


# ---------------------------------------------------------------------------
# Política de versionado y convivencia
# ---------------------------------------------------------------------------
CAMBIOS_INCOMPATIBLES = (
    "retirar o renombrar una operación o cambiar su función",
    "retirar o renombrar un campo de entrada o de salida",
    "añadir un campo obligatorio a una entrada o volver obligatorio uno opcional",
    "añadir un campo a una salida (los consumidores validan con additionalProperties: false)",
    "dejar de garantizar un campo de salida o volverlo anulable",
    "cambiar un tipo, estrechar una cota o un enum de entrada, ensanchar los de salida",
    "cambiar un valor por defecto, un formato o un patrón de entrada",
    "subir el presupuesto de latencia de una operación",
)

POLITICA: dict[str, Any] = {
    "versionado": (
        "SemVer en x-version. PATCH: solo textos (descripciones). MINOR: cambios compatibles "
        "(operación nueva, campo de entrada opcional nuevo, cota de entrada relajada, presupuesto "
        "de latencia más estricto). MAJOR: cualquier cambio incompatible; se publica como un "
        "contrato nuevo (cm-grafo-v2.json) y la v1 no se modifica."
    ),
    "cambios_incompatibles": list(CAMBIOS_INCOMPATIBLES),
    "convivencia": (
        "Una versión mayor nueva convive con la anterior al menos un sprint (5 días hábiles) "
        "desde su publicación: las dos se publican, se implementan y se prueban en el CI. La "
        "anterior se marca x-deprecado con su fecha de retiro y no se retira sin la aprobación "
        "de Backend y Testing en contratos/cambios-anunciados.yaml."
    ),
    "publicacion": (
        "Cada versión publicada queda inmutable en contratos/historial/cm-grafo-v1/"
        "<x-version>.json y contratos/cm-grafo-v1.json es siempre la última. La prueba de "
        "contrato (packages/core/tests/test_cm_grafo.py) falla si el contrato cambia sin "
        "publicar una versión nueva, si una versión publicada se modifica o si entre dos "
        "versiones publicadas hay un cambio incompatible que no esté anunciado en "
        "contratos/cambios-anunciados.yaml (breaking: true, surfaces: [cm-grafo-v1], "
        "contrato_version: <x-version>)."
    ),
    "aprobacion": (
        "Consumidores: backend y testing. Una versión entra en vigor cuando ambos la aprueban "
        "en contratos/cambios-anunciados.yaml (consumer_approval) y el Architecture Board la "
        "registra en approved_by."
    ),
}

FUERA_DE_V1: dict[str, str] = {
    "inventario_interfaz": (
        "Inventario de elementos de interfaz (GrafoCliente.inventario_ui): CodeMapping no tiene "
        "implementación; Testing lo resuelve con kallicode_testing.inventory_client y el contrato "
        "selectores-portal-v1. Previsto como operación nueva (cambio compatible, v1.1)."
    ),
}


# ---------------------------------------------------------------------------
# Generación
# ---------------------------------------------------------------------------
def esquema(tipo: Any) -> dict[str, Any]:
    """JSON Schema (modo validación) de un modelo o de una lista de modelos."""
    return TypeAdapter(tipo).json_schema()


def _operacion(o: Operacion) -> dict[str, Any]:
    return {
        "metodo": f"ConsultaGrafo.{o.nombre}",
        "funcion": f"kallicode_codemapping.cm_queries.{o.funcion}",
        "descripcion": o.descripcion,
        "contrato_origen": o.contrato_origen,
        "latencia": {
            "p95_ms": o.presupuesto_p95_ms,
            "condiciones": o.condiciones,
            "verificado_por": o.verificado_por,
        },
        "entrada": esquema(o.entrada),
        "salida": esquema(o.salida),
    }


def generar_contrato() -> dict[str, Any]:
    """El contrato cm-grafo-v1 a partir del catálogo y de los modelos (fuente única)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:kallicode:contrato:{NOMBRE_CONTRATO}",
        "title": "API de consulta del grafo de CodeMapping",
        "description": (
            "Contrato v1 entre CodeMapping y sus consumidores (Backend y Testing), KC-CM-09-4. "
            "Cada operación declara su método en ConsultaGrafo, su función en "
            "kallicode_codemapping.cm_queries, sus esquemas de entrada y salida "
            "(additionalProperties: false en todo objeto) y su presupuesto de latencia p95, "
            "verificado por un benchmark del CI. El tenant es siempre el de la sesión."
        ),
        "x-version": VERSION,
        "x-consumidores": ["backend", "testing"],
        "x-politica": POLITICA,
        "x-fuera-de-v1": FUERA_DE_V1,
        "operaciones": {o.nombre: _operacion(o) for o in OPERACIONES},
    }


def serializar(contrato: dict[str, Any]) -> str:
    return json.dumps(contrato, ensure_ascii=False, indent=2) + "\n"


def serializar_contrato() -> str:
    return serializar(generar_contrato())


# ---------------------------------------------------------------------------
# Compatibilidad entre versiones publicadas
# ---------------------------------------------------------------------------
def objetos_abiertos(contrato: dict[str, Any]) -> list[str]:
    """Objetos de entrada o salida sin additionalProperties: false (debe ser [])."""
    faltan: list[str] = []
    for nombre, op in contrato["operaciones"].items():
        for parte in ("entrada", "salida"):
            faltan += sin_propiedades_adicionales(op[parte], f"{nombre}.{parte}")
    return faltan


def cambios_incompatibles(base: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Cambios de `actual` que rompen a un consumidor de `base` (dos contratos cm-grafo)."""
    cambios: list[str] = []
    ops_b, ops_a = base["operaciones"], actual["operaciones"]
    for nombre in sorted(set(ops_b) - set(ops_a)):
        cambios.append(f"{nombre}: operación retirada")
    for nombre in sorted(set(ops_b) & set(ops_a)):
        b, a = ops_b[nombre], ops_a[nombre]
        for clave in ("metodo", "funcion"):
            if b[clave] != a[clave]:
                cambios.append(f"{nombre}: cambia {clave} ({b[clave]} -> {a[clave]})")
        if a["latencia"]["p95_ms"] > b["latencia"]["p95_ms"]:
            cambios.append(
                f"{nombre}: el presupuesto p95 sube "
                f"({b['latencia']['p95_ms']} -> {a['latencia']['p95_ms']} ms)"
            )
        cambios += comparar_esquemas(b["entrada"], a["entrada"], "entrada", f"{nombre}.entrada")
        cambios += comparar_esquemas(b["salida"], a["salida"], "salida", f"{nombre}.salida")
    return cambios


_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def clave_version(version: str) -> tuple[int, int, int]:
    m = _SEMVER.fullmatch(version)
    if not m:
        raise ValueError(f"versión no SemVer: {version!r}")
    return int(m[1]), int(m[2]), int(m[3])


def versiones_publicadas(ruta: Path = RUTA_HISTORIAL) -> list[tuple[str, dict[str, Any]]]:
    """(versión, contrato) de cada versión publicada, en orden SemVer."""
    publicadas = [(p.stem, json.loads(p.read_text(encoding="utf-8"))) for p in ruta.glob("*.json")]
    return sorted(publicadas, key=lambda x: clave_version(x[0]))


def publicar(contrato: dict[str, Any] | None = None) -> list[Path]:
    """Escribe el contrato vigente y su versión en el historial (sin sobrescribir otra)."""
    contrato = contrato or generar_contrato()
    texto = serializar(contrato)
    destino = RUTA_HISTORIAL / f"{contrato['x-version']}.json"
    if destino.exists() and destino.read_text(encoding="utf-8") != texto:
        raise RuntimeError(
            f"{destino.name} ya está publicada con otro contenido: sube x-version "
            "(kallicode_core.cm_grafo.VERSION) según la política de versionado"
        )
    RUTA_HISTORIAL.mkdir(parents=True, exist_ok=True)
    for ruta in (RUTA_CONTRATO, destino):
        ruta.write_text(texto, encoding="utf-8", newline="\n")
    return [RUTA_CONTRATO, destino]


if __name__ == "__main__":  # pragma: no cover - publicación manual del contrato
    for escrito in publicar():
        sys.stdout.write(f"contrato escrito en {escrito}\n")
