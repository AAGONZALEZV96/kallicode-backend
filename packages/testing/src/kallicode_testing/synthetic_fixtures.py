"""Generador de fixtures sintéticos desde modelos Pydantic (KC-QA-B3-06).

A partir de cualquier modelo Pydantic del backend (por ejemplo, ``TicketIn``) genera:

- un ejemplo de datos válidos, que respeta los límites declarados en ``Field(...)``;
- casos de borde por cada campo de texto: largo mínimo, largo máximo, uno más que el
  máximo, un texto extremadamente largo y caracteres Unicode;
- un archivo JSON local con todo lo anterior, para reutilizarlo en otras pruebas.

Cada caso de borde trae ``esperado_valido``: lo que la API debería responder (aceptar o
rechazar) según las reglas del modelo. Las pruebas comparan esa expectativa con Pydantic.
"""

import json
import re
import types
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypedDict, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel
from pydantic.fields import FieldInfo

TEXTO_UNICODE = "ñandú Ñuñoa 漢字 Ελληνικά مرحبا 🚀✅"
LARGO_EXTREMO = 100_000


class CasoBorde(TypedDict):
    campo: str
    descripcion: str
    datos: dict[str, Any]
    esperado_valido: bool


def _restriccion(campo: FieldInfo, nombre: str) -> Any:
    """Busca una restricción (max_length, ge, pattern...) en la metadata del Field."""
    for regla in campo.metadata:
        valor = getattr(regla, nombre, None)
        if valor is not None:
            return valor
    return None


def _sin_opcional(tipo: Any) -> Any:
    """Convierte ``str | None`` en ``str`` para saber qué tipo generar."""
    if get_origin(tipo) in (Union, types.UnionType):
        tipos = [t for t in get_args(tipo) if t is not type(None)]
        return tipos[0] if tipos else tipo
    return tipo


def _texto_para_patron(patron: str) -> str | None:
    """Resuelve patrones simples tipo ``^(bug|mejora)$`` tomando la primera opción."""
    coincidencia = re.fullmatch(r"\^\(([^()]+)\)\$", patron)
    if coincidencia:
        return coincidencia.group(1).split("|")[0]
    return None


def _valor_valido(tipo: Any, campo: FieldInfo) -> Any:
    tipo = _sin_opcional(tipo)

    if get_origin(tipo) is Literal:
        return get_args(tipo)[0]
    if isinstance(tipo, type) and issubclass(tipo, BaseModel):
        return datos_validos(tipo)
    if isinstance(tipo, type) and issubclass(tipo, Enum):
        return next(iter(tipo)).value
    if tipo is bool:
        return True
    if tipo in (int, float):
        minimo = _restriccion(campo, "ge")
        if minimo is None:
            mayor_que = _restriccion(campo, "gt")
            minimo = mayor_que + 1 if mayor_que is not None else 1
        return tipo(minimo)
    if tipo is str:
        patron = _restriccion(campo, "pattern")
        if patron:
            return _texto_para_patron(patron) or ""
        largo = max(_restriccion(campo, "min_length") or 1, 1)
        return "a" * largo
    if tipo is UUID:
        return "00000000-0000-4000-8000-000000000001"
    if tipo is datetime:
        return datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    if tipo is date:
        return "2026-01-01"
    if get_origin(tipo) is list:
        return []
    if get_origin(tipo) is dict:
        return {}
    return None


def datos_validos(modelo: type[BaseModel]) -> dict[str, Any]:
    """Devuelve un diccionario que el modelo acepta: solo campos obligatorios."""
    return {
        nombre: _valor_valido(campo.annotation, campo)
        for nombre, campo in modelo.model_fields.items()
        if campo.is_required()
    }


def casos_borde(modelo: type[BaseModel]) -> list[CasoBorde]:
    """Genera casos de borde para cada campo de texto libre (sin ``pattern``)."""
    base = datos_validos(modelo)
    casos: list[CasoBorde] = []

    for nombre, campo in modelo.model_fields.items():
        if _sin_opcional(campo.annotation) is not str or _restriccion(campo, "pattern"):
            continue
        minimo = _restriccion(campo, "min_length")
        maximo = _restriccion(campo, "max_length")

        # (descripción, valor del campo, ¿la API debería aceptarlo?)
        variantes: list[tuple[str, str, bool]] = []
        if minimo:
            variantes.append((f"largo mínimo ({minimo})", "a" * minimo, True))
            variantes.append((f"bajo el mínimo ({minimo - 1})", "a" * (minimo - 1), False))
        if maximo:
            variantes.append((f"largo máximo ({maximo})", "a" * maximo, True))
            variantes.append((f"sobre el máximo ({maximo + 1})", "a" * (maximo + 1), False))

        extremo_valido = maximo is None or maximo >= LARGO_EXTREMO
        variantes.append(
            (f"texto extremo ({LARGO_EXTREMO} caracteres)", "x" * LARGO_EXTREMO, extremo_valido)
        )

        unicode = TEXTO_UNICODE[:maximo] if maximo else TEXTO_UNICODE
        unicode_valido = minimo is None or len(unicode) >= minimo
        variantes.append(
            ("caracteres Unicode (tildes, CJK, árabe, emoji)", unicode, unicode_valido)
        )

        for descripcion, valor, valido in variantes:
            casos.append(
                {
                    "campo": nombre,
                    "descripcion": descripcion,
                    "datos": {**base, nombre: valor},
                    "esperado_valido": valido,
                }
            )

    return casos


def escribir_fixtures(modelo: type[BaseModel], directorio: str | Path) -> Path:
    """Escribe ``<Modelo>.json`` con los datos válidos y los casos de borde."""
    destino = Path(directorio) / f"{modelo.__name__}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    contenido = {
        "modelo": modelo.__name__,
        "validos": [datos_validos(modelo)],
        "borde": casos_borde(modelo),
    }
    destino.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino
