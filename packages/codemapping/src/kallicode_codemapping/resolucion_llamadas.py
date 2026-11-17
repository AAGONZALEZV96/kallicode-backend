"""Desambiguación de llamadas por nombre: familia de lenguaje e imports (KC-CM-12-4).

El analizador solo conoce el NOMBRE del invocado (`validar`, `get`). El cargador lo resuelve contra los
símbolos del tenant con ese nombre. Antes, cualquier homónimo del mismo lenguaje quedaba enlazado, y
la verificación de calidad semántica (docs/calidad/informe-calidad-cm.md) lo midió como la primera
fuente de falsos positivos. Ahora, entre los candidatos, gana el mejor rango:

    0  el mismo fichero que la llamada
    1  un fichero que la llamada importa (rutas candidatas de este módulo)
    2  cualquier otro del tenant (lo de siempre: sin información, no se descarta nada)

Solo se descartan homónimos cuando hay uno mejor. Una llamada a un método de un objeto inyectado
(`self.repo.guardar()`), cuyo módulo no se importa, sigue resolviendo como antes.

Familia de lenguaje: TypeScript, TSX y JavaScript se importan entre sí, así que comparten familia. Un
componente .tsx que llama a una función de un .ts ya no se queda sin arista.

Las rutas importadas son candidatas, no rutas exactas: no se conoce la raíz de los imports absolutos
(`from clientes.validaciones import x` puede estar en `src/clientes/validaciones.py`), así que
el cargador las compara como sufijo de ruta.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable

FAMILIA_JS = "js"
LENGUAJES_JS = frozenset({"typescript", "tsx", "javascript"})
_LENGUAJES_JS = LENGUAJES_JS
EXTENSIONES_JS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

_PY_FROM = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)$", re.DOTALL)
_PY_IMPORT = re.compile(r"^\s*import\s+(.+)$", re.DOTALL)
_JS_ORIGEN = re.compile(r"""(?:\bfrom\s*|^\s*import\s*|\brequire\s*\(\s*)['"]([^'"]+)['"]""")
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;")


def familia(lenguaje: str) -> str:
    """Lenguajes cuyos ficheros se llaman entre sí (la resolución no cruza familias)."""
    return FAMILIA_JS if lenguaje in _LENGUAJES_JS else lenguaje


def _nombres_importados(texto: str) -> list[str]:
    limpio = texto.strip().strip("()").replace("\n", " ")
    return [n.split(" as ")[0].strip() for n in limpio.split(",") if n.strip() and n.strip() != "*"]


def _modulo_python(modulo: str) -> list[str]:
    base = modulo.replace(".", "/").strip("/")
    return [f"{base}.py", f"{base}/__init__.py"] if base else []


def _imports_python(ruta: str, sentencia: str) -> set[str]:
    rutas: set[str] = set()
    if m := _PY_FROM.match(sentencia):
        puntos, modulo, nombres = m.groups()
        if puntos:
            base = posixpath.dirname(ruta)
            for _ in range(len(puntos) - 1):
                base = posixpath.dirname(base)
            modulo = posixpath.join(base, modulo.replace(".", "/")) if modulo else base
        # `from paquete import modulo` importa módulos: también son candidatos.
        rutas.update(_modulo_python(modulo))
        for nombre in _nombres_importados(nombres):
            rutas.update(_modulo_python(f"{modulo}/{nombre}" if modulo else nombre))
    elif m := _PY_IMPORT.match(sentencia):
        for nombre in _nombres_importados(m.group(1)):
            rutas.update(_modulo_python(nombre))
    return rutas


def _imports_js(ruta: str, sentencia: str) -> set[str]:
    rutas: set[str] = set()
    for origen in _JS_ORIGEN.findall(sentencia):
        if not origen.startswith("."):
            continue  # paquete de node_modules o alias de compilación: no se resuelve
        destino = posixpath.normpath(posixpath.join(posixpath.dirname(ruta), origen))
        if destino.startswith(".."):
            continue
        if destino.endswith(EXTENSIONES_JS):
            rutas.add(destino)
        rutas.update(destino + ext for ext in EXTENSIONES_JS)
        rutas.update(f"{destino}/index{ext}" for ext in EXTENSIONES_JS)
    return rutas


def _imports_java(sentencia: str) -> set[str]:
    m = _JAVA_IMPORT.match(sentencia)
    if not m:
        return set()
    partes = m.group(1).split(".")
    # `import a.b.Clase;` o `import static a.b.Clase.metodo;`: el fichero es el de la clase.
    return {"/".join(partes[:i]) + ".java" for i in range(len(partes), 1, -1)}


def rutas_importadas(ruta: str, lenguaje: str, imports: Iterable[str]) -> set[str]:
    """Rutas (o sufijos de ruta) de los ficheros que importa el fichero `ruta`."""
    ruta = ruta.replace("\\", "/")
    rutas: set[str] = set()
    for sentencia in imports:
        if lenguaje == "python":
            rutas |= _imports_python(ruta, sentencia)
        elif lenguaje in _LENGUAJES_JS:
            rutas |= _imports_js(ruta, sentencia)
        elif lenguaje == "java":
            rutas |= _imports_java(sentencia)
    return {r for r in rutas if r and not r.startswith("/")}
