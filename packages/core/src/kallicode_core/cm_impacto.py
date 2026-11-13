"""Consultas de recorrido del grafo: impacto a k saltos y dependencias ocultas (KC-CM-09-1).

El pipeline, antes de actuar, resuelve el área que va a tocar (nodos o ficheros) y expande su
vecindad en el grafo de CodeMapping (`cm.nodes` + `cm.edges`). Este módulo es la única definición
de esas consultas y de sus modelos de entrada/salida; el cliente psycopg síncrono está en
`kallicode_codemapping.cm_queries` (misma SQL con otra sintaxis de parámetros). La congelación del
contrato como JSON Schema es KC-CM-09-4.

Las ocho consultas (`CONSULTAS`):

    resolver_nodos         ids -> nodos existentes del tenant, con su kind
    semillas               nodos + rutas -> nodos de partida (cada fichero con sus símbolos)
    impacto                quién depende del área: aristas recorridas en sentido inverso
    dependencias           de qué depende el área: aristas recorridas hacia delante
    subgrafo_nodos         vecindad en ambos sentidos hasta k saltos
    aristas_entre          aristas cuyos dos extremos están en un conjunto de nodos
    vecinos                un salto desde un nodo, con filtro por tipo y sentido
    dependencias_ocultas   nodos acoplados por una tabla compartida (CALLS* -> USES_TABLE <- )
                           que no se alcanzan por CALLS desde el área

Garantías:
- Toda referencia a una tabla de `cm` filtra `tenant_id = core.current_tenant()` de forma
  explícita, además de RLS, también dentro del término recursivo (cada nivel del recorrido).
  Sin tenant en la sesión no devuelven nada. Un test inspecciona el SQL de las ocho.
- Los recorridos usan `WITH RECURSIVE ... CYCLE`: un ciclo (incluida la autorreferencia) corta la
  rama en lugar de colgar la consulta. `k` está acotado a K_MAX porque CYCLE evita bucles pero no
  la explosión de caminos simples en grafos densos; el resultado se acota a MAX_NODOS.
- Cada nodo aparece una vez, con su distancia mínima a las semillas y el tipo de la última arista
  de un camino mínimo (el menor en orden alfabético si hay varios). Orden: distancia, id (C).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION_CONTRATO = "cm-impacto-v1"

K_DEFECTO = 3
K_MAX = 5
MAX_SEMILLAS = 500
MAX_NODOS = 5_000
MAX_ARISTAS = 20_000
# Tope por sentencia: una consulta degenerada falla en lugar de bloquear al pipeline.
TIEMPO_MAXIMO_MS = 5_000


class TipoArista(StrEnum):
    """Los tipos de cm.edges (edges_tipo_chk, migración 002)."""

    DEFINES = "DEFINES"
    CALLS = "CALLS"
    HAS_COLUMN = "HAS_COLUMN"
    REFERENCES = "REFERENCES"
    USES_TABLE = "USES_TABLE"
    REALIZED_BY = "REALIZED_BY"
    RELATES = "RELATES"
    EXPRESSES = "EXPRESSES"


# Aristas de dependencia estructural: las que propagan un cambio. Las semánticas (EXPRESSES,
# REALIZED_BY, RELATES) y DEFINES quedan fuera por defecto; las semillas de tipo fichero ya se
# expanden a sus símbolos.
TIPOS_DEPENDENCIA: tuple[TipoArista, ...] = (
    TipoArista.CALLS,
    TipoArista.USES_TABLE,
    TipoArista.REFERENCES,
    TipoArista.HAS_COLUMN,
)

Direccion = Literal["entrante", "saliente", "ambas"]


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class ConsultaGrafoIn(BaseModel):
    """Área de partida y alcance del recorrido. El tenant es el de la sesión."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodos: list[str] = Field(
        default_factory=list,
        max_length=MAX_SEMILLAS,
        description="Ids de nodos del grafo (cm.nodes) desde los que se recorre.",
    )
    ficheros: list[str] = Field(
        default_factory=list,
        max_length=MAX_SEMILLAS,
        description="Rutas (cm.files.path); cada una aporta el fichero y sus símbolos.",
    )
    k: int = Field(default=K_DEFECTO, ge=1, le=K_MAX, description="Saltos máximos.")
    tipos: list[TipoArista] | None = Field(
        default=None,
        min_length=1,
        description="Tipos de arista recorridos; por defecto los de dependencia (impacto y "
        "dependencias) o todos (subgrafo).",
    )

    @model_validator(mode="after")
    def _con_semillas(self) -> ConsultaGrafoIn:
        if not self.nodos and not self.ficheros:
            raise ValueError("hace falta al menos un nodo o un fichero de partida")
        return self


class NodoGrafo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: str


