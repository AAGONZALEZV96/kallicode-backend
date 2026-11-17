"""Calidad semántica del grafo: precisión y exhaustividad sobre 50 preguntas (KC-CM-12-4).

El grafo puede estar completo y ser rápido y aun así responder mal. Este módulo construye el grafo
del repositorio de evaluación (`packages/codemapping/tests/evaluacion/repo_eval`) por la ruta real
del pipeline (parser, cargador, esquema y USES_TABLE, embeddings, cm.reachability) y le hace las
50 preguntas de `tests/evaluacion/preguntas.yaml`, cuya respuesta está escrita a mano:

- 40 de impacto: `impacto`, `dependencias` y `dependencias_ocultas` de cm_queries;
- 10 de búsqueda semántica: `buscar_por_texto` sobre la capa de símbolos.

Por pregunta: precisión = aciertos / devueltos y exhaustividad = aciertos / esperados. Los umbrales
del ticket (precisión > 0,85 y exhaustividad > 0,80) se aplican a la media macro de las 50. La
media micro y el desglose por operación son informativos. Convenciones de los casos vacíos: sin
esperados ni devueltos, 1 y 1; con esperados y nada devuelto, precisión 0; sin esperados y algo
devuelto, precisión 0 y exhaustividad 1.

Embebedor: `ngramas-hash-v1` de evaluacion_busqueda (determinista, sin red), porque la CI no tiene
TEI. Con `--tei` se mide con BGE-M3 (`make up`). La descripción de cada símbolo, que en producción
escribe el LLM (fase 4), sale de `repo_eval/descripciones.yaml`.

Uso (también lo lanza .github/workflows/calidad_semantica_nocturno.yml):

    uv run python -m kallicode_codemapping.evaluacion_calidad \
        --resultado resultados/calidad.json --serie resultados/serie_calidad.jsonl \
        --resumen-md resultados/resumen.md

Informe y casos fallidos: docs/calidad/informe-calidad-cm.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg
import yaml
from kallicode_core.cm_impacto import K_MAX, ConsultaGrafoIn, Direccion

from . import cm_queries
from .embeddings import (
    TEIEmbeddingClient,
    embeber_en_lotes,
    generar_uuid5_vector,
    texto_embedding_simbolo,
)
from .evaluacion_busqueda import EMBEBEDOR_REFERENCIA, Embebedor, EmbebedorReferencia
from .extract_sql import detectar_uso_tablas
from .graph_loader import GraphLoader, normalizar_dsn
from .graph_loader import clave_tabla as clave_tabla_cargador
from .ingest_repo import LANG_BY_EXT, SourceFile
from .parser_treesitter import Symbol, parse_file

RAIZ_REPO = Path(__file__).resolve().parents[4]
RUTA_EVALUACION = RAIZ_REPO / "packages" / "codemapping" / "tests" / "evaluacion"
RUTA_REPO_EVAL = RUTA_EVALUACION / "repo_eval"
RUTA_PREGUNTAS = RUTA_EVALUACION / "preguntas.yaml"
RUTA_ESQUEMA = RUTA_REPO_EVAL / "esquema.yaml"
RUTA_DESCRIPCIONES = RUTA_REPO_EVAL / "descripciones.yaml"
RUTA_LINEA_BASE = RAIZ_REPO / "docs" / "calidad" / "linea_base_calidad_cm.json"

OperacionGrafo = Literal["impacto", "dependencias", "dependencias_ocultas"]
Operacion = Literal["impacto", "dependencias", "dependencias_ocultas", "busqueda"]
OPERACIONES_GRAFO: tuple[OperacionGrafo, ...] = ("impacto", "dependencias", "dependencias_ocultas")
OPERACIONES: tuple[Operacion, ...] = (*OPERACIONES_GRAFO, "busqueda")
TIPOS_SEMILLA = ("nodo", "fichero", "tabla")
DIRECCION: dict[str, Direccion] = {"impacto": "entrante", "dependencias": "saliente"}

N_PREGUNTAS = 50
N_PREGUNTAS_GRAFO = 40
ANONIMO = "<anonymous>"
SEPARADOR = "::"
PREFIJO_TABLA = "tabla:"
TENANT_DEFECTO = "org_eval_calidad_cm"
RUTAS_IMPACTO = ("materializada", "recursiva")


# ---------------------------------------------------------------------------
# Claves legibles de los elementos del grafo
# ---------------------------------------------------------------------------
def clave_simbolo(ruta: str, nombre: str) -> str:
    return f"{ruta}{SEPARADOR}{nombre}"


def clave_tabla(esquema: str | None, nombre: str) -> str:
    return f"{PREFIJO_TABLA}{esquema or 'public'}.{nombre}"


def es_anonimo(clave: str) -> bool:
    return clave.endswith(f"{SEPARADOR}{ANONIMO}")


# ---------------------------------------------------------------------------
# Conjunto de evaluación
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Semilla:
    tipo: Literal["nodo", "fichero", "tabla"]
    valor: str

    @property
    def clave(self) -> str:
        """La clave del elemento de partida (las tablas llevan el prefijo `tabla:`)."""
        return f"{PREFIJO_TABLA}{self.valor}" if self.tipo == "tabla" else self.valor


@dataclass(frozen=True)
class Pregunta:
    id: str
    operacion: Operacion
    esperado: frozenset[str]
    justificacion: str
    semilla: Semilla | None = None
    k: int = 1
    texto: str | None = None


@dataclass(frozen=True)
class Conjunto:
    version: str
    umbral_precision: float
    umbral_exhaustividad: float
    preguntas: tuple[Pregunta, ...]

    def de_operacion(self, *operaciones: str) -> list[Pregunta]:
        return [p for p in self.preguntas if p.operacion in operaciones]


def _pregunta(datos: Mapping[str, Any]) -> Pregunta:
    pid = str(datos["id"])
    operacion = str(datos["operacion"])
    if operacion not in OPERACIONES:
        raise ValueError(f"{pid}: operación desconocida {operacion!r}")
    esperado_lista = [str(e) for e in datos.get("esperado") or []]
    if len(set(esperado_lista)) != len(esperado_lista):
        raise ValueError(f"{pid}: elementos repetidos en esperado")
    justificacion = str(datos.get("justificacion") or "").strip()
    if not justificacion:
        raise ValueError(f"{pid}: falta la justificación de la respuesta")
    if operacion == "busqueda":
        texto = str(datos.get("texto") or "").strip()
        if not texto or not esperado_lista:
            raise ValueError(f"{pid}: una búsqueda necesita texto y al menos un esperado")
        return Pregunta(
            id=pid,
            operacion="busqueda",
            esperado=frozenset(esperado_lista),
            justificacion=justificacion,
            k=len(esperado_lista),
            texto=texto,
        )
    semilla_datos = datos.get("semilla") or {}
    if len(semilla_datos) != 1 or next(iter(semilla_datos)) not in TIPOS_SEMILLA:
        raise ValueError(f"{pid}: la semilla es una de {TIPOS_SEMILLA}")
    tipo, valor = next(iter(semilla_datos.items()))
    k = int(datos.get("k", 0))
    if not 1 <= k <= K_MAX:
        raise ValueError(f"{pid}: k fuera de 1..{K_MAX}")
    return Pregunta(
        id=pid,
        operacion=operacion,  # type: ignore[arg-type]
        esperado=frozenset(esperado_lista),
        justificacion=justificacion,
        semilla=Semilla(tipo=tipo, valor=str(valor)),
        k=k,
    )


def cargar_conjunto(ruta: Path = RUTA_PREGUNTAS) -> Conjunto:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    preguntas = tuple(_pregunta(p) for p in datos["preguntas"])
    ids = [p.id for p in preguntas]
    if len(set(ids)) != len(ids):
        raise ValueError("ids de pregunta repetidos")
    return Conjunto(
        version=str(datos["version"]),
        umbral_precision=float(datos["umbrales"]["precision"]),
        umbral_exhaustividad=float(datos["umbrales"]["exhaustividad"]),
        preguntas=preguntas,
    )


# ---------------------------------------------------------------------------
# Repositorio de evaluación
# ---------------------------------------------------------------------------
def fuentes_repo(raiz: Path = RUTA_REPO_EVAL) -> list[SourceFile]:
    fuentes: list[SourceFile] = []
    for ruta in sorted(raiz.rglob("*")):
        if ruta.is_file() and ruta.suffix in LANG_BY_EXT:
            contenido = ruta.read_text(encoding="utf-8")
            fuentes.append(
                SourceFile(
                    path=ruta.relative_to(raiz).as_posix(),
                    language=LANG_BY_EXT[ruta.suffix],
                    sha256=hashlib.sha256(contenido.encode()).hexdigest(),
                    content=contenido,
                )
            )
    return fuentes


def cargar_esquema(ruta: Path = RUTA_ESQUEMA) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Tablas y FK con la forma de extract_schema (columnas como dicts)."""
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    tablas = [
        {
            "esquema": t["esquema"],
            "name": t["name"],
            "columns": [{"name": c, "type": "text", "nullable": True} for c in t["columns"]],
        }
        for t in datos["tablas"]
    ]
    return tablas, [dict(fk) for fk in datos["fks"]]


