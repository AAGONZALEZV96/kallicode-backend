"""Detectores de acceso a datos en el código del cliente (KC-CM-09-3, CM-F17).

Fase 3: además del SQL crudo, el código accede a la base a través de ORM. Este módulo
recorre el AST de tree-sitter de cada archivo una sola vez y devuelve, sin resolver nada
contra el esquema, tres tipos de hallazgo:

- ``sql``: textos SQL embebidos en literales de cadena (con concatenaciones e
  interpolaciones sustituidas por ``?``) o el archivo completo si es ``.sql``.
- ``modelos``: mapeos modelo -> tabla declarados en el código (Prisma ``model`` con
  ``@@map``/``@@schema``, TypeORM ``@Entity``, SQLAlchemy ``__tablename__``/``Table(...)``,
  JPA ``@Entity``/``@Table``).
- ``usos``: referencias a esos modelos desde el código (``prisma.user.findMany``,
  ``getRepository(User)``, ``select(User)``, ``JpaRepository<User, Long>``...).

La resolución a tablas confirmadas por el esquema vive en ``extract_sql.detectar_uso_tablas``:
aquí nunca se decide que una tabla existe.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# Orden estable: es el orden de las claves de por_detector en las stats del snapshot.
DETECTORES: tuple[str, ...] = ("sql", "prisma", "typeorm", "sqlalchemy", "jpa")

LENGUAJES_AST = frozenset({"python", "typescript", "tsx", "javascript", "java"})
_LENGUAJES_TS = frozenset({"typescript", "tsx", "javascript"})

# Operaciones del cliente de Prisma (prisma.<modelo>.<operación>(...)).
OPERACIONES_PRISMA = frozenset(
    {
        "findUnique",
        "findUniqueOrThrow",
        "findFirst",
        "findFirstOrThrow",
        "findMany",
        "create",
        "createMany",
        "createManyAndReturn",
        "update",
        "updateMany",
        "updateManyAndReturn",
        "upsert",
        "delete",
        "deleteMany",
        "count",
        "aggregate",
        "groupBy",
    }
)

# Literal que contiene una sentencia SQL (o un fragmento de query builder que empieza por
# FROM/JOIN). Se exige la palabra clave al principio: "Select a file" en mitad de un
# mensaje no es SQL, y el esquema confirmado filtra lo que se cuele.
_ES_SQL = re.compile(
    r"(?is)^\s*(?:\?\s*)*(?:\(\s*)*(?:"
    r"with\s|select\s|insert\s|update\s|delete\s|merge\s|truncate\s|"
    r"(?:(?:left|right|inner|full|cross)\s+(?:outer\s+)?)?(?:from|join)\s"
    r")"
)
_PREFIJO_CADENA = re.compile(r"""^[rRbBuUfF]{0,3}("{3}|'{3}|"|'|`)""")
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)

_CAMEL_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")


@dataclass(frozen=True)
class Modelo:
    """Mapeo modelo -> tabla declarado en el código (aún sin confirmar por el esquema)."""

    orm: str
    nombre: str  # nombre del modelo tal como lo referencia el código
    candidatos: tuple[str, ...]  # nombres de tabla a probar, en orden
    esquema: str | None
    file: str


@dataclass(frozen=True)
class UsoModelo:
    """Referencia a un modelo. Para Prisma, ``nombre`` es la propiedad del cliente."""

    orm: str
    nombre: str


@dataclass
class HallazgosArchivo:
    file: str
    language: str
    sql: list[str] = field(default_factory=list)
    modelos: list[Modelo] = field(default_factory=list)
    usos: list[UsoModelo] = field(default_factory=list)


def snake_case(nombre: str) -> str:
    """UserProfile -> user_profile (convención por defecto de TypeORM, Flask-SQLAlchemy y
    Spring Boot)."""
    return _CAMEL_2.sub(r"\1_\2", _CAMEL_1.sub(r"\1_\2", nombre)).lower()


def lower_camel(nombre: str) -> str:
    """UserProfile -> userProfile (propiedad del cliente de Prisma)."""
    return nombre[:1].lower() + nombre[1:]


