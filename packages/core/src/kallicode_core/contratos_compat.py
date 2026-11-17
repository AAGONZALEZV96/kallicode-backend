"""Detector de cambios incompatibles entre dos versiones de un contrato JSON Schema (KC-CM-09-4).

Compara esquemas generados por Pydantic (objetos con `properties`/`required`, `$defs` + `$ref`,
`anyOf`, `enum`/`const`, cotas) y devuelve la lista de cambios que romperían a un consumidor que ya
cumple la versión base. La dirección importa:

- `entrada` (lo que el consumidor ENVÍA): es incompatible aceptar menos que antes — quitar un campo
  (additionalProperties: false lo rechazaría), añadir un campo obligatorio, volver obligatorio uno
  opcional, estrechar un tipo, una cota o un enum, cambiar un valor por defecto o un formato.
- `salida` (lo que el consumidor RECIBE y valida con el esquema congelado): es incompatible
  devolver algo que antes no podía llegar — añadir un campo (additionalProperties: false en el
  consumidor), quitar uno o dejar de garantizarlo, ensanchar un tipo, una cota o un enum.

Las descripciones y los títulos no cuentan. Ante una forma que no sabe comparar, el detector la
declara incompatible (falso positivo antes que dejar pasar una rotura).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

Modo = Literal["entrada", "salida"]

_COTAS_SUPERIORES = ("maximum", "exclusiveMaximum", "maxLength", "maxItems")
_COTAS_INFERIORES = ("minimum", "exclusiveMinimum", "minLength", "minItems")
_IGNORADAS = {"description", "title", "examples"}


def _resolver(nodo: Mapping[str, Any], raiz: Mapping[str, Any]) -> dict[str, Any]:
    """Sigue `$ref` locales (#/$defs/X) conservando las claves hermanas del nodo."""
    actual = dict(nodo)
    vistos: set[str] = set()
    while "$ref" in actual:
        ref = str(actual.pop("$ref"))
        if ref in vistos or not ref.startswith("#/$defs/"):
            break
        vistos.add(ref)
        destino = raiz.get("$defs", {}).get(ref.rsplit("/", 1)[-1], {})
        actual = {**destino, **actual}
    if "allOf" in actual and len(actual["allOf"]) == 1:
        unico = _resolver(actual.pop("allOf")[0], raiz)
        actual = {**unico, **actual}
    return actual


def _variantes(nodo: dict[str, Any], raiz: Mapping[str, Any]) -> list[dict[str, Any]]:
    miembros = nodo.get("anyOf") or nodo.get("oneOf")
    if miembros:
        return [_resolver(m, raiz) for m in miembros]
    return [nodo]


def _tipos(nodo: dict[str, Any], raiz: Mapping[str, Any]) -> set[str]:
    tipos: set[str] = set()
    for v in _variantes(nodo, raiz):
        if "type" in v:
            t = v["type"]
            tipos |= set(t) if isinstance(t, list) else {str(t)}
        elif "properties" in v:
            tipos.add("object")
        elif "const" in v or "enum" in v:
            valores = v.get("enum", [v.get("const")])
            tipos |= {type(x).__name__ for x in valores}
        else:
            tipos.add("any")
    return tipos


def _valores(nodo: dict[str, Any], raiz: Mapping[str, Any]) -> set[str] | None:
    """Valores permitidos si TODAS las variantes no nulas son enum/const; None si es abierto."""
    valores: set[str] = set()
    for v in _variantes(nodo, raiz):
        if v.get("type") == "null":
            continue
        if "enum" in v:
            valores |= {json.dumps(x, sort_keys=True) for x in v["enum"]}
        elif "const" in v:
            valores.add(json.dumps(v["const"], sort_keys=True))
        else:
            return None
    return valores


def _no_nula(nodo: dict[str, Any], raiz: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [v for v in _variantes(nodo, raiz) if v.get("type") != "null"]


def comparar_esquemas(
    base: Mapping[str, Any],
    actual: Mapping[str, Any],
    modo: Modo,
    ruta: str = "$",
) -> list[str]:
    """Cambios incompatibles de `actual` frente a `base` (dos esquemas raíz con sus `$defs`)."""
    cambios = _comparar(dict(base), dict(actual), base, actual, modo, ruta, 0)
    return list(dict.fromkeys(cambios))  # una variante X | None se compara dos veces


def _comparar(
    b: dict[str, Any],
    a: dict[str, Any],
    raiz_b: Mapping[str, Any],
    raiz_a: Mapping[str, Any],
    modo: Modo,
    ruta: str,
    profundidad: int,
) -> list[str]:
    if profundidad > 50:
        return [f"{ruta}: esquema demasiado profundo para compararlo"]
    b, a = _resolver(b, raiz_b), _resolver(a, raiz_a)
    cambios: list[str] = []

    # Tipos
    tb, ta = _tipos(b, raiz_b), _tipos(a, raiz_a)
    if modo == "entrada" and "any" not in ta and not tb <= ta:
        cambios.append(f"{ruta}: deja de aceptar el tipo {sorted(tb - ta)}")
    if modo == "salida" and "any" not in tb and not ta <= tb:
        cambios.append(f"{ruta}: puede devolver el tipo nuevo {sorted(ta - tb)}")

    # Enumeraciones / constantes
    vb, va = _valores(b, raiz_b), _valores(a, raiz_a)
    if vb is not None or va is not None:
        if modo == "entrada" and vb is None and va is not None:
            cambios.append(f"{ruta}: pasa de valor libre a enumeración")
        elif modo == "salida" and va is None and vb is not None:
            cambios.append(f"{ruta}: pasa de enumeración a valor libre")
        elif vb is not None and va is not None:
            if modo == "entrada" and vb - va:
                cambios.append(f"{ruta}: deja de aceptar los valores {sorted(vb - va)}")
            if modo == "salida" and va - vb:
                cambios.append(f"{ruta}: puede devolver los valores nuevos {sorted(va - vb)}")

    # Cotas, valor por defecto y formato
    cambios += _cotas(b, a, modo, ruta)
    if modo == "entrada" and ("default" in b or "default" in a):
        if b.get("default") != a.get("default"):
            cambios.append(f"{ruta}: cambia el valor por defecto")
    for clave in ("format", "pattern"):
        if b.get(clave) != a.get(clave):
            cambios.append(f"{ruta}: cambia {clave}")

    # Variantes compuestas (p. ej. X | None con X objeto o lista)
    nb, na = _no_nula(b, raiz_b), _no_nula(a, raiz_a)
    if len(nb) == len(na) == 1 and (nb[0] is not b or na[0] is not a):
        return cambios + _comparar(nb[0], na[0], raiz_b, raiz_a, modo, ruta, profundidad + 1)
    if len(nb) != len(na) or len(nb) > 1:
        if json.dumps(nb, sort_keys=True) != json.dumps(na, sort_keys=True) and len(nb) > 1:
            cambios.append(f"{ruta}: cambia la composición de variantes (anyOf)")
        return cambios

    # Objetos
    if "properties" in b or "properties" in a:
        cambios += _objeto(b, a, raiz_b, raiz_a, modo, ruta, profundidad)
    # Listas
    if "items" in b or "items" in a:
        if "items" not in b or "items" not in a:
            cambios.append(f"{ruta}: cambia la forma de los elementos de la lista")
        else:
            cambios += _comparar(
                b["items"], a["items"], raiz_b, raiz_a, modo, f"{ruta}[]", profundidad + 1
            )
    return cambios


def _cotas(b: dict[str, Any], a: dict[str, Any], modo: Modo, ruta: str) -> list[str]:
    cambios: list[str] = []
    for clave in _COTAS_SUPERIORES:
        cb, ca = b.get(clave), a.get(clave)
        if modo == "entrada" and ca is not None and (cb is None or ca < cb):
            cambios.append(f"{ruta}: {clave} se estrecha ({cb} -> {ca})")
        if modo == "salida" and cb is not None and (ca is None or ca > cb):
            cambios.append(f"{ruta}: {clave} se ensancha ({cb} -> {ca})")
    for clave in _COTAS_INFERIORES:
        cb, ca = b.get(clave), a.get(clave)
        if modo == "entrada" and ca is not None and (cb is None or ca > cb):
            cambios.append(f"{ruta}: {clave} se estrecha ({cb} -> {ca})")
        if modo == "salida" and cb is not None and (ca is None or ca < cb):
            cambios.append(f"{ruta}: {clave} se ensancha ({cb} -> {ca})")
    return cambios


def _objeto(
    b: dict[str, Any],
    a: dict[str, Any],
    raiz_b: Mapping[str, Any],
    raiz_a: Mapping[str, Any],
    modo: Modo,
    ruta: str,
    profundidad: int,
) -> list[str]:
    cambios: list[str] = []
    pb, pa = b.get("properties", {}), a.get("properties", {})
    rb, ra = set(b.get("required", [])), set(a.get("required", []))
    for nombre in sorted(set(pb) - set(pa)):
        cambios.append(f"{ruta}.{nombre}: campo retirado")
    for nombre in sorted(set(pa) - set(pb)):
        if modo == "salida":
            cambios.append(f"{ruta}.{nombre}: campo nuevo en la salida")
        elif nombre in ra:
            cambios.append(f"{ruta}.{nombre}: campo obligatorio nuevo en la entrada")
    comunes = sorted(set(pb) & set(pa))
    for nombre in comunes:
        if modo == "entrada" and nombre in ra and nombre not in rb:
            cambios.append(f"{ruta}.{nombre}: pasa a ser obligatorio")
        if modo == "salida" and nombre in rb and nombre not in ra:
            cambios.append(f"{ruta}.{nombre}: deja de estar garantizado")
    adicionales_b = b.get("additionalProperties", True) is not False
    adicionales_a = a.get("additionalProperties", True) is not False
    if modo == "entrada" and adicionales_b and not adicionales_a:
        cambios.append(f"{ruta}: deja de aceptar propiedades adicionales")
    if modo == "salida" and adicionales_a and not adicionales_b:
        cambios.append(f"{ruta}: puede devolver propiedades adicionales")
    for nombre in comunes:
        cambios += _comparar(
            pb[nombre], pa[nombre], raiz_b, raiz_a, modo, f"{ruta}.{nombre}", profundidad + 1
        )
    return cambios


def sin_propiedades_adicionales(esquema: Mapping[str, Any], ruta: str = "$") -> list[str]:
    """Objetos del esquema (incluidos sus `$defs`) que no declaran additionalProperties: false."""
    faltan: list[str] = []

    def visitar(nodo: Any, donde: str) -> None:
        if isinstance(nodo, Mapping):
            es_objeto = nodo.get("type") == "object" or "properties" in nodo
            if es_objeto and nodo.get("additionalProperties") is not False:
                faltan.append(donde)
            for clave, valor in nodo.items():
                if clave not in _IGNORADAS and clave not in ("default", "const", "enum"):
                    visitar(valor, f"{donde}.{clave}")
        elif isinstance(nodo, list):
            for i, valor in enumerate(nodo):
                visitar(valor, f"{donde}[{i}]")

    visitar(esquema, ruta)
    return faltan