class NodoAlcanzado(NodoGrafo):
    distancia: int = Field(ge=0, le=K_MAX, description="0 para las semillas.")
    via_tipo: TipoArista | None = Field(
        description="Tipo de la última arista de un camino mínimo; null en las semillas."
    )


class Arista(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tipo: TipoArista
    src_kind: str
    src_id: str
    dst_kind: str
    dst_id: str


class RecorridoOut(BaseModel):
    """Resultado de impacto (entrante) o dependencias (saliente)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-impacto-v1"] = VERSION_CONTRATO
    direccion: Direccion
    k: int
    tipos: list[TipoArista]
    semillas: list[str] = Field(description="Nodos de partida que existen en el tenant.")
    nodos: list[NodoAlcanzado] = Field(description="Semillas incluidas, a distancia 0.")
    truncado: bool = Field(description=f"Se alcanzaron más de {MAX_NODOS} nodos.")


class SubgrafoOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-impacto-v1"] = VERSION_CONTRATO
    k: int
    tipos: list[TipoArista]
    semillas: list[str]
    nodos: list[NodoAlcanzado]
    aristas: list[Arista]
    truncado: bool


class DependenciaOculta(NodoGrafo):
    tablas: list[str] = Field(min_length=1, description="Tablas compartidas con el área.")
    distancia: int = Field(
        ge=0, le=K_MAX, description="Saltos CALLS hasta el símbolo del área que usa la tabla."
    )


class DependenciasOcultasOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["cm-impacto-v1"] = VERSION_CONTRATO
    k: int
    semillas: list[str]
    dependencias: list[DependenciaOculta]
    truncado: bool


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
# Cada alias de una tabla de cm es único dentro de su consulta y lleva su propio
# `<alias>.tenant_id = core.current_tenant()`: el test de inspección lo exige.
_SEMILLAS = """
semillas_fichero AS (
    SELECT sf.id
      FROM cm.files sf
     WHERE sf.tenant_id = core.current_tenant()
       AND sf.path = ANY(CAST(:ficheros AS text[]))
),
semillas AS (
    SELECT sn.id
      FROM cm.nodes sn
     WHERE sn.tenant_id = core.current_tenant()
       AND sn.id = ANY(CAST(:nodos AS text[]))
    UNION
    SELECT x.id FROM semillas_fichero x
    UNION
    SELECT ss.id
      FROM cm.symbols ss
      JOIN semillas_fichero y ON y.id = ss.file_id
     WHERE ss.tenant_id = core.current_tenant()
)"""

_PASO_ENTRANTE = """
          SELECT ei.src_id AS id, ei.tipo
            FROM cm.edges ei
           WHERE ei.tenant_id = core.current_tenant()
             AND ei.dst_id = r.id
             AND ei.tipo = ANY(CAST(:tipos AS text[]))"""

_PASO_SALIENTE = """
          SELECT eo.dst_id AS id, eo.tipo
            FROM cm.edges eo
           WHERE eo.tenant_id = core.current_tenant()
             AND eo.src_id = r.id
             AND eo.tipo = ANY(CAST(:tipos AS text[]))"""


def ctes_recorrido(direccion: Direccion) -> str:
    """`WITH RECURSIVE` hasta la CTE `alcanzados(id, distancia, via_tipo)`, sin SELECT final.

    Recorre hasta :k saltos desde las semillas (:nodos, :ficheros) por aristas de :tipos en el
    sentido indicado; `alcanzados` tiene un nodo por fila con su distancia mínima. La reutilizan
    otras consultas que parten de un área del grafo (p. ej. kallicode_core.cm_incidentes).

    El paso recursivo es un LATERAL para que la referencia a `recorrido` aparezca una sola vez
    aunque se sigan los dos sentidos; cada rama usa su índice (edges_in_ix / edges_out_ix).
    """
    ramas = {
        "entrante": [_PASO_ENTRANTE],
        "saliente": [_PASO_SALIENTE],
        "ambas": [_PASO_ENTRANTE, _PASO_SALIENTE],
    }[direccion]
    paso = "\n          UNION ALL".join(ramas)
    return f"""
WITH RECURSIVE {_SEMILLAS.strip()},
recorrido (id, distancia, via_tipo) AS (
    SELECT s.id, 0, CAST(NULL AS text) FROM semillas s
    UNION ALL
    SELECT v.id, r.distancia + 1, v.tipo
      FROM recorrido r
      CROSS JOIN LATERAL ({paso}
      ) v
     WHERE r.distancia < CAST(:k AS integer)
) CYCLE id SET es_ciclo USING ruta,
alcanzados AS (
    SELECT DISTINCT ON (r.id) r.id, r.distancia, r.via_tipo
      FROM recorrido r
     WHERE NOT r.es_ciclo
     ORDER BY r.id, r.distancia, r.via_tipo COLLATE "C" NULLS FIRST
)"""


def _sql_recorrido(direccion: Direccion) -> str:
    return (
        ctes_recorrido(direccion)
        + """
SELECT a.id, n.kind, a.distancia, a.via_tipo
  FROM alcanzados a
  JOIN cm.nodes n ON n.tenant_id = core.current_tenant() AND n.id = a.id
 ORDER BY a.distancia, a.id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""
    )


