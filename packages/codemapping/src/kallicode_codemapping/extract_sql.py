"""Fase 3 - capa de DATOS: esquema de la base del cliente + uso desde codigo.

Dos fuentes deterministas: information_schema (tablas, columnas, FKs)
y el propio codigo (referencias a tablas en SQL embebido). El resultado
son los nodos Table/Column y las aristas USES_TABLE del grafo.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.exc import DBAPIError

from .config import Settings
from .detectores_orm import DETECTORES, Modelo, analizar_archivo, lower_camel
from .ingest_repo import SourceFile
from .logging_setup import get_logger

logger = get_logger()

# SQLSTATE 57014 = query_canceled (lo que lanza Postgres al vencer statement_timeout)
_SQLSTATE_QUERY_CANCELED = "57014"


class SchemaExtractionTimeout(Exception):
    """Una consulta contra la base del cliente supero el tiempo limite (CM-F13)."""


def build_client_engine(settings: Settings) -> Engine:
    """Engine contra la base DEL CLIENTE con salvaguardas (CM-F13).

    Solo lectura a nivel de sesion (default_transaction_read_only), tiempo
    limite por sentencia y pool acotado: nunca mas de client_db_max_connections
    conexiones abiertas contra una base productiva ajena.
    """
    if settings.database_url is None:
        raise ValueError("database_url (base del cliente) no configurada")
    url = str(settings.database_url)
    max_conn = settings.client_db_max_connections
    options = (
        f"-c statement_timeout={settings.client_db_statement_timeout_ms} "
        "-c default_transaction_read_only=on"
    )
    return create_engine(
        url,
        pool_size=max_conn,
        max_overflow=0,
        pool_pre_ping=True,
        connect_args={"options": options},
    )


def extract_schema(
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    engine = build_client_engine(settings)
    logger.info(
        "extraccion de esquema del cliente: conexiones maximas acotadas",
        extra={
            "tenant_id": settings.tenant_id,
            "phase": "3/7",
            "max_connections": settings.client_db_max_connections,
            "statement_timeout_ms": settings.client_db_statement_timeout_ms,
        },
    )
    try:
        with engine.connect() as conn:
            return _read_schemas(inspect(conn), set(settings.client_db_exclude_schemas))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _SQLSTATE_QUERY_CANCELED:
            raise SchemaExtractionTimeout(
                f"la extraccion excedio {settings.client_db_statement_timeout_ms} ms"
            ) from exc
        raise
    finally:
        engine.dispose()


def _read_schemas(
    insp: Any, excluded: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tables: list[dict[str, Any]] = []
    fks: list[dict[str, Any]] = []
    for schema in insp.get_schema_names():
        if schema in excluded:
            continue
        for table in insp.get_table_names(schema=schema):
            cols = [
                {"name": c["name"], "type": str(c["type"]), "nullable": bool(c["nullable"])}
                for c in insp.get_columns(table, schema=schema)
            ]
            tables.append({"name": table, "esquema": schema, "columns": cols})
            for fk in insp.get_foreign_keys(table, schema=schema):
                fks.append(
                    {
                        "from_table": table,
                        "from_schema": schema,
                        "from_cols": fk["constrained_columns"],
                        "to_table": fk["referred_table"],
                        "to_schema": fk.get("referred_schema") or schema,
                        "to_cols": fk["referred_columns"],
                    }
                )
    return tables, fks


# --------------------------------------------------------------------------
# Uso de tablas desde el codigo: aristas USES_TABLE (KC-CM-09-3, CM-F17)
# --------------------------------------------------------------------------
# Antes: una regex con ventana de 400 caracteres que se cortaba en ; " ', sin CTE, alias
# ni esquema y ciega a los ORM. Ahora el SQL sale de los literales del AST (detectores_orm)
# y se tokeniza; los ORM aportan modelos y usos; todo se resuelve contra el esquema
# extraido de la base del cliente. Regla: ninguna tabla no confirmada entra al grafo.

_TOKEN = re.compile(
    r"""
    (?P<comentario>--[^\n]*|/\*.*?\*/)
  | (?P<literal>'(?:[^']|'')*')
  | (?P<citado>"(?:[^"]|"")+"|`[^`]+`|\[[^\]\s][^\]]*\])
  | (?P<ident>[^\W\d]\w*)
  | (?P<punto>[.,();])
  | (?P<otro>\S)
    """,
    re.DOTALL | re.VERBOSE,
)

_PALABRAS_CLAVE = frozenset(
    """
    all and any as asc at between by case cross default delete desc distinct do else end
    except exists fetch for from full group having ilike in inner insert intersect into is
    join lateral left like limit materialized merge natural not nothing null offset on only
    or order outer over recursive returning right select set similar straight_join table
    then to truncate union update using values when where window with matched conflict
    """.split()  # noqa: SIM905 (lista de palabras legible)
)
# Palabras que cierran la lista de tablas de un FROM (a la misma profundidad).
_FIN_FROM = frozenset(
    """
    where group order having limit offset union intersect except window returning set on
    using join inner left right full cross natural fetch for values select into
    """.split()  # noqa: SIM905 (lista de palabras legible)
)
_ANTES_DE_INTO = frozenset({"insert", "merge", "replace", "ignore"})
_NO_ES_UPDATE = frozenset({"for", "do", "key"})  # FOR UPDATE, DO UPDATE, FOR NO KEY UPDATE
# Funciones cuyo argumento lleva FROM sin ser una tabla: EXTRACT(YEAR FROM x), TRIM(... FROM x).
_FUNCIONES_CON_FROM = frozenset({"extract", "substring", "trim", "position", "overlay"})


@dataclass(frozen=True)
class TablaRef:
    """Tabla referenciada en SQL; ``esquema`` es None si el nombre no está cualificado."""

    esquema: str | None
    nombre: str


@dataclass(frozen=True)
class _Tok:
    tipo: str  # id | p | x
    valor: str  # en minúsculas (los identificadores se comparan sin distinguir caja)
    citado: bool = False


def _tokenizar(sql: str) -> list[_Tok]:
    toks: list[_Tok] = []
    for m in _TOKEN.finditer(sql):
        tipo = m.lastgroup
        valor = m.group(0)
        if tipo in ("comentario", "literal"):
            continue
        if tipo == "citado":
            toks.append(_Tok("id", valor[1:-1].replace('""', '"').lower(), citado=True))
        elif tipo == "ident":
            toks.append(_Tok("id", valor.lower()))
        elif tipo == "punto":
            toks.append(_Tok("p", valor))
        else:
            toks.append(_Tok("x", valor))
    return toks


def _es_id(tok: _Tok | None) -> bool:
    return tok is not None and tok.tipo == "id" and (tok.citado or tok.valor not in _PALABRAS_CLAVE)


def _sentencias(toks: list[_Tok]) -> list[list[_Tok]]:
    sentencias: list[list[_Tok]] = [[]]
    for t in toks:
        if t.tipo == "p" and t.valor == ";":
            sentencias.append([])
        else:
            sentencias[-1].append(t)
    return [s for s in sentencias if s]


def _nombre_cualificado(toks: list[_Tok], i: int) -> tuple[list[str], int]:
    """Lee ``a.b.c`` desde i; devuelve las partes y el índice siguiente."""
    partes = [toks[i].valor]
    i += 1
    while (
        i + 1 < len(toks)
        and toks[i].tipo == "p"
        and toks[i].valor == "."
        and toks[i + 1].tipo == "id"
    ):
        partes.append(toks[i + 1].valor)
        i += 2
    return partes, i


def _tablas_de_sentencia(toks: list[_Tok]) -> list[TablaRef]:
    def tok(j: int) -> _Tok | None:
        return toks[j] if 0 <= j < len(toks) else None

    def kw(j: int, *palabras: str) -> bool:
        t = tok(j)
        return t is not None and t.tipo == "id" and not t.citado and t.valor in palabras

    # 1) Nombres de CTE: WITH [RECURSIVE] x [(cols)] AS [NOT] [MATERIALIZED] ( ... ), y AS (...)
    ctes: set[str] = set()
    for i, t in enumerate(toks):
        if not (t.tipo == "id" and (kw(i - 1, "with", "recursive") or _es_coma(tok(i - 1)))):
            continue
        j = i + 1
        if _es_parentesis(tok(j), "("):
            j = _cierre(toks, j) + 1
        if not kw(j, "as"):
            continue
        j += 1
        if kw(j, "not"):
            j += 1
        if kw(j, "materialized"):
            j += 1
        if _es_parentesis(tok(j), "("):
            ctes.add(t.valor)

    # 2) Referencias a tablas con su alias. Los destinos de UPDATE/DELETE pueden ser alias
    #    (UPDATE c SET ... FROM app.customers c; DELETE o FROM orders o JOIN ...).
    refs: list[tuple[list[str], str | None]] = []  # (partes, alias)
    destinos: list[str] = []  # destinos sin cualificar, a resolver por alias
    profundidad = 0
    from_en: int | None = None  # profundidad del FROM cuya lista de tablas está abierta
    aperturas: list[str | None] = []  # identificador que precede a cada "(" abierto

    def leer_tabla(j: int, admite_funcion: bool) -> int:
        """Lee una tabla en j (si la hay) con su alias; devuelve el índice siguiente."""
        while kw(j, "only", "lateral"):
            j += 1
        if not _es_id(tok(j)):
            return j
        partes, k = _nombre_cualificado(toks, j)
        if admite_funcion and _es_parentesis(tok(k), "("):
            return k  # FROM generate_series(...), unnest(...): función, no tabla
        alias: str | None = None
        if kw(k, "as") and _es_id(tok(k + 1)):
            alias, k = toks[k + 1].valor, k + 2
        elif _es_id(tok(k)):
            alias, k = toks[k].valor, k + 1
        refs.append((partes, alias))
        return k

    i = 0
    while i < len(toks):
        t = toks[i]
        if t.tipo == "p" and t.valor == "(":
            profundidad += 1
            previo = tok(i - 1)
            aperturas.append(previo.valor if previo is not None and previo.tipo == "id" else None)
        elif t.tipo == "p" and t.valor == ")":
            profundidad -= 1
            if aperturas:
                aperturas.pop()
            if from_en is not None and profundidad < from_en:
                from_en = None
        elif t.tipo == "p" and t.valor == "," and from_en == profundidad:
            i = leer_tabla(i + 1, admite_funcion=True)
            continue
        elif t.tipo == "id" and not t.citado:
            v = t.valor
            if from_en == profundidad and v in _FIN_FROM:
                from_en = None
            if v == "from" and (
                kw(i - 1, "distinct") or (aperturas and aperturas[-1] in _FUNCIONES_CON_FROM)
            ):
                i += 1  # IS DISTINCT FROM x, EXTRACT(YEAR FROM x): no es una tabla
                continue
            if v == "from":
                from_en = profundidad
                i = leer_tabla(i + 1, admite_funcion=True)
                continue
            if v == "join" or (v == "using" and _es_id(tok(i + 1))):
                i = leer_tabla(i + 1, admite_funcion=True)
                continue
            if v == "into" and kw(i - 1, *_ANTES_DE_INTO):
                i = leer_tabla(i + 1, admite_funcion=False)
                continue
            if v == "truncate":
                j = i + 2 if kw(i + 1, "table") else i + 1
                i = leer_tabla(j, admite_funcion=False)
                continue
            if v == "update" and not kw(i - 1, *_NO_ES_UPDATE) and _es_id(tok(i + 1)):
                antes = len(refs)
                i = leer_tabla(i + 1, admite_funcion=False)
                if len(refs) > antes and len(refs[-1][0]) == 1 and refs[-1][1] is None:
                    destinos.append(refs.pop()[0][0])
                continue
            if v == "delete" and _es_id(tok(i + 1)):
                destinos.append(toks[i + 1].valor)  # DELETE alias FROM ... (MySQL/T-SQL)
                i += 2
                continue
        i += 1

    alias_de = {alias: partes for partes, alias in refs if alias is not None}
    resultado: list[TablaRef] = []
    for nombre in destinos:
        if nombre in alias_de:
            continue  # el alias ya está en refs con su tabla
        refs.append(([nombre], None))
    for partes, _alias in refs:
        if len(partes) == 1 and partes[0] in ctes:
            continue
        esquema = partes[-2] if len(partes) >= 2 else None
        resultado.append(TablaRef(esquema=esquema, nombre=partes[-1]))
    return resultado


def _es_coma(t: _Tok | None) -> bool:
    return t is not None and t.tipo == "p" and t.valor == ","


def _es_parentesis(t: _Tok | None, valor: str) -> bool:
    return t is not None and t.tipo == "p" and t.valor == valor


def _cierre(toks: list[_Tok], i: int) -> int:
    """Índice del paréntesis que cierra el abierto en i (o el último token)."""
    nivel = 0
    for j in range(i, len(toks)):
        if _es_parentesis(toks[j], "("):
            nivel += 1
        elif _es_parentesis(toks[j], ")"):
            nivel -= 1
            if nivel == 0:
                return j
    return len(toks) - 1


def extraer_tablas_sql(sql: str) -> list[TablaRef]:
    """Tablas que lee o escribe un texto SQL (una o varias sentencias separadas por ;).

    Excluye los nombres de CTE de su sentencia (una CTE sombrea a la tabla del mismo
    nombre), las funciones en FROM y los alias; resuelve los destinos de UPDATE/DELETE
    que son alias. Las partes ``esquema.tabla`` se conservan (``db.esquema.tabla`` -> las
    dos últimas). El orden es el de aparición y puede haber repetidos.
    """
    tablas: list[TablaRef] = []
    for sentencia in _sentencias(_tokenizar(sql)):
        tablas.extend(_tablas_de_sentencia(sentencia))
    return tablas


class IndiceTablas:
    """Tablas confirmadas por el esquema del cliente, resolubles con o sin esquema."""

    def __init__(self, known_tables: list[dict[str, Any]]) -> None:
        self._pares = {
            (str(t.get("esquema") or "public").lower(), str(t["name"]).lower())
            for t in known_tables
        }
        self._por_nombre: dict[str, set[str]] = defaultdict(set)
        for esquema, nombre in self._pares:
            self._por_nombre[nombre].add(esquema)

    def resolver(self, esquema: str | None, nombre: str) -> tuple[str, str] | None:
        """(esquema, tabla) confirmada o None.

        Sin esquema: la única tabla con ese nombre; si hay varias, la de ``public`` (el
        search_path por defecto); si ninguna está en public, es ambigua y no se resuelve.
        """
        nombre = nombre.lower()
        if esquema is not None:
            par = (esquema.lower(), nombre)
            return par if par in self._pares else None
        esquemas = self._por_nombre.get(nombre, set())
        if len(esquemas) == 1:
            return next(iter(esquemas)), nombre
        if "public" in esquemas:
            return "public", nombre
        return None


@dataclass
class ResultadoUsoTablas:
    """Aristas USES_TABLE (archivo -> tabla confirmada) y métrica de cobertura."""

    usage: list[dict[str, str]]
    # detector -> [accesos detectados, accesos resueltos a una tabla confirmada]
    por_detector: dict[str, list[int]] = field(default_factory=dict)

    def stats(self) -> dict[str, Any]:
        """Bloque ``fase_3.uses_table`` del contrato ingesta-stats-v1 (>= 1.1.0)."""
        return stats_uso_tablas(self.usage, self.por_detector)


def stats_uso_tablas(
    usage: list[dict[str, str]], por_detector: dict[str, list[int]] | None = None
) -> dict[str, Any]:
    por_detector = por_detector or {}
    detectados = sum(d for d, _r in por_detector.values())
    resueltos = sum(r for _d, r in por_detector.values())
    return {
        "aristas": len(usage),
        "accesos_detectados": detectados,
        "accesos_resueltos": resueltos,
        "cobertura": round(resueltos / detectados, 4) if detectados else None,
        "por_detector": {
            det: {"detectados": por_detector[det][0], "resueltos": por_detector[det][1]}
            for det in DETECTORES
            if det in por_detector
        },
    }


def _resolver_modelo(indice: IndiceTablas, modelo: Modelo) -> tuple[str, str] | None:
    for candidato in modelo.candidatos:
        par = indice.resolver(modelo.esquema, candidato)
        if par is not None:
            return par
    return None


def detectar_uso_tablas(
    files: list[SourceFile], known_tables: list[dict[str, Any]]
) -> ResultadoUsoTablas:
    """Aristas USES_TABLE de SQL crudo y de los ORM, solo hacia tablas del esquema.

    Un *acceso* es una referencia distinta dentro de un archivo: una tabla en SQL, la
    declaración de un modelo o un uso de un modelo conocido. La cobertura publicada es la
    fracción de accesos que resuelven a una tabla confirmada por el esquema.
    """
    indice = IndiceTablas(known_tables)
    hallazgos = [analizar_archivo(sf.path, sf.language, sf.content) for sf in files]

    modelos: dict[tuple[str, str], list[Modelo]] = defaultdict(list)
    for h in hallazgos:
        for m in h.modelos:
            clave = lower_camel(m.nombre) if m.orm == "prisma" else m.nombre
            modelos[(m.orm, clave)].append(m)
    # JPQL/HQL usa el nombre de la entidad, no el de la tabla.
    entidades_jpa = {nombre.lower(): ms for (orm, nombre), ms in modelos.items() if orm == "jpa"}

    usage: list[dict[str, str]] = []
    por_detector: dict[str, list[int]] = {}
    for h in hallazgos:
        accesos: dict[tuple[str, str], tuple[str, str] | None] = {}
        for texto in h.sql:
            for ref in extraer_tablas_sql(texto):
                par = indice.resolver(ref.esquema, ref.nombre)
                if par is None and h.language == "java" and ref.esquema is None:
                    ms = entidades_jpa.get(ref.nombre, [])
                    par = _resolver_modelo(indice, ms[0]) if len(ms) == 1 else None
                clave = f"{ref.esquema or ''}.{ref.nombre}"
                accesos[("sql", clave)] = accesos.get(("sql", clave)) or par
        for m in h.modelos:
            accesos[(m.orm, f"def:{m.nombre}")] = _resolver_modelo(indice, m)
        for u in h.usos:
            ms = modelos.get((u.orm, u.nombre))
            if not ms:
                continue  # no es un modelo conocido: no es un acceso a datos
            pares = {_resolver_modelo(indice, m) for m in ms}
            accesos[(u.orm, f"uso:{u.nombre}")] = pares.pop() if len(pares) == 1 else None

        for (detector, _clave), par in accesos.items():
            cuenta = por_detector.setdefault(detector, [0, 0])
            cuenta[0] += 1
            cuenta[1] += par is not None
        for esquema, tabla in sorted({p for p in accesos.values() if p is not None}):
            usage.append({"file": h.file, "esquema": esquema, "table": tabla})

    return ResultadoUsoTablas(usage=usage, por_detector=por_detector)


def find_table_usage(
    files: list[SourceFile], known_tables: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Aristas USES_TABLE (archivo, esquema, tabla). Ver detectar_uso_tablas."""
    return detectar_uso_tablas(files, known_tables).usage