def analizar_archivo(path: str, language: str, content: str) -> HallazgosArchivo:
    """Hallazgos de acceso a datos de un archivo; vacío para lenguajes no soportados."""
    h = HallazgosArchivo(file=path, language=language)
    if language == "sql":
        h.sql.append(content)
        return h
    if language == "prisma":
        h.modelos.extend(_modelos_prisma(path, content))
        return h
    if language not in LENGUAJES_AST:
        return h

    src = content.encode("utf-8")
    root = _parser(language).parse(src).root_node
    h.sql.extend(_literales_sql(root, src))
    if language == "python":
        _sqlalchemy(root, src, path, h)
    elif language == "java":
        _jpa(root, src, path, h)
    else:
        _typeorm_y_prisma(root, src, path, h)
    return h


# --------------------------------------------------------------------------
# Utilidades de AST
# --------------------------------------------------------------------------
@lru_cache(maxsize=8)
def _parser(language: str) -> Any:
    from tree_sitter_languages import get_parser

    with warnings.catch_warnings():
        # tree_sitter < 0.22 avisa de Language(path, name) deprecado en cada carga.
        warnings.simplefilter("ignore", FutureWarning)
        return get_parser(language)


def _texto(node: Any, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _recorrer(root: Any) -> Iterator[Any]:
    pila = [root]
    while pila:
        nodo = pila.pop()
        yield nodo
        pila.extend(reversed(nodo.children))


def _hijos_nombrados(node: Any) -> list[Any]:
    return [c for c in node.children if c.is_named]


# --------------------------------------------------------------------------
# SQL embebido en literales
# --------------------------------------------------------------------------
_TIPOS_CADENA = frozenset({"string", "template_string", "string_literal"})
_TIPOS_INTERPOLACION = frozenset({"interpolation", "template_substitution"})
_TIPOS_CONCAT = frozenset({"binary_expression", "binary_operator", "concatenated_string"})


def _valor_cadena(node: Any, src: bytes) -> str:
    """Contenido de un literal: sin delimitadores, interpolaciones -> ``?``, escapes simples."""
    partes: list[str] = []
    cursor = node.start_byte
    for hijo in node.children:
        if hijo.type in _TIPOS_INTERPOLACION:
            partes.append(src[cursor : hijo.start_byte].decode("utf-8", errors="replace"))
            partes.append(" ? ")
            cursor = hijo.end_byte
    partes.append(src[cursor : node.end_byte].decode("utf-8", errors="replace"))
    texto = "".join(partes)
    m = _PREFIJO_CADENA.match(texto)
    if m:
        delim = m.group(1)
        texto = texto[m.end() :]
        texto = texto.removesuffix(delim)
    return _ESCAPE.sub(lambda e: " " if e.group(1) in "ntr" else e.group(1), texto)


def _es_suma(node: Any, src: bytes) -> bool:
    if node.type == "concatenated_string":
        return True
    op = node.child_by_field_name("operator")
    return op is not None and _texto(op, src) == "+"


def _piezas_concat(node: Any, src: bytes, otros: list[Any]) -> list[str] | None:
    """Operandos de una concatenación de cadenas; None si no contiene ningún literal.

    Los operandos que no son literales se sustituyen por ``?`` y se añaden a ``otros`` para
    seguir buscando SQL dentro de ellos (``conn.query(`...`) + sufijo``).
    """
    piezas: list[str] = []
    hay_literal = False
    pila = [node]
    while pila:
        n = pila.pop()
        if n.type in _TIPOS_CADENA:
            piezas.append(_valor_cadena(n, src))
            hay_literal = True
        elif n.type in _TIPOS_CONCAT and _es_suma(n, src):
            operandos = [c for c in _hijos_nombrados(n) if c.type != "comment"]
            if n.type != "concatenated_string":
                op = n.child_by_field_name("operator")
                operandos = [c for c in operandos if op is None or c.id != op.id]
            pila.extend(reversed(operandos))
        elif n.type == "parenthesized_expression":
            pila.extend(reversed(_hijos_nombrados(n)))
        else:
            piezas.append(" ? ")
            otros.append(n)
    return piezas if hay_literal else None


def _literales_sql(root: Any, src: bytes) -> list[str]:
    """Textos SQL de los literales de cadena; una concatenación cuenta como un literal."""
    encontrados: list[str] = []
    pila = [root]
    while pila:
        nodo = pila.pop()
        texto: str | None = None
        otros: list[Any] = []
        if nodo.type in _TIPOS_CONCAT and _es_suma(nodo, src):
            piezas = _piezas_concat(nodo, src, otros)
            if piezas is not None:
                texto = "".join(piezas)
                pila.extend(otros)
        elif nodo.type in _TIPOS_CADENA:
            texto = _valor_cadena(nodo, src)
        if texto is None:
            pila.extend(reversed(nodo.children))
            continue
        if _ES_SQL.match(texto):
            encontrados.append(texto)
        # Las interpolaciones pueden contener otras consultas: se siguen recorriendo.
        for n in _recorrer(nodo):
            if n.type in _TIPOS_INTERPOLACION:
                pila.append(n)
    return encontrados


def _cadena_simple(node: Any, src: bytes) -> str | None:
    """Valor de un literal sin interpolaciones (nombre de tabla/esquema en un mapeo)."""
    if node is None or node.type not in _TIPOS_CADENA:
        return None
    if any(c.type in _TIPOS_INTERPOLACION for c in node.children):
        return None
    return _valor_cadena(node, src) or None


# --------------------------------------------------------------------------
# Prisma: schema.prisma (texto) y cliente (prisma.<modelo>.<op>) en TS/JS
# --------------------------------------------------------------------------
_PRISMA_MODELO = re.compile(r"(?m)^\s*model\s+(\w+)\s*\{")
_PRISMA_MAP = re.compile(r'@@map\(\s*(?:name\s*:\s*)?"([^"]+)"')
_PRISMA_SCHEMA = re.compile(r'@@schema\(\s*"([^"]+)"')
_PRISMA_COMENTARIO = re.compile(r"//[^\n]*")


def _modelos_prisma(path: str, content: str) -> list[Modelo]:
    texto = _PRISMA_COMENTARIO.sub("", content)
    modelos: list[Modelo] = []
    for m in _PRISMA_MODELO.finditer(texto):
        profundidad, i = 1, m.end()
        while i < len(texto) and profundidad:
            profundidad += {"{": 1, "}": -1}.get(texto[i], 0)
            i += 1
        cuerpo = texto[m.end() : i]
        mapa = _PRISMA_MAP.search(cuerpo)
        esquema = _PRISMA_SCHEMA.search(cuerpo)
        modelos.append(
            Modelo(
                orm="prisma",
                nombre=m.group(1),
                candidatos=(mapa.group(1) if mapa else m.group(1),),
                esquema=esquema.group(1) if esquema else None,
                file=path,
            )
        )
    return modelos


def _uso_prisma(call: Any, src: bytes) -> UsoModelo | None:
    fn = call.child_by_field_name("function")
    if fn is None or fn.type != "member_expression":
        return None
    op = fn.child_by_field_name("property")
    obj = fn.child_by_field_name("object")
    if op is None or obj is None or _texto(op, src) not in OPERACIONES_PRISMA:
        return None
    if obj.type != "member_expression":
        return None
    prop = obj.child_by_field_name("property")
    if prop is None or prop.type != "property_identifier":
        return None
    return UsoModelo(orm="prisma", nombre=_texto(prop, src))


# --------------------------------------------------------------------------
# TypeORM (TS/JS)
# --------------------------------------------------------------------------
_CLASES_TS = frozenset({"class_declaration", "abstract_class_declaration", "class"})


def _clase_decorada(decorador: Any) -> Any | None:
    padre = decorador.parent
    if padre is None:
        return None
    if padre.type in _CLASES_TS:
        return padre
    if padre.type == "export_statement":
        decl = padre.child_by_field_name("declaration")
        if decl is not None and decl.type in _CLASES_TS:
            return decl
    return None


def _propiedades_objeto(obj: Any, src: bytes) -> dict[str, str]:
    props: dict[str, str] = {}
    for par in obj.children:
        if par.type != "pair":
            continue
        clave = par.child_by_field_name("key")
        valor = _cadena_simple(par.child_by_field_name("value"), src)
        if clave is not None and valor is not None:
            props[_texto(clave, src).strip("\"'")] = valor
    return props


def _entidad_typeorm(decorador: Any, src: bytes, path: str) -> Modelo | None:
    expr = _hijos_nombrados(decorador)
    if not expr:
        return None
    llamada = expr[0]
    args: list[Any] = []
    if llamada.type == "call_expression":
        fn = llamada.child_by_field_name("function")
        nombre_dec = _texto(fn, src) if fn is not None else ""
        argumentos = llamada.child_by_field_name("arguments")
        args = _hijos_nombrados(argumentos) if argumentos is not None else []
    else:
        nombre_dec = _texto(llamada, src)
    if nombre_dec.rsplit(".", 1)[-1] != "Entity":
        return None
    clase = _clase_decorada(decorador)
    nombre_nodo = clase.child_by_field_name("name") if clase is not None else None
    if nombre_nodo is None:
        return None
    clase_nombre = _texto(nombre_nodo, src)
    tabla: str | None = None
    esquema: str | None = None
    for arg in args:
        if arg.type == "string":
            tabla = _cadena_simple(arg, src)
        elif arg.type == "object":
            props = _propiedades_objeto(arg, src)
            tabla = props.get("name", tabla)
            esquema = props.get("schema", esquema)
    candidatos = (tabla,) if tabla else (snake_case(clase_nombre), clase_nombre)
    return Modelo("typeorm", clase_nombre, candidatos, esquema, path)


def _nombres_visibles_ts(root: Any, src: bytes) -> set[str]:
    visibles: set[str] = set()
    for n in _recorrer(root):
        if n.type in ("import_specifier", "namespace_import", "import_clause"):
            for c in n.children:
                if c.type == "identifier":
                    visibles.add(_texto(c, src))
        elif n.type in _CLASES_TS:
            nombre = n.child_by_field_name("name")
            if nombre is not None:
                visibles.add(_texto(nombre, src))
    return visibles


def _typeorm_y_prisma(root: Any, src: bytes, path: str, h: HallazgosArchivo) -> None:
    visibles = _nombres_visibles_ts(root, src)
    candidatos_uso: set[str] = set()
    for n in _recorrer(root):
        if n.type == "decorator":
            modelo = _entidad_typeorm(n, src, path)
            if modelo is not None:
                h.modelos.append(modelo)
        elif n.type == "call_expression":
            uso = _uso_prisma(n, src)
            if uso is not None:
                h.usos.append(uso)
        elif n.type == "arguments":
            # getRepository(User), @InjectRepository(User), manager.find(User, ...),
            # createQueryBuilder(User, "u"). Las relaciones (() => User) no son acceso.
            candidatos_uso.update(_texto(c, src) for c in n.children if c.type == "identifier")
        elif n.type == "type_arguments":
            # Repository<User>, class X extends Repository<User>
            previo = n.prev_named_sibling
            if previo is not None and _texto(previo, src).endswith("Repository"):
                candidatos_uso.update(
                    _texto(c, src) for c in _hijos_nombrados(n) if c.type == "type_identifier"
                )
    h.usos.extend(
        UsoModelo(orm="typeorm", nombre=nombre) for nombre in sorted(candidatos_uso & visibles)
    )


# --------------------------------------------------------------------------
# SQLAlchemy (Python)
# --------------------------------------------------------------------------
_METODOS_TABLA_CORE = frozenset({"select", "insert", "update", "delete", "join", "outerjoin"})


def _argumentos_kw(arglist: Any, src: bytes) -> tuple[list[Any], dict[str, Any]]:
    posicionales: list[Any] = []
    nombrados: dict[str, Any] = {}
    for c in _hijos_nombrados(arglist):
        if c.type == "keyword_argument":
            k = c.child_by_field_name("name")
            v = c.child_by_field_name("value")
            if k is not None and v is not None:
                nombrados[_texto(k, src)] = v
        elif c.type != "comment":
            posicionales.append(c)
    return posicionales, nombrados


def _tabla_core(call: Any, src: bytes) -> tuple[str, str | None] | None:
    """``Table("t", metadata, ..., schema="s")`` -> ("t", "s")."""
    fn = call.child_by_field_name("function")
    if fn is None or _texto(fn, src).rsplit(".", 1)[-1] != "Table":
        return None
    arglist = call.child_by_field_name("arguments")
    if arglist is None:
        return None
    pos, kw = _argumentos_kw(arglist, src)
    tabla = _cadena_simple(pos[0], src) if pos else None
    if tabla is None:
        return None
    return tabla, _cadena_simple(kw.get("schema"), src)


def _esquema_table_args(valor: Any, src: bytes) -> str | None:
    """``{"schema": "s"}`` o ``(UniqueConstraint(...), {"schema": "s"})``."""
    dicts = [valor] if valor.type == "dictionary" else []
    if valor.type == "tuple":
        dicts = [c for c in _hijos_nombrados(valor) if c.type == "dictionary"]
    for d in dicts:
        for par in d.children:
            if par.type != "pair":
                continue
            if _cadena_simple(par.child_by_field_name("key"), src) == "schema":
                return _cadena_simple(par.child_by_field_name("value"), src)
    return None


def _modelo_sqlalchemy(clase: Any, src: bytes, path: str) -> Modelo | None:
    nombre_nodo = clase.child_by_field_name("name")
    cuerpo = clase.child_by_field_name("body")
    if nombre_nodo is None or cuerpo is None:
        return None
    nombre = _texto(nombre_nodo, src)
    tabla: str | None = None
    esquema: str | None = None
    abstracta = False
    for sentencia in _hijos_nombrados(cuerpo):
        asig = _hijos_nombrados(sentencia)[0] if sentencia.type == "expression_statement" else None
        if asig is None or asig.type != "assignment":
            continue
        izq = asig.child_by_field_name("left")
        der = asig.child_by_field_name("right")
        if izq is None or der is None:
            continue
        clave = _texto(izq, src)
        if clave == "__tablename__":
            tabla = _cadena_simple(der, src)
        elif clave == "__table_args__":
            esquema = _esquema_table_args(der, src)
        elif clave == "__table__" and der.type == "call":
            core = _tabla_core(der, src)
            if core is not None:
                tabla, esquema = core
        elif clave == "__abstract__" and _texto(der, src) == "True":
            abstracta = True
    if abstracta:
        return None
    if tabla is not None:
        return Modelo("sqlalchemy", nombre, (tabla,), esquema, path)
    # Flask-SQLAlchemy: class User(db.Model) sin __tablename__ -> "user".
    bases = clase.child_by_field_name("superclasses")
    if bases is not None and any(
        _texto(b, src).rsplit(".", 1)[-1] == "Model" for b in _hijos_nombrados(bases)
    ):
        return Modelo("sqlalchemy", nombre, (snake_case(nombre),), esquema, path)
    return None


def _nombres_visibles_py(root: Any, src: bytes) -> set[str]:
    visibles: set[str] = set()
    for n in _recorrer(root):
        if n.type in ("import_from_statement", "import_statement"):
            for c in _hijos_nombrados(n):
                if c.type == "aliased_import":
                    alias = c.child_by_field_name("alias")
                    if alias is not None:
                        visibles.add(_texto(alias, src))
                elif c.type == "dotted_name" and n.child_by_field_name("module_name") != c:
                    visibles.add(_texto(c, src).split(".")[0])
        elif n.type == "class_definition":
            nombre = n.child_by_field_name("name")
            if nombre is not None:
                visibles.add(_texto(nombre, src))
        elif n.type == "assignment":
            izq = n.child_by_field_name("left")
            if izq is not None and izq.type == "identifier":
                visibles.add(_texto(izq, src))
    return visibles


def _partes_atributo(node: Any, src: bytes) -> list[str]:
    """``modelos.Payment.id`` -> ["modelos", "Payment", "id"]; [] si no es identificador."""
    if node.type == "identifier":
        return [_texto(node, src)]
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        if obj is not None and attr is not None:
            base = _partes_atributo(obj, src)
            return [*base, _texto(attr, src)] if base else []
    return []


def _sqlalchemy(root: Any, src: bytes, path: str, h: HallazgosArchivo) -> None:
    visibles = _nombres_visibles_py(root, src)
    cadenas: list[list[str]] = []
    for n in _recorrer(root):
        if n.type == "class_definition":
            modelo = _modelo_sqlalchemy(n, src, path)
            if modelo is not None:
                h.modelos.append(modelo)
        elif n.type == "assignment":
            # eventos = Table("events", metadata, ..., schema="audit")
            izq = n.child_by_field_name("left")
            der = n.child_by_field_name("right")
            if izq is not None and der is not None and der.type == "call":
                core = _tabla_core(der, src)
                if core is not None and izq.type == "identifier":
                    h.modelos.append(
                        Modelo("sqlalchemy", _texto(izq, src), (core[0],), core[1], path)
                    )
        elif n.type == "argument_list":
            # select(User), session.query(modelos.Payment), session.get(User, 1)
            for c in _hijos_nombrados(n):
                valor = c.child_by_field_name("value") if c.type == "keyword_argument" else c
                if valor is not None:
                    cadenas.append(_partes_atributo(valor, src))
        elif n.type == "attribute":
            # User.query (Flask-SQLAlchemy), eventos.insert() (Core)
            attr = n.child_by_field_name("attribute")
            obj = n.child_by_field_name("object")
            if attr is not None and obj is not None:
                metodo = _texto(attr, src)
                if metodo == "query" or metodo in _METODOS_TABLA_CORE:
                    cadenas.append(_partes_atributo(obj, src))
    nombres: set[str] = set()
    for partes in cadenas:
        if not partes or partes[0] not in visibles:
            continue
        # El modelo es el primer componente o, si es un módulo importado, el segundo.
        nombres.update(partes[:2])
    h.usos.extend(UsoModelo(orm="sqlalchemy", nombre=nombre) for nombre in sorted(nombres))


# --------------------------------------------------------------------------
# JPA / Hibernate (Java)
# --------------------------------------------------------------------------
def _anotaciones(clase: Any, src: bytes) -> dict[str, dict[str, str]]:
    """Anotaciones de una clase: nombre -> {argumento: valor literal}."""
    resultado: dict[str, dict[str, str]] = {}
    for mods in clase.children:
        if mods.type != "modifiers":
            continue
        for anot in mods.children:
            if anot.type not in ("annotation", "marker_annotation"):
                continue
            nombre = anot.child_by_field_name("name")
            if nombre is None:
                continue
            args: dict[str, str] = {}
            lista = anot.child_by_field_name("arguments")
            for par in _hijos_nombrados(lista) if lista is not None else []:
                if par.type == "element_value_pair":
                    k = par.child_by_field_name("key")
                    v = _cadena_simple(par.child_by_field_name("value"), src)
                    if k is not None and v is not None:
                        args[_texto(k, src)] = v
                elif (v := _cadena_simple(par, src)) is not None:
                    args["value"] = v
            resultado[_texto(nombre, src).rsplit(".", 1)[-1]] = args
    return resultado


def _jpa(root: Any, src: bytes, path: str, h: HallazgosArchivo) -> None:
    nombres: set[str] = set()
    for n in _recorrer(root):
        if n.type == "class_declaration":
            anot = _anotaciones(n, src)
            nombre_nodo = n.child_by_field_name("name")
            if "Entity" in anot and nombre_nodo is not None:
                clase = _texto(nombre_nodo, src)
                tabla = anot.get("Table", {}).get("name")
                entidad = anot["Entity"].get("name") or clase
                candidatos = (tabla,) if tabla else (entidad, snake_case(entidad))
                esquema = anot.get("Table", {}).get("schema")
                h.modelos.append(Modelo("jpa", clase, candidatos, esquema, path))
        elif n.type == "generic_type":
            # interface Repo extends JpaRepository<Invoice, Long>
            base, *resto = _hijos_nombrados(n)
            if _texto(base, src).endswith("Repository") and resto:
                tipos = [c for c in _hijos_nombrados(resto[0]) if c.type == "type_identifier"]
                if tipos:
                    nombres.add(_texto(tipos[0], src))
        elif n.type == "class_literal":
            # em.find(Invoice.class, id)
            tipo = _hijos_nombrados(n)
            if tipo and tipo[0].type == "type_identifier":
                nombres.add(_texto(tipo[0], src))
    h.usos.extend(UsoModelo(orm="jpa", nombre=nombre) for nombre in sorted(nombres))