def cargar_descripciones(ruta: Path = RUTA_DESCRIPCIONES) -> dict[str, str]:
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in datos["descripciones"].items()}


@dataclass(frozen=True)
class Inventario:
    """Elementos del repositorio de evaluación tal como los ve una persona (no el grafo)."""

    simbolos: frozenset[str]
    ficheros: frozenset[str]
    tablas: frozenset[str]
    ambiguos: frozenset[str]

    def existe(self, clave: str) -> bool:
        return clave in self.simbolos or clave in self.ficheros or clave in self.tablas


def inventario(
    fuentes: Sequence[SourceFile], simbolos: Sequence[Symbol], tablas: Sequence[Mapping[str, Any]]
) -> Inventario:
    vistos: set[str] = set()
    ambiguos: set[str] = set()
    for s in simbolos:
        clave = clave_simbolo(s.file, s.name)
        (ambiguos if clave in vistos else vistos).add(clave)
    return Inventario(
        simbolos=frozenset(vistos),
        ficheros=frozenset(f.path for f in fuentes),
        tablas=frozenset(clave_tabla(t.get("esquema"), t["name"]) for t in tablas),
        ambiguos=frozenset(ambiguos),
    )


def errores_conjunto(conjunto: Conjunto, inv: Inventario) -> list[str]:
    """Todo lo que impide medir: claves inexistentes, ambiguas o anónimas, y tamaño del conjunto."""
    errores: list[str] = []
    if len(conjunto.preguntas) != N_PREGUNTAS:
        errores.append(f"el conjunto tiene {len(conjunto.preguntas)} preguntas, no {N_PREGUNTAS}")
    if len(conjunto.de_operacion(*OPERACIONES_GRAFO)) != N_PREGUNTAS_GRAFO:
        errores.append(f"hacen falta {N_PREGUNTAS_GRAFO} preguntas de impacto")
    for p in conjunto.preguntas:
        claves = set(p.esperado)
        if p.semilla is not None:
            claves.add(p.semilla.clave)
            if p.semilla.clave in p.esperado:
                errores.append(f"{p.id}: la semilla no se cuenta, no puede estar en esperado")
            if p.semilla.tipo == "fichero" and p.semilla.valor not in inv.ficheros:
                errores.append(f"{p.id}: la semilla {p.semilla.valor} no es un fichero")
        for clave in sorted(claves):
            if not inv.existe(clave):
                errores.append(f"{p.id}: {clave} no existe en repo_eval")
            elif clave in inv.ambiguos:
                errores.append(f"{p.id}: {clave} es ambigua (dos símbolos con ese nombre)")
            elif es_anonimo(clave):
                errores.append(f"{p.id}: {clave} es anónimo y no se puede citar")
    return errores


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
def precision_exhaustividad(
    devueltos: AbstractSet[str], esperado: AbstractSet[str]
) -> tuple[float, float]:
    aciertos = len(devueltos & esperado)
    if not devueltos:
        return (1.0, 1.0) if not esperado else (0.0, 0.0)
    return aciertos / len(devueltos), (aciertos / len(esperado) if esperado else 1.0)


