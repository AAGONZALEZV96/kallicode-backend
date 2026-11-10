"""Evaluación de la búsqueda híbrida: P@5 sobre veinte consultas de referencia (KC-CM-08-4).

Corpus y consultas: `packages/codemapping/tests/fixtures/busqueda/{corpus,consultas}.json`. Cada
elemento del corpus pertenece a un tema; una consulta de un tema considera relevantes los elementos
de su tema que cumplen sus filtros (capas, módulo) y la curación. La relevancia se deriva de esa
regla (no de listas escritas a mano) para que no pueda desalinearse del corpus.

Línea base versionada: `docs/benchmarks/busqueda_hibrida_p5_v1.json`. La prueba de integración
(`tests/test_busqueda.py`) mide P@5 con `cm.buscar_hibrido` contra PostgreSQL real y falla si baja
de ella. `ranking_referencia` es la misma semántica en Python con búsqueda EXACTA (sin índice): es
lo que genera la línea base y lo que permite medir otro embebedor sin base de datos.

Embebedores:
- `ngramas-hash-v1` (por defecto): determinista, sin red ni modelo; n-gramas de caracteres
  "hasheados" en 1024 dimensiones. Lo usa la CI, que no tiene TEI. Mide filtros, curación y orden
  de la búsqueda con una similitud léxica, no la calidad semántica de BGE-M3.
- `bge-m3`: el de producción, vía TEI (`--tei http://localhost:8080`, `make up`).

Regenerar la línea base (solo cuando cambie a propósito el corpus, las consultas o el embebedor):

    uv run python -m kallicode_codemapping.evaluacion_busqueda --actualizar
    uv run python -m kallicode_codemapping.evaluacion_busqueda --tei http://localhost:8080
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from .cm_queries import precision_en_k

RAIZ_REPO = Path(__file__).resolve().parents[4]
RUTA_FIXTURES = RAIZ_REPO / "packages" / "codemapping" / "tests" / "fixtures" / "busqueda"
RUTA_CORPUS = RUTA_FIXTURES / "corpus.json"
RUTA_CONSULTAS = RUTA_FIXTURES / "consultas.json"
RUTA_LINEA_BASE = RAIZ_REPO / "docs" / "benchmarks" / "busqueda_hibrida_p5_v1.json"

VERSION_LINEA_BASE = "busqueda-hibrida-p5-v1"
EMBEBEDOR_REFERENCIA = "ngramas-hash-v1"
DIMENSIONES = 1024
K_PRECISION = 5

CAPAS_DEFINICION = ("file", "symbol", "db_table", "db_column", "definition", "concept")
CAPAS = (*CAPAS_DEFINICION, "doc", "nugget")
CONCEPTO_CURADO = ("curado", "curador")
CONCEPTO_SIN_CURAR = ("sin_curar", "pendiente")
NUGGET_ESTADOS = ("aprobado", "propuesto", "rechazado")


# ---------------------------------------------------------------------------
# Embebedor determinista de referencia
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset(
    {
        "a",
        "al",
        "algo",
        "ante",
        "con",
        "cual",
        "cuales",
        "cuando",
        "de",
        "del",
        "desde",
        "donde",
        "dos",
        "el",
        "ella",
        "en",
        "entre",
        "era",
        "es",
        "esa",
        "ese",
        "eso",
        "esta",
        "este",
        "esto",
        "estos",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "muy",
        "nada",
        "ni",
        "no",
        "nos",
        "o",
        "otra",
        "otro",
        "para",
        "pero",
        "por",
        "que",
        "se",
        "ser",
        "si",
        "sin",
        "sobre",
        "solo",
        "son",
        "su",
        "sus",
        "tambien",
        "te",
        "tiene",
        "todo",
        "tras",
        "tu",
        "un",
        "una",
        "uno",
        "unos",
        "y",
        "ya",
        "the",
        "of",
        "and",
        "to",
        "in",
        "for",
        "is",
        "on",
        "with",
    }
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NO_ALFANUM = re.compile(r"[^a-z0-9]+")


def _sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def tokens(texto: str) -> list[str]:
    """Palabras normalizadas: sin acentos, en minúsculas, separando camelCase, snake_case y rutas."""
    plano = _sin_acentos(_CAMEL.sub(" ", texto)).lower()
    return [t for t in _NO_ALFANUM.split(plano) if len(t) > 2 and t not in _STOPWORDS]


def _rasgos(texto: str) -> Counter[str]:
    rasgos: Counter[str] = Counter()
    for t in tokens(texto):
        rasgos[f"w:{t}"] += 1
        marcado = f"#{t}#"
        for i in range(len(marcado) - 3):
            rasgos[f"g:{marcado[i : i + 4]}"] += 1
    return rasgos


def _a_float32(valores: Iterable[float]) -> list[float]:
    """Redondea a float4, la precisión con la que pgvector guarda cada componente."""
    lista = list(valores)
    return list(struct.unpack(f"<{len(lista)}f", struct.pack(f"<{len(lista)}f", *lista)))


def embeber_referencia(texto: str, dimensiones: int = DIMENSIONES) -> list[float]:
    """Vector unitario determinista (feature hashing con signo de palabras y 4-gramas)."""
    vector = [0.0] * dimensiones
    for rasgo, cuenta in _rasgos(texto).items():
        h = int.from_bytes(hashlib.blake2b(rasgo.encode("utf-8"), digest_size=8).digest(), "little")
        peso = (1.0 if rasgo.startswith("w:") else 0.5) * (1.0 + math.log(cuenta))
        vector[h % dimensiones] += peso if (h >> 32) & 1 else -peso
    norma = math.sqrt(sum(x * x for x in vector))
    if norma == 0:
        # Texto sin rasgos: un vector fijo (pgvector no admite el coseno de un vector nulo).
        vector[0] = 1.0
        norma = 1.0
    return _a_float32(x / norma for x in vector)


class EmbebedorReferencia:
    """Mismo protocolo que TEIEmbeddingClient (cm_queries.Embebedor), sin red."""

    nombre = EMBEBEDOR_REFERENCIA

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        return [embeber_referencia(t) for t in texts]


class Embebedor(Protocol):
    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# Corpus y consultas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Elemento:
    id: str
    tema: str
    capa: str
    texto: str
    titulo: str = ""
    modulo: str = ""
    estado: str | None = None
    fichero: str | None = None  # símbolo -> id del fichero
    enlaces: tuple[str, ...] = ()  # doc -> nodos que describe
    chunk: str | None = None  # nugget -> chunk de evidencia

    @property
    def texto_embebible(self) -> str:
        """Lo que se embebe: título y texto (para un nugget, el de su chunk; ver vector_de)."""
        return f"{self.titulo}. {self.texto}" if self.titulo else self.texto


@dataclass(frozen=True)
class Consulta:
    id: str
    tema: str
    texto: str
    capas: tuple[str, ...] | None = None
    modulo: str | None = None
    incluir_sin_curar: bool = False


@dataclass
class Corpus:
    version: str
    elementos: dict[str, Elemento] = field(default_factory=dict)

    def de_capa(self, *capas: str) -> list[Elemento]:
        return [e for e in self.elementos.values() if e.capa in capas]


def _validar_elemento(e: Elemento, ids: Mapping[str, Elemento]) -> None:
    if e.capa not in CAPAS:
        raise ValueError(f"{e.id}: capa desconocida {e.capa}")
    if e.capa == "concept" and e.estado not in (*CONCEPTO_CURADO, *CONCEPTO_SIN_CURAR):
        raise ValueError(f"{e.id}: estado de concepto inválido {e.estado}")
    if e.capa == "nugget":
        if e.estado not in NUGGET_ESTADOS:
            raise ValueError(f"{e.id}: estado de nugget inválido {e.estado}")
        if e.chunk is None or ids.get(e.chunk) is None or ids[e.chunk].capa != "doc":
            raise ValueError(f"{e.id}: el nugget debe citar un chunk doc del corpus")
    if e.capa == "symbol" and (e.fichero is None or ids.get(e.fichero) is None):
        raise ValueError(f"{e.id}: el símbolo debe declarar su fichero")
    for nodo in e.enlaces:
        if nodo not in ids or ids[nodo].capa not in CAPAS_DEFINICION:
            raise ValueError(f"{e.id}: enlace a un nodo inexistente {nodo}")


def cargar_corpus(ruta: Path = RUTA_CORPUS) -> Corpus:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    corpus = Corpus(version=str(datos["version"]))
    for tema, bloque in datos["temas"].items():
        for crudo in bloque["elementos"]:
            if crudo["id"] in corpus.elementos:
                raise ValueError(f"id repetido en el corpus: {crudo['id']}")
            corpus.elementos[crudo["id"]] = Elemento(
                id=crudo["id"],
                tema=tema,
                capa=crudo["capa"],
                texto=crudo["texto"],
                titulo=crudo.get("titulo", ""),
                modulo=bloque["modulo"],
                estado=crudo.get("estado"),
                fichero=crudo.get("fichero"),
                enlaces=tuple(crudo.get("enlaces", ())),
                chunk=crudo.get("chunk"),
            )
    for e in corpus.elementos.values():
        _validar_elemento(e, corpus.elementos)
    return corpus


def cargar_consultas(ruta: Path = RUTA_CONSULTAS) -> list[Consulta]:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    consultas = [
        Consulta(
            id=c["id"],
            tema=c["tema"],
            texto=c["texto"],
            capas=tuple(c["capas"]) if c.get("capas") else None,
            modulo=c.get("modulo"),
            incluir_sin_curar=bool(c.get("incluir_sin_curar", False)),
        )
        for c in datos["consultas"]
    ]
    if len({c.id for c in consultas}) != len(consultas):
        raise ValueError("ids de consulta repetidos")
    return consultas


# ---------------------------------------------------------------------------
# Semántica de cm.buscar_hibrido sobre el corpus (filtros y curación)
# ---------------------------------------------------------------------------
def _en_modulo(ruta: str, modulo: str) -> bool:
    return ruta == modulo or ruta.startswith(modulo + "/")


def _ruta_de_nodo(corpus: Corpus, nodo_id: str) -> str:
    """Ruta con la que un nodo cuenta para el filtro de módulo de un chunk enlazado."""
    nodo = corpus.elementos[nodo_id]
    if nodo.capa == "file":
        return nodo.titulo
    if nodo.capa == "symbol" and nodo.fichero:
        return corpus.elementos[nodo.fichero].titulo
    if nodo.capa == "definition":
        return nodo.modulo
    return ""  # tablas, columnas y conceptos no ubican un chunk en un módulo


def chunk_en_modulo(corpus: Corpus, chunk: Elemento, modulo: str) -> bool:
    return any(_en_modulo(_ruta_de_nodo(corpus, n), modulo) for n in chunk.enlaces)


def es_elegible(corpus: Corpus, e: Elemento, consulta: Consulta) -> bool:
    """¿Puede cm.buscar_hibrido devolver `e` para `consulta`? (capa, módulo, curación)."""
    capas = consulta.capas or CAPAS
    if e.capa not in capas:
        return False
    if e.capa == "concept" and e.estado in CONCEPTO_SIN_CURAR and not consulta.incluir_sin_curar:
        return False
    if e.capa == "nugget":
        if e.estado == "rechazado":
            return False
        if e.estado == "propuesto" and not consulta.incluir_sin_curar:
            return False
    if consulta.modulo is None:
        return True
    if e.capa in CAPAS_DEFINICION:
        return _en_modulo(e.modulo, consulta.modulo)
    chunk = e if e.capa == "doc" else corpus.elementos[str(e.chunk)]
    return chunk_en_modulo(corpus, chunk, consulta.modulo)


def relevantes(corpus: Corpus, consulta: Consulta) -> set[str]:
    """Elementos del tema de la consulta que la búsqueda puede devolver."""
    return {
        e.id
        for e in corpus.elementos.values()
        if e.tema == consulta.tema and es_elegible(corpus, e, consulta)
    }


def vectores_corpus(corpus: Corpus, embebedor: Embebedor) -> dict[str, list[float]]:
    """Vector de cada elemento embebible (un nugget no tiene: usa el de su chunk)."""
    propios = [e for e in corpus.elementos.values() if e.capa != "nugget"]
    vectores = embebedor.embed_texts_sync([e.texto_embebible for e in propios])
    return {e.id: v for e, v in zip(propios, vectores, strict=True)}


def vector_de(corpus: Corpus, vectores: Mapping[str, list[float]], e: Elemento) -> list[float]:
    return vectores[str(e.chunk) if e.capa == "nugget" else e.id]


def coseno(a: Sequence[float], b: Sequence[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb) if na and nb else 0.0


def ranking_referencia(
    corpus: Corpus,
    vectores: Mapping[str, list[float]],
    consulta: Consulta,
    vector_consulta: Sequence[float],
    k: int = K_PRECISION,
) -> list[str]:
    """Top-k EXACTO con la semántica de cm.buscar_hibrido: (distancia, capa, id)."""
    puntuados = [
        (1.0 - coseno(vector_consulta, vector_de(corpus, vectores, e)), e.capa, e.id)
        for e in corpus.elementos.values()
        if es_elegible(corpus, e, consulta)
    ]
    puntuados.sort()
    return [i for _, _, i in puntuados[:k]]


# ---------------------------------------------------------------------------
# P@5 y línea base
# ---------------------------------------------------------------------------
Buscador = Callable[[Consulta, list[float]], list[str]]


def medir_precision(
    corpus: Corpus,
    consultas: Sequence[Consulta],
    vectores_consulta: Mapping[str, list[float]],
    buscar: Buscador,
    k: int = K_PRECISION,
) -> dict[str, Any]:
    """P@k por consulta y media, con lo que devolvió `buscar` (ids en orden)."""
    por_consulta: dict[str, dict[str, Any]] = {}
    for c in consultas:
        devueltos = buscar(c, vectores_consulta[c.id])[:k]
        rel = relevantes(corpus, c)
        por_consulta[c.id] = {
            "p_at_5": round(precision_en_k(devueltos, rel, k), 4),
            # Con menos de k relevantes ni una búsqueda perfecta llega a 1.
            "p_at_5_maxima": round(min(len(rel), k) / k, 4),
            "relevantes": len(rel),
            "devueltos": devueltos,
        }
    n = len(por_consulta)
    return {
        "p_at_5_media": round(sum(v["p_at_5"] for v in por_consulta.values()) / n, 4),
        "p_at_5_maxima_media": round(sum(v["p_at_5_maxima"] for v in por_consulta.values()) / n, 4),
        "consultas": por_consulta,
    }


def generar_linea_base(
    corpus: Corpus,
    consultas: Sequence[Consulta],
    embebedor: Embebedor,
    nombre_embebedor: str,
    fecha: date | None = None,
) -> dict[str, Any]:
    vectores = vectores_corpus(corpus, embebedor)
    vq = dict(
        zip(
            [c.id for c in consultas],
            embebedor.embed_texts_sync([c.texto for c in consultas]),
            strict=True,
        )
    )
    medicion = medir_precision(
        corpus,
        consultas,
        vq,
        lambda c, v: ranking_referencia(corpus, vectores, c, v),
    )
    return {
        "version": VERSION_LINEA_BASE,
        "ticket": "KC-CM-08-4",
        "fecha": (fecha or date.today()).isoformat(),
        "metrica": "P@5 = relevantes entre los 5 primeros devueltos / 5 (un hueco cuenta como fallo)",
        "embebedor": nombre_embebedor,
        "corpus": corpus.version,
        "consultas_version": "consultas-busqueda-v1",
        "n_consultas": len(consultas),
        "metodo": "ranking exacto con la semántica de cm.buscar_hibrido (filtros y curación)",
        **medicion,
    }


def cargar_linea_base(ruta: Path = RUTA_LINEA_BASE) -> dict[str, Any]:
    datos: dict[str, Any] = json.loads(ruta.read_text(encoding="utf-8"))
    return datos


class _ClienteTEI:
    def __init__(self, url: str) -> None:
        from .embeddings import TEIEmbeddingClient, embeber_en_lotes

        self._cliente = TEIEmbeddingClient(url)
        self._embeber = embeber_en_lotes

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        return self._embeber(self._cliente, texts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--tei", help="URL de TEI para medir con BGE-M3 (por defecto, ngramas)")
    parser.add_argument(
        "--actualizar",
        action="store_true",
        help="Escribe la línea base en docs/benchmarks/busqueda_hibrida_p5_v1.json",
    )
    args = parser.parse_args(argv)
    embebedor: Embebedor = _ClienteTEI(args.tei) if args.tei else EmbebedorReferencia()
    nombre = "bge-m3" if args.tei else EMBEBEDOR_REFERENCIA
    linea = generar_linea_base(cargar_corpus(), cargar_consultas(), embebedor, nombre)
    texto = json.dumps(linea, ensure_ascii=False, indent=2) + "\n"
    if args.actualizar:
        RUTA_LINEA_BASE.write_text(texto, encoding="utf-8", newline="\n")
    sys.stdout.write(texto if not args.actualizar else f"P@5 media = {linea['p_at_5_media']}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
