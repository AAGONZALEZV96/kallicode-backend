"""Doble de prueba del contrato cm-grafo-v1 para Backend y Testing (KC-CM-09-4).

`ConsultaGrafoDoble` implementa `kallicode_core.cm_grafo.ConsultaGrafo` sobre un grafo en memoria,
sin base de datos, con la MISMA semántica que el cliente real: los recorridos son un BFS por niveles
(distancia mínima, tipo de la última arista de un camino mínimo, ciclos sin colgar), y las
respuestas se construyen con las mismas funciones de core (orden, recorte, truncado). La prueba
packages/codemapping/tests/test_contrato_consulta.py exige que doble y PostgreSQL devuelvan lo
mismo sobre el mismo grafo, y test_impacto.py usa `GrafoEnMemoria` como implementación de
referencia.

    grafo = GrafoEnMemoria(
        ficheros=[("f1", "src/billing/invoice.py", ["emitir", "calc_iva"])],
        tablas=["dbt_facturas"],
        aristas=[("CALLS", "emitir", "calc_iva"), ("USES_TABLE", "emitir", "dbt_facturas")],
    )
    doble = ConsultaGrafoDoble(grafo)
    doble.impacto(ConsultaGrafoIn(nodos=["calc_iva"], k=2)).nodos

Nuggets y búsqueda devuelven las filas registradas (`registrar_nuggets`, `registrar_busqueda`) o la
respuesta vacía válida; los incidentes se registran con sus nodos (`registrar_incidente`) y se
resuelven con el recorrido, como en la base.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kallicode_core import cm_busqueda, cm_consulta, cm_impacto, cm_incidentes
from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut
from kallicode_core.cm_consulta import ConsultaNuggetsIn, ConsultaNuggetsOut, VariablePlantilla
from kallicode_core.cm_impacto import (
    Arista,
    ConsultaGrafoIn,
    DependenciasOcultasOut,
    Direccion,
    NodoGrafo,
    RecorridoOut,
    ResolverNodosIn,
    SubgrafoOut,
    TipoArista,
    VecinosIn,
)
from kallicode_core.cm_incidentes import ConsultaIncidentesIn, ConsultaIncidentesOut

Fichero = tuple[str, str, list[str]]  # (id, path, símbolos)
AristaT = tuple[str, str, str]  # (tipo, src, dst)
FilaRecorrido = tuple[str, str, int, str | None]  # (id, kind, distancia, via_tipo)


@dataclass
class GrafoEnMemoria:
    """Grafo de CodeMapping en memoria con la semántica de referencia de los recorridos."""

    ficheros: list[Fichero]
    tablas: list[str]
    aristas: list[AristaT]
    kinds: dict[str, str] = field(default_factory=dict)
    salientes: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    entrantes: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for fid, _, simbolos in self.ficheros:
            self.kinds[fid] = "file"
            self.kinds.update(dict.fromkeys(simbolos, "symbol"))
        self.kinds.update(dict.fromkeys(self.tablas, "db_table"))
        sal: dict[str, list[tuple[str, str]]] = defaultdict(list)
        ent: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for tipo, src, dst in self.aristas:
            sal[src].append((tipo, dst))
            ent[dst].append((tipo, src))
        self.salientes, self.entrantes = dict(sal), dict(ent)

    def semillas(self, nodos: Sequence[str] = (), ficheros: Sequence[str] = ()) -> set[str]:
        out = {n for n in nodos if n in self.kinds}
        for fid, path, simbolos in self.ficheros:
            if path in ficheros:
                out |= {fid, *simbolos}
        return out

    def _vecinos(self, nodo: str, direccion: Direccion) -> list[tuple[str, str]]:
        res: list[tuple[str, str]] = []
        if direccion in ("entrante", "ambas"):
            res += self.entrantes.get(nodo, [])
        if direccion in ("saliente", "ambas"):
            res += self.salientes.get(nodo, [])
        return res

    def recorrer(
        self, semillas: set[str], k: int, tipos: Iterable[str], direccion: Direccion
    ) -> list[FilaRecorrido]:
        """BFS por niveles: distancia mínima y menor tipo de arista entre los caminos mínimos."""
        permitidos = {str(t) for t in tipos}
        distancia = dict.fromkeys(semillas, 0)
        via: dict[str, str | None] = dict.fromkeys(semillas)
        frontera = list(semillas)
        for d in range(1, k + 1):
            nuevos: dict[str, str] = {}
            for n in frontera:
                for tipo, m in self._vecinos(n, direccion):
                    if tipo in permitidos and m not in distancia:
                        nuevos[m] = min(nuevos.get(m, tipo), tipo)
            for m, tipo in nuevos.items():
                distancia[m], via[m] = d, tipo
            frontera = list(nuevos)
        filas = [(n, self.kinds[n], distancia[n], via[n]) for n in distancia if n in self.kinds]
        return sorted(filas, key=lambda f: (f[2], f[0]))

    def ocultas(self, semillas: set[str], k: int) -> list[tuple[str, list[str], int]]:
        alcance = {n: d for n, _, d, _ in self.recorrer(semillas, k, ["CALLS"], "saliente")}
        tablas: dict[str, int] = {}
        for n, d in alcance.items():
            for tipo, t in self.salientes.get(n, []):
                if tipo == "USES_TABLE":
                    tablas[t] = min(tablas.get(t, d), d)
        acoplados: dict[str, tuple[set[str], int]] = {}
        for t, d in tablas.items():
            for tipo, src in self.entrantes.get(t, []):
                if tipo == "USES_TABLE" and src not in alcance:
                    ts, dmin = acoplados.get(src, (set(), d))
                    acoplados[src] = (ts | {t}, min(dmin, d))
        filas = [(n, sorted(ts), d) for n, (ts, d) in acoplados.items() if n in self.kinds]
        return sorted(filas, key=lambda f: (f[2], f[0]))

    def aristas_entre(self, ids: set[str], tipos: Iterable[str]) -> list[AristaT]:
        permitidos = {str(t) for t in tipos}
        filas = [a for a in self.aristas if a[0] in permitidos and a[1] in ids and a[2] in ids]
        return sorted(filas, key=lambda a: (a[1], a[0], a[2]))


def grafo_sintetico(semilla: int) -> GrafoEnMemoria:
    """Grafo determinista de ~4.350 nodos y ~21.000 aristas, con ciclos y tablas compartidas.

    Es el grafo de referencia de los benchmarks del contrato (200 ficheros de 20 símbolos,
    150 tablas; CALLS de 1 a 7 por símbolo, USES_TABLE en el 30 %, REFERENCES entre tablas).
    """
    rnd = random.Random(semilla)
    ficheros: list[Fichero] = [
        (f"f{i:03d}", f"src/m{i % 10}/f{i:03d}.py", [f"s{i:03d}_{j:02d}" for j in range(20)])
        for i in range(200)
    ]
    simbolos = [s for _, _, ss in ficheros for s in ss]
    tablas = [f"t{i:03d}" for i in range(150)]
    aristas: set[AristaT] = set()
    for fid, _, ss in ficheros:
        aristas |= {("DEFINES", fid, s) for s in ss}
    for s in simbolos:
        aristas |= {("CALLS", s, rnd.choice(simbolos)) for _ in range(rnd.randint(1, 7))}
        if rnd.random() < 0.3:
            aristas |= {("USES_TABLE", s, rnd.choice(tablas)) for _ in range(rnd.randint(1, 2))}
    aristas |= {("REFERENCES", rnd.choice(tablas), rnd.choice(tablas)) for _ in range(200)}
    return GrafoEnMemoria(ficheros, tablas, sorted(aristas))


@dataclass(frozen=True)
class IncidenteDoble:
    id: str
    resumen: str
    nodos: tuple[str, ...]
    origen: str = "manual"
    ticket: str | None = None
    causa: str | None = None
    correccion: str | None = None
    creado_en: datetime = field(default_factory=lambda: datetime.now(UTC))
    embedding: tuple[float, ...] | None = None


def _coseno(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


class ConsultaGrafoDoble:
    """Implementación en memoria de `ConsultaGrafo` (un tenant: el del grafo)."""

    def __init__(self, grafo: GrafoEnMemoria) -> None:
        self.grafo = grafo
        self._nuggets: dict[VariablePlantilla, list[Mapping[str, Any]]] = {}
        self._busqueda: list[Mapping[str, Any]] = []
        self._incidentes: list[IncidenteDoble] = []

    # --- datos registrados -----------------------------------------------------------------
    def registrar_nuggets(
        self, variable: VariablePlantilla, filas: Sequence[Mapping[str, Any]]
    ) -> None:
        """Filas ya ordenadas con las columnas de cm_consulta (id, regla, tipo, ...)."""
        self._nuggets[variable] = list(filas)

    def registrar_busqueda(self, filas: Sequence[Mapping[str, Any]]) -> None:
        """Filas ya ordenadas con las columnas de cm_busqueda.COLUMNAS."""
        self._busqueda = list(filas)

    def registrar_incidente(self, incidente: IncidenteDoble) -> None:
        self._incidentes.append(incidente)

    # --- recorridos ------------------------------------------------------------------------
    def _semillas(self, consulta: ConsultaGrafoIn) -> set[str]:
        return self.grafo.semillas(consulta.nodos, consulta.ficheros)

    def _filas(self, consulta: ConsultaGrafoIn, direccion: Direccion) -> list[dict[str, Any]]:
        tipos = cm_impacto.tipos_efectivos(consulta, direccion)
        return [
            {"id": n, "kind": kind, "distancia": d, "via_tipo": via}
            for n, kind, d, via in self.grafo.recorrer(
                self._semillas(consulta), consulta.k, tipos, direccion
            )
        ]

    def resolver_nodos(self, consulta: ResolverNodosIn) -> list[NodoGrafo]:
        ids = sorted({i for i in consulta.ids if i in self.grafo.kinds})
        return [NodoGrafo(id=i, kind=self.grafo.kinds[i]) for i in ids]

    def semillas(self, consulta: ConsultaGrafoIn) -> list[NodoGrafo]:
        return [NodoGrafo(id=i, kind=self.grafo.kinds[i]) for i in sorted(self._semillas(consulta))]

    def impacto(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return cm_impacto.construir_recorrido(
            consulta, "entrante", self._filas(consulta, "entrante")
        )

    def dependencias(self, consulta: ConsultaGrafoIn) -> RecorridoOut:
        return cm_impacto.construir_recorrido(
            consulta, "saliente", self._filas(consulta, "saliente")
        )

    def subgrafo(self, consulta: ConsultaGrafoIn) -> SubgrafoOut:
        filas = self._filas(consulta, "ambas")
        ids = {str(f["id"]) for f in filas[: cm_impacto.MAX_NODOS]}
        tipos = cm_impacto.tipos_efectivos(consulta, "ambas")
        aristas = [
            {
                "tipo": t,
                "src_kind": self.grafo.kinds[s],
                "src_id": s,
                "dst_kind": self.grafo.kinds[d],
                "dst_id": d,
            }
            for t, s, d in self.grafo.aristas_entre(ids, tipos)
        ]
        return cm_impacto.construir_subgrafo(consulta, filas, aristas)

    def vecinos(self, consulta: VecinosIn) -> list[Arista]:
        tipos = (
            {t.value for t in consulta.tipos} if consulta.tipos else {t.value for t in TipoArista}
        )
        filas = [
            a
            for a in self.grafo.aristas
            if a[0] in tipos
            and (
                (consulta.direccion in ("entrante", "ambas") and a[2] == consulta.nodo)
                or (consulta.direccion in ("saliente", "ambas") and a[1] == consulta.nodo)
            )
        ]
        # Una autorreferencia sale una vez por cada sentido, como el UNION ALL de la consulta.
        if consulta.direccion == "ambas":
            filas += [a for a in filas if a[1] == a[2] == consulta.nodo]
        filas.sort(key=lambda a: (a[1], a[0], a[2]))
        return cm_impacto.aristas(
            {
                "tipo": t,
                "src_kind": self.grafo.kinds[s],
                "src_id": s,
                "dst_kind": self.grafo.kinds[d],
                "dst_id": d,
            }
            for t, s, d in filas[: cm_impacto.MAX_ARISTAS]
        )

    def dependencias_ocultas(self, consulta: ConsultaGrafoIn) -> DependenciasOcultasOut:
        semillas = self._semillas(consulta)
        filas = [
            {"id": n, "kind": self.grafo.kinds[n], "tablas": ts, "distancia": d}
            for n, ts, d in self.grafo.ocultas(semillas, consulta.k)
        ]
        return cm_impacto.construir_ocultas(consulta, sorted(semillas), filas)

    # --- búsqueda, nuggets e incidentes ----------------------------------------------------
    def buscar_hibrido(self, consulta: BusquedaHibridaIn) -> BusquedaHibridaOut:
        return cm_busqueda.construir_respuesta(consulta, self._busqueda[: consulta.k])

    def resolver_nuggets(self, consulta: ConsultaNuggetsIn) -> ConsultaNuggetsOut:
        return cm_consulta.construir_respuesta(consulta, self._nuggets.get(consulta.variable, []))

    def resolver_incidentes(self, consulta: ConsultaIncidentesIn) -> ConsultaIncidentesOut:
        if not consulta.con_area():
            return cm_incidentes.respuesta_vacia(consulta)
        semillas = self.grafo.semillas(consulta.nodos, consulta.ficheros)
        tipos = [t.value for t in cm_impacto.TIPOS_DEPENDENCIA]
        distancia = {
            n: d for n, _, d, _ in self.grafo.recorrer(semillas, consulta.saltos, tipos, "ambas")
        }
        filas: list[dict[str, Any]] = []
        for inc in self._incidentes:
            ds = [distancia[n] for n in inc.nodos if n in distancia]
            if not ds:
                continue
            similitud = (
                None
                if consulta.embedding is None or inc.embedding is None
                else _coseno(inc.embedding, consulta.embedding)
            )
            filas.append(
                {
                    "id": inc.id,
                    "ticket": inc.ticket,
                    "origen": inc.origen,
                    "resumen": inc.resumen,
                    "causa": inc.causa,
                    "correccion": inc.correccion,
                    "creado_en": inc.creado_en,
                    "distancia": min(ds),
                    "similitud": similitud,
                }
            )
        filas.sort(
            key=lambda f: (
                f["distancia"],
                f["similitud"] is None,
                -(f["similitud"] or 0.0),
                -f["creado_en"].timestamp(),
                f["id"],
            )
        )
        return cm_incidentes.construir_respuesta(consulta, filas[: cm_incidentes.MAX_CANDIDATOS])