@dataclass(frozen=True)
class ResultadoPregunta:
    id: str
    operacion: Operacion
    esperado: frozenset[str]
    devueltos: frozenset[str]
    precision: float
    exhaustividad: float

    @property
    def falsos_positivos(self) -> list[str]:
        return sorted(self.devueltos - self.esperado)

    @property
    def falsos_negativos(self) -> list[str]:
        return sorted(self.esperado - self.devueltos)

    @property
    def acierta(self) -> bool:
        return self.devueltos == self.esperado

    def como_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operacion": self.operacion,
            "precision": round(self.precision, 4),
            "exhaustividad": round(self.exhaustividad, 4),
            "esperados": len(self.esperado),
            "devueltos": len(self.devueltos),
            "falsos_positivos": self.falsos_positivos,
            "falsos_negativos": self.falsos_negativos,
        }


def resultado(pregunta: Pregunta, devueltos: Iterable[str]) -> ResultadoPregunta:
    # Los anónimos no se listan en la respuesta: se ignoran también en lo devuelto.
    dev = frozenset(d for d in devueltos if not es_anonimo(d))
    p, r = precision_exhaustividad(dev, pregunta.esperado)
    return ResultadoPregunta(pregunta.id, pregunta.operacion, pregunta.esperado, dev, p, r)