SQL_RESOLVER_NODOS = """
SELECT rn.id, rn.kind
  FROM cm.nodes rn
 WHERE rn.tenant_id = core.current_tenant()
   AND rn.id = ANY(CAST(:nodos AS text[]))
 ORDER BY rn.id COLLATE "C"
"""

SQL_SEMILLAS = f"""
WITH {_SEMILLAS.strip()}
SELECT fn.id, fn.kind
  FROM semillas s
  JOIN cm.nodes fn ON fn.tenant_id = core.current_tenant() AND fn.id = s.id
 ORDER BY fn.id COLLATE "C"
"""

SQL_IMPACTO = _sql_recorrido("entrante")
SQL_DEPENDENCIAS = _sql_recorrido("saliente")
SQL_SUBGRAFO_NODOS = _sql_recorrido("ambas")

SQL_ARISTAS_ENTRE = """
SELECT ea.tipo, ea.src_kind, ea.src_id, ea.dst_kind, ea.dst_id
  FROM cm.edges ea
 WHERE ea.tenant_id = core.current_tenant()
   AND ea.src_id = ANY(CAST(:ids AS text[]))
   AND ea.dst_id = ANY(CAST(:ids AS text[]))
   AND ea.tipo = ANY(CAST(:tipos AS text[]))
 ORDER BY ea.src_id COLLATE "C", ea.tipo COLLATE "C", ea.dst_id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""

SQL_VECINOS = """
SELECT v.tipo, v.src_kind, v.src_id, v.dst_kind, v.dst_id
  FROM (
    SELECT vi.tipo, vi.src_kind, vi.src_id, vi.dst_kind, vi.dst_id
      FROM cm.edges vi
     WHERE CAST(:entrantes AS boolean)
       AND vi.tenant_id = core.current_tenant()
       AND vi.dst_id = CAST(:nodo AS text)
       AND vi.tipo = ANY(CAST(:tipos AS text[]))
    UNION ALL
    SELECT vo.tipo, vo.src_kind, vo.src_id, vo.dst_kind, vo.dst_id
      FROM cm.edges vo
     WHERE CAST(:salientes AS boolean)
       AND vo.tenant_id = core.current_tenant()
       AND vo.src_id = CAST(:nodo AS text)
       AND vo.tipo = ANY(CAST(:tipos AS text[]))
  ) v
 ORDER BY v.src_id COLLATE "C", v.tipo COLLATE "C", v.dst_id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""

SQL_DEPENDENCIAS_OCULTAS = f"""
WITH RECURSIVE {_SEMILLAS.strip()},
alcance (id, distancia) AS (
    SELECT s.id, 0 FROM semillas s
    UNION ALL
    SELECT ec.dst_id, a.distancia + 1
      FROM alcance a
      JOIN cm.edges ec
        ON ec.tenant_id = core.current_tenant()
       AND ec.src_id = a.id
       AND ec.tipo = 'CALLS'
     WHERE a.distancia < CAST(:k AS integer)
) CYCLE id SET es_ciclo USING ruta,
alcance_min AS (
    SELECT a.id, min(a.distancia) AS distancia
      FROM alcance a
     WHERE NOT a.es_ciclo
     GROUP BY a.id
),
tablas AS (
    SELECT eu.dst_id AS tabla_id, min(am.distancia) AS distancia
      FROM alcance_min am
      JOIN cm.edges eu
        ON eu.tenant_id = core.current_tenant()
       AND eu.src_id = am.id
       AND eu.tipo = 'USES_TABLE'
     GROUP BY eu.dst_id
),
acoplados AS (
    SELECT ex.src_id AS id, t.tabla_id, t.distancia
      FROM tablas t
      JOIN cm.edges ex
        ON ex.tenant_id = core.current_tenant()
       AND ex.dst_id = t.tabla_id
       AND ex.tipo = 'USES_TABLE'
     WHERE NOT EXISTS (SELECT 1 FROM alcance_min am2 WHERE am2.id = ex.src_id)
)
SELECT ac.id, nd.kind, array_agg(DISTINCT ac.tabla_id) AS tablas, min(ac.distancia) AS distancia
  FROM acoplados ac
  JOIN cm.nodes nd ON nd.tenant_id = core.current_tenant() AND nd.id = ac.id
 GROUP BY ac.id, nd.kind
 ORDER BY min(ac.distancia), ac.id COLLATE "C"
 LIMIT CAST(:limite AS integer)
"""