def _media(valores: Sequence[float]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def _agregado(resultados: Sequence[ResultadoPregunta]) -> dict[str, Any]:
    aciertos = sum(len(r.devueltos & r.esperado) for r in resultados)
    devueltos = sum(len(r.devueltos) for r in resultados)
    esperados = sum(len(r.esperado) for r in resultados)
    return {
        "preguntas": len(resultados),
        "precision": round(_media([r.precision for r in resultados]), 4),
        "exhaustividad": round(_media([r.exhaustividad for r in resultados]), 4),
        "precision_micro": round(aciertos / devueltos, 4) if devueltos else 0.0,
        "exhaustividad_micro": round(aciertos / esperados, 4) if esperados else 0.0,
    }


def resumir(resultados: Sequence[ResultadoPregunta], conjunto: Conjunto) -> dict[str, Any]:
    """Media macro global (la del criterio), micro, desglose por operación y preguntas fallidas."""
    resumen = _agregado(resultados)
    resumen["por_operacion"] = {
        op: _agregado([r for r in resultados if r.operacion == op])
        for op in OPERACIONES
        if any(r.operacion == op for r in resultados)
    }
    resumen["fallidas"] = [r.id for r in resultados if not r.acierta]
    resumen["umbrales"] = {
        "precision": conjunto.umbral_precision,
        "exhaustividad": conjunto.umbral_exhaustividad,
    }
    resumen["cumple"] = (
        resumen["precision"] > conjunto.umbral_precision
        and resumen["exhaustividad"] > conjunto.umbral_exhaustividad
    )
    return resumen


# ---------------------------------------------------------------------------
# Construcción del grafo y preguntas contra la base
# ---------------------------------------------------------------------------
@dataclass
class Indice:
    """Traducción entre los ids del grafo y las claves legibles de las preguntas."""

    clave_por_id: dict[str, str] = field(default_factory=dict)
    id_por_clave: dict[str, str] = field(default_factory=dict)
    estado_reachability: str | None = None

    def registrar(self, id_: str, clave: str) -> None:
        self.clave_por_id[id_] = clave
        self.id_por_clave[clave] = id_

    def clave(self, id_: str, kind: str = "?") -> str:
        return self.clave_por_id.get(id_, f"?{kind}:{id_}")


def construir_grafo(
    loader: GraphLoader,
    fuentes: list[SourceFile],
    tablas: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    descripciones: Mapping[str, str],
    embebedor: Embebedor,
) -> Indice:
    """Fases 1-3 y 6 del pipeline sobre repo_eval y refresco de cm.reachability."""
    indice = Indice()
    sha = hashlib.sha256("".join(f.sha256 for f in fuentes).encode()).hexdigest()[:40]
    snap = loader.start_snapshot(commit_sha=sha, rama="main")
    simbolos = [s for f in fuentes for s in parse_file(f)]
    file_ids = loader.load_files(snap, fuentes)
    loader.load_symbols(simbolos, file_ids)
    usage = detectar_uso_tablas(fuentes, tablas).usage
    tabla_ids = loader.load_schema(tablas, fks, usage, file_ids)

    for ruta, fid in file_ids.items():
        indice.registrar(fid, ruta)
    for t in tablas:
        clave = clave_tabla(t.get("esquema"), t["name"])
        indice.registrar(tabla_ids[clave_tabla_cargador(t.get("esquema"), t["name"])], clave)
    for s in simbolos:
        indice.registrar(s.id, clave_simbolo(s.file, s.name))

    # Fase 6: el texto canónico de cada símbolo, con la descripción del fixture en lugar del LLM.
    textos = [
        texto_embedding_simbolo(
            s.name, s.signature, descripciones.get(clave_simbolo(s.file, s.name))
        )
        for s in simbolos
    ]
    vectores = embebedor.embed_texts_sync(textos)
    loader.upsert_vectors(
        [
            {
                "id": str(generar_uuid5_vector(loader.tenant_id, "code", s.id)),
                "layer": "code",
                "node_ref": s.id,
                "text": texto,
                "vector": vector,
                "payload": {"name": s.name, "module": s.file.split("/", 1)[0]},
            }
            for s, texto, vector in zip(simbolos, textos, vectores, strict=True)
        ]
    )
    loader.complete_snapshot(snap)
    with loader.fase("reachability") as cur:
        cur.execute("SELECT estado FROM cm.refrescar_reachability()")
        fila = cur.fetchone()
    indice.estado_reachability = str(fila[0]) if fila else None
    return indice


def _consulta(pregunta: Pregunta, indice: Indice) -> ConsultaGrafoIn:
    semilla = pregunta.semilla
    if semilla is None:
        raise ValueError(f"{pregunta.id}: sin semilla")
    if semilla.tipo == "fichero":
        return ConsultaGrafoIn(ficheros=[semilla.valor], k=pregunta.k)
    id_ = indice.id_por_clave.get(semilla.clave)
    if id_ is None:
        raise ValueError(f"{pregunta.id}: la semilla {semilla.clave} no está en el grafo")
    return ConsultaGrafoIn(nodos=[id_], k=pregunta.k)


def responder(
    loader: GraphLoader,
    indice: Indice,
    pregunta: Pregunta,
    embebedor: Embebedor,
    ruta: str = "auto",
) -> list[str]:
    """Claves que devuelve el grafo para la pregunta, sin las semillas (distancia 0)."""
    if pregunta.operacion == "busqueda":
        salida = cm_queries.buscar_por_texto(
            loader, embebedor, pregunta.texto or "", capas=["symbol"], k=pregunta.k
        )
        return [indice.clave(r.referencia) for r in salida.resultados]
    consulta = _consulta(pregunta, indice)
    if pregunta.operacion == "dependencias_ocultas":
        ocultas = cm_queries.dependencias_ocultas(loader, consulta)
        return [indice.clave(d.id, d.kind) for d in ocultas.dependencias]
    recorrido = cm_queries._recorrer(
        loader, consulta, pregunta.operacion, DIRECCION[pregunta.operacion], ruta=ruta
    )
    return [indice.clave(n.id, n.kind) for n in recorrido.nodos if n.distancia > 0]


@dataclass(frozen=True)
class Evaluacion:
    resultados: list[ResultadoPregunta]
    resumen: dict[str, Any]


def evaluar(
    loader: GraphLoader, indice: Indice, conjunto: Conjunto, embebedor: Embebedor
) -> Evaluacion:
    """Las 50 preguntas; impacto y dependencias por las dos rutas de cm.impacto()."""
    resultados: list[ResultadoPregunta] = []
    discrepancias: list[str] = []
    for p in conjunto.preguntas:
        if p.operacion in DIRECCION:
            por_ruta = {r: responder(loader, indice, p, embebedor, r) for r in RUTAS_IMPACTO}
            if set(por_ruta["materializada"]) != set(por_ruta["recursiva"]):
                discrepancias.append(p.id)
            devueltos = por_ruta["materializada"]
        else:
            devueltos = responder(loader, indice, p, embebedor)
        resultados.append(resultado(p, devueltos))
    resumen = resumir(resultados, conjunto)
    resumen["rutas_discrepantes"] = discrepancias
    resumen["estado_reachability"] = indice.estado_reachability
    return Evaluacion(resultados, resumen)


# ---------------------------------------------------------------------------
# Tenant efímero
# ---------------------------------------------------------------------------
TABLAS_TENANT = (
    "cm.reachability",
    "cm.reachability_estado",
    "vec.definition_embeddings",
    "cm.edges",
    "cm.nodes",
    "cm.db_columns",
    "cm.db_tables",
    "cm.symbols",
    "cm.files",
    "cm.snapshot_vigente",
    "cm.repo_snapshots",
)


def limpiar_tenant(dsn_admin: str, tenant: str) -> None:
    with psycopg.connect(dsn_admin, autocommit=True) as conn:
        for tabla in TABLAS_TENANT:
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (tenant,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (tenant,))


@contextmanager
def tenant_efimero(dsn_admin: str, tenant: str) -> Iterator[str]:
    """Organización propia, vacía al empezar y borrada al terminar (también si falla)."""
    limpiar_tenant(dsn_admin, tenant)
    with psycopg.connect(dsn_admin, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Evaluación de calidad CM')",
            (tenant,),
        )
    try:
        yield tenant
    finally:
        limpiar_tenant(dsn_admin, tenant)


def dsn_app(dsn_admin: str) -> str:
    """El mismo servidor con el rol kallicode_app: las preguntas pasan por RLS, como en producción."""
    return dsn_admin + ("&" if "?" in dsn_admin else "?") + "options=-c%20role%3Dkallicode_app"


def ejecutar(
    dsn_admin: str,
    *,
    tenant: str = TENANT_DEFECTO,
    embebedor: Embebedor | None = None,
    conjunto: Conjunto | None = None,
) -> Evaluacion:
    conjunto = conjunto or cargar_conjunto()
    embebedor = embebedor or EmbebedorReferencia()
    fuentes = fuentes_repo()
    tablas, fks = cargar_esquema()
    errores = errores_conjunto(
        conjunto, inventario(fuentes, [s for f in fuentes for s in parse_file(f)], tablas)
    )
    if errores:
        raise ValueError("conjunto de evaluación no válido:\n" + "\n".join(errores))
    with (
        tenant_efimero(dsn_admin, tenant),
        GraphLoader(dsn=dsn_app(dsn_admin), tenant_id=tenant) as loader,
    ):
        indice = construir_grafo(loader, fuentes, tablas, fks, cargar_descripciones(), embebedor)
        return evaluar(loader, indice, conjunto, embebedor)


# ---------------------------------------------------------------------------
# Serie temporal e informe
# ---------------------------------------------------------------------------
def _sha_actual() -> str:
    if sha := os.getenv("GITHUB_SHA"):
        return sha
    try:
        salida = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=RAIZ_REPO,
        )
        return salida.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconocido"