CONSULTAS: dict[str, str] = {
    "resolver_nodos": SQL_RESOLVER_NODOS,
    "semillas": SQL_SEMILLAS,
    "impacto": SQL_IMPACTO,
    "dependencias": SQL_DEPENDENCIAS,
    "subgrafo_nodos": SQL_SUBGRAFO_NODOS,
    "aristas_entre": SQL_ARISTAS_ENTRE,
    "vecinos": SQL_VECINOS,
    "dependencias_ocultas": SQL_DEPENDENCIAS_OCULTAS,
}


# ---------------------------------------------------------------------------
# Parámetros y construcción de respuestas (comunes a cualquier cliente)
# ---------------------------------------------------------------------------
def tipos_efectivos(consulta: ConsultaGrafoIn, direccion: Direccion) -> list[TipoArista]:
    """Tipos pedidos, o los de dependencia (impacto/dependencias) o todos (subgrafo)."""
    if consulta.tipos is not None:
        return sorted(set(consulta.tipos))
    if direccion == "ambas":
        return sorted(TipoArista)
    return sorted(TIPOS_DEPENDENCIA)


def parametros_recorrido(consulta: ConsultaGrafoIn, direccion: Direccion) -> dict[str, Any]:
    """Parámetros de SQL_IMPACTO, SQL_DEPENDENCIAS, SQL_SUBGRAFO_NODOS y de las ocultas."""
    return {
        "nodos": list(consulta.nodos),
        "ficheros": list(consulta.ficheros),
        "k": consulta.k,
        "tipos": [t.value for t in tipos_efectivos(consulta, direccion)],
        "limite": MAX_NODOS + 1,
    }


def parametros_vecinos(
    nodo: str, direccion: Direccion, tipos: Sequence[TipoArista | str] | None = None
) -> dict[str, Any]:
    elegidos = sorted({TipoArista(t) for t in tipos}) if tipos else sorted(TipoArista)
    return {
        "nodo": nodo,
        "entrantes": direccion in ("entrante", "ambas"),
        "salientes": direccion in ("saliente", "ambas"),
        "tipos": [t.value for t in elegidos],
        "limite": MAX_ARISTAS,
    }


def _nodo_alcanzado(f: Mapping[str, Any]) -> NodoAlcanzado:
    via = f["via_tipo"]
    return NodoAlcanzado(
        id=str(f["id"]),
        kind=str(f["kind"]),
        distancia=int(f["distancia"]),
        via_tipo=None if via is None else TipoArista(via),
    )


def aristas(filas: Iterable[Mapping[str, Any]]) -> list[Arista]:
    return [
        Arista(
            tipo=TipoArista(f["tipo"]),
            src_kind=str(f["src_kind"]),
            src_id=str(f["src_id"]),
            dst_kind=str(f["dst_kind"]),
            dst_id=str(f["dst_id"]),
        )
        for f in filas
    ]


def construir_recorrido(
    consulta: ConsultaGrafoIn, direccion: Direccion, filas: Sequence[Mapping[str, Any]]
) -> RecorridoOut:
    nodos = [_nodo_alcanzado(f) for f in filas[:MAX_NODOS]]
    return RecorridoOut(
        direccion=direccion,
        k=consulta.k,
        tipos=tipos_efectivos(consulta, direccion),
        semillas=[n.id for n in nodos if n.distancia == 0],
        nodos=nodos,
        truncado=len(filas) > MAX_NODOS,
    )


def construir_subgrafo(
    consulta: ConsultaGrafoIn,
    filas_nodos: Sequence[Mapping[str, Any]],
    filas_aristas: Sequence[Mapping[str, Any]],
) -> SubgrafoOut:
    recorrido = construir_recorrido(consulta, "ambas", filas_nodos)
    return SubgrafoOut(
        k=consulta.k,
        tipos=recorrido.tipos,
        semillas=recorrido.semillas,
        nodos=recorrido.nodos,
        aristas=aristas(filas_aristas[:MAX_ARISTAS]),
        truncado=recorrido.truncado or len(filas_aristas) > MAX_ARISTAS,
    )


def construir_ocultas(
    consulta: ConsultaGrafoIn,
    semillas: Sequence[str],
    filas: Sequence[Mapping[str, Any]],
) -> DependenciasOcultasOut:
    return DependenciasOcultasOut(
        k=consulta.k,
        semillas=list(semillas),
        dependencias=[
            DependenciaOculta(
                id=str(f["id"]),
                kind=str(f["kind"]),
                tablas=sorted(str(t) for t in f["tablas"]),
                distancia=int(f["distancia"]),
            )
            for f in filas[:MAX_NODOS]
        ],
        truncado=len(filas) > MAX_NODOS,
    )