def punto_serie(
    evaluacion: Evaluacion, conjunto: Conjunto, embebedor: str, ahora: datetime | None = None
) -> dict[str, Any]:
    r = evaluacion.resumen
    return {
        "fecha": (ahora or datetime.now(UTC)).isoformat(timespec="seconds"),
        "sha": _sha_actual(),
        "conjunto": conjunto.version,
        "embebedor": embebedor,
        "precision": r["precision"],
        "exhaustividad": r["exhaustividad"],
        "precision_micro": r["precision_micro"],
        "exhaustividad_micro": r["exhaustividad_micro"],
        "por_operacion": {
            op: {"precision": v["precision"], "exhaustividad": v["exhaustividad"]}
            for op, v in r["por_operacion"].items()
        },
        "fallidas": r["fallidas"],
        "cumple": r["cumple"],
    }


def anadir_a_serie(ruta: Path, punto: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Añade el punto al JSONL (lo crea si no existe) y devuelve la serie completa."""
    serie = leer_serie(ruta)
    serie.append(dict(punto))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        "".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in serie),
        encoding="utf-8",
    )
    return serie


def leer_serie(ruta: Path) -> list[dict[str, Any]]:
    if not ruta.exists():
        return []
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea]


def _cifra(valor: float) -> str:
    return f"{valor:.3f}".replace(".", ",")


def resumen_markdown(
    evaluacion: Evaluacion, serie: Sequence[Mapping[str, Any]], ultimos: int = 10
) -> str:
    r = evaluacion.resumen
    veredicto = "cumple" if r["cumple"] else "NO cumple"
    lineas = [
        "## Calidad semántica del grafo (KC-CM-12-4)",
        "",
        f"Precisión **{_cifra(r['precision'])}** (umbral > {_cifra(r['umbrales']['precision'])}) · "
        f"exhaustividad **{_cifra(r['exhaustividad'])}** "
        f"(umbral > {_cifra(r['umbrales']['exhaustividad'])}): **{veredicto}**.",
        "",
        "| Operación | Preguntas | Precisión | Exhaustividad |",
        "|---|---|---|---|",
    ]
    for op, v in r["por_operacion"].items():
        lineas.append(
            f"| {op} | {v['preguntas']} | {_cifra(v['precision'])} | {_cifra(v['exhaustividad'])} |"
        )
    if r["rutas_discrepantes"]:
        lineas += [
            "",
            f"Rutas de cm.impacto() que no coinciden: {', '.join(r['rutas_discrepantes'])}",
        ]
    fallidas = [x for x in evaluacion.resultados if not x.acierta]
    if fallidas:
        lineas += ["", "| Pregunta | P | E | Sobran | Faltan |", "|---|---|---|---|---|"]
        for x in fallidas:
            lineas.append(
                f"| {x.id} | {_cifra(x.precision)} | {_cifra(x.exhaustividad)} | "
                f"{', '.join(x.falsos_positivos) or '-'} | {', '.join(x.falsos_negativos) or '-'} |"
            )
    if serie:
        lineas += ["", "| Fecha | SHA | Precisión | Exhaustividad |", "|---|---|---|---|"]
        for p in list(serie)[-ultimos:]:
            lineas.append(
                f"| {p['fecha']} | {str(p['sha'])[:10]} | {_cifra(p['precision'])} | "
                f"{_cifra(p['exhaustividad'])} |"
            )
    return "\n".join(lineas) + "\n"


# ---------------------------------------------------------------------------
# Línea base versionada: ninguna pregunta puede empeorar sin que se vea
# ---------------------------------------------------------------------------
def _por_pregunta(resultados: Iterable[ResultadoPregunta]) -> dict[str, dict[str, float]]:
    return {
        r.id: {"precision": round(r.precision, 4), "exhaustividad": round(r.exhaustividad, 4)}
        for r in resultados
    }


def cargar_linea_base(ruta: Path = RUTA_LINEA_BASE) -> dict[str, Any]:
    datos: dict[str, Any] = json.loads(ruta.read_text(encoding="utf-8"))
    return datos


def actualizar_linea_base(
    evaluacion: Evaluacion, conjunto: Conjunto, embebedor: str, ruta: Path = RUTA_LINEA_BASE
) -> dict[str, Any]:
    """Las preguntas del grafo no dependen del embebedor; las de búsqueda se guardan por embebedor."""
    base = cargar_linea_base(ruta) if ruta.exists() else {}
    if base.get("conjunto") != conjunto.version:
        base = {}
    grafo = [r for r in evaluacion.resultados if r.operacion in OPERACIONES_GRAFO]
    busqueda = [r for r in evaluacion.resultados if r.operacion == "busqueda"]
    base = {
        "conjunto": conjunto.version,
        "descripcion": "Precisión y exhaustividad por pregunta (KC-CM-12-4). Regenerar solo "
        "cuando cambie a propósito el conjunto, el fixture o el pipeline: "
        "python -m kallicode_codemapping.evaluacion_calidad --actualizar-linea-base [--tei URL]",
        "grafo": _por_pregunta(grafo),
        "busqueda": {**base.get("busqueda", {}), embebedor: _por_pregunta(busqueda)},
    }
    ruta.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return base


def regresiones(evaluacion: Evaluacion, base: Mapping[str, Any], embebedor: str) -> list[str]:
    """Preguntas que bajan de su línea base (las de búsqueda, solo si hay base del embebedor)."""
    referencia: dict[str, dict[str, float]] = {
        **base.get("grafo", {}),
        **base.get("busqueda", {}).get(embebedor, {}),
    }
    peores: list[str] = []
    for r in evaluacion.resultados:
        ref = referencia.get(r.id)
        if ref is None:
            if r.operacion in OPERACIONES_GRAFO:
                peores.append(f"{r.id}: sin línea base")
            continue
        for metrica, valor in (("precision", r.precision), ("exhaustividad", r.exhaustividad)):
            if round(valor, 4) < ref[metrica]:
                peores.append(f"{r.id}: {metrica} {valor:.4f} < {ref[metrica]:.4f}")
    return peores


class ClienteTEI:
    """BGE-M3 vía TEI, por lotes (job nocturno o medición manual: la CI de PR no tiene TEI)."""

    def __init__(self, url: str) -> None:
        self._cliente = TEIEmbeddingClient(url)

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        return embeber_en_lotes(self._cliente, texts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dsn",
        default=os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or "postgresql://postgres:dev@localhost:5432/kallicode",
        help="DSN de superusuario (crea y borra un tenant efímero); las preguntas van como "
        "kallicode_app.",
    )
    parser.add_argument("--tenant", default=TENANT_DEFECTO)
    parser.add_argument("--tei", help="URL de TEI para medir con BGE-M3 en lugar de ngramas.")
    parser.add_argument("--resultado", type=Path, help="JSON con el resumen y cada pregunta.")
    parser.add_argument("--serie", type=Path, help="JSONL de la serie temporal (añade un punto).")
    parser.add_argument(
        "--resumen-md", type=Path, help="Resumen en Markdown (GITHUB_STEP_SUMMARY)."
    )
    parser.add_argument(
        "--actualizar-linea-base",
        action="store_true",
        help=f"Reescribe {RUTA_LINEA_BASE.relative_to(RAIZ_REPO).as_posix()} con esta medición.",
    )
    args = parser.parse_args(argv)

    embebedor: Embebedor = ClienteTEI(args.tei) if args.tei else EmbebedorReferencia()
    nombre_embebedor = "bge-m3" if args.tei else EMBEBEDOR_REFERENCIA
    conjunto = cargar_conjunto()
    evaluacion = ejecutar(
        normalizar_dsn(args.dsn), tenant=args.tenant, embebedor=embebedor, conjunto=conjunto
    )

    salida = {
        "conjunto": conjunto.version,
        "embebedor": nombre_embebedor,
        "resumen": evaluacion.resumen,
        "preguntas": [r.como_dict() for r in evaluacion.resultados],
    }
    texto = json.dumps(salida, ensure_ascii=False, indent=2)
    if args.resultado:
        args.resultado.parent.mkdir(parents=True, exist_ok=True)
        args.resultado.write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)
    serie: list[dict[str, Any]] = []
    if args.serie:
        serie = anadir_a_serie(args.serie, punto_serie(evaluacion, conjunto, nombre_embebedor))
    if args.resumen_md:
        args.resumen_md.parent.mkdir(parents=True, exist_ok=True)
        args.resumen_md.write_text(resumen_markdown(evaluacion, serie), encoding="utf-8")
    if args.actualizar_linea_base:
        actualizar_linea_base(evaluacion, conjunto, nombre_embebedor)
    return 0 if evaluacion.resumen["cumple"] else 1


if __name__ == "__main__":
    sys.exit(main())
