"""Repositorio sintético para medir la ingesta a escala (KC-CM-11-1).

Genera, de forma determinista, un repositorio Git de código Python y el esquema de base de datos, la
capa semántica y las aristas que lo acompañan, **con los recuentos esperados por capa calculados de
antemano**. El benchmark de ingesta (tests/bench_ingesta_escala.py) carga ese repositorio con el
pipeline real y compara lo persistido con `Escala.esperado()`: cualquier pérdida o duplicado en la
carga masiva (COPY por lotes, upserts, resolución de llamadas) cambia un recuento y falla la puerta.

Escala completa (`Escala.completa()`), la declarada por el proyecto:

    nodos     200.000 = 8.000 archivos + 176.000 símbolos + 600 tablas + 9.000 columnas
                        + 6.000 definiciones + 400 conceptos
    aristas   260.000 = 214.000 CALLS + 24.000 USES_TABLE + 1.200 REFERENCES
                        + 12.000 REALIZED_BY + 6.000 EXPRESSES + 2.800 RELATES

Todo es resoluble sin ambigüedad: cada símbolo tiene un nombre único en el repositorio (`fn_0000123`),
así que cada llamada resuelve a exactamente un destino y el recuento de aristas CALLS es exacto. Las
llamadas, los usos de tablas y los enlaces semánticos usan desplazamientos fijos que no repiten pares
ni se llaman a sí mismos.

No es código de producción: lo usan los benchmarks y sus pruebas.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TIPOS_COLUMNA = ("bigint", "text", "integer", "timestamp with time zone", "boolean", "numeric")
_ESQUEMA = "public"
_LLAMADAS_DOBLES_NUM, _LLAMADAS_DOBLES_DEN = 19, 88  # 38.000 de 176.000 símbolos llaman a dos
_USOS_POR_ARCHIVO = 3
_FK_POR_TABLA = 2
_MIEMBROS_POR_DEFINICION = 2
_RELACIONES_POR_CONCEPTO = 7
_ARCHIVOS_POR_MODULO = 500
_ARCHIVOS_POR_PAQUETE = 50


@dataclass(frozen=True)
class Escala:
    """Tamaño del repositorio sintético. Los mínimos evitan pares repetidos o autollamadas."""

    archivos: int = 8_000
    simbolos_por_archivo: int = 22
    tablas: int = 600
    columnas_por_tabla: int = 15
    definiciones: int = 6_000
    conceptos: int = 400

    def __post_init__(self) -> None:
        if self.archivos < 1 or self.simbolos_por_archivo < 1:
            raise ValueError("archivos y simbolos_por_archivo deben ser >= 1")
        if self.simbolos < 4:
            raise ValueError("se necesitan al menos 4 símbolos para llamadas sin repetir pares")
        if self.tablas < 8:
            raise ValueError(
                "se necesitan al menos 8 tablas para claves foráneas sin repetir pares"
            )
        if self.conceptos < _RELACIONES_POR_CONCEPTO + 2:
            raise ValueError("se necesitan al menos 9 conceptos para relaciones sin repetir pares")
        if self.definiciones < 1 or self.columnas_por_tabla < 1:
            raise ValueError("definiciones y columnas_por_tabla deben ser >= 1")

    @classmethod
    def completa(cls) -> Escala:
        """200.000 nodos y 260.000 aristas."""
        return cls()

    @classmethod
    def reducida(cls, divisor: int) -> Escala:
        """La escala completa dividida por `divisor` (mismas proporciones), para pruebas rápidas."""
        if divisor < 1:
            raise ValueError("el divisor debe ser >= 1")
        c = cls()
        return cls(
            archivos=max(c.archivos // divisor, 1),
            simbolos_por_archivo=c.simbolos_por_archivo,
            tablas=max(c.tablas // divisor, 8),
            columnas_por_tabla=c.columnas_por_tabla,
            definiciones=max(c.definiciones // divisor, 1),
            conceptos=max(c.conceptos // divisor, _RELACIONES_POR_CONCEPTO + 2),
        )

    @property
    def simbolos(self) -> int:
        return self.archivos * self.simbolos_por_archivo

    @property
    def llamadas_dobles(self) -> int:
        return self.simbolos * _LLAMADAS_DOBLES_NUM // _LLAMADAS_DOBLES_DEN

    def esperado(self) -> dict[str, dict[str, int]]:
        """Recuentos que debe tener el grafo cargado, por capa (nodos) y por tipo de arista."""
        nodos = {
            "files": self.archivos,
            "symbols": self.simbolos,
            "db_tables": self.tablas,
            "db_columns": self.tablas * self.columnas_por_tabla,
            "definitions": self.definiciones,
            "concepts": self.conceptos,
        }
        aristas = {
            "CALLS": self.simbolos + self.llamadas_dobles,
            "USES_TABLE": self.archivos * _USOS_POR_ARCHIVO,
            "REFERENCES": self.tablas * _FK_POR_TABLA,
            "REALIZED_BY": self.definiciones * _MIEMBROS_POR_DEFINICION,
            "EXPRESSES": self.definiciones,
            "RELATES": self.conceptos * _RELACIONES_POR_CONCEPTO,
        }
        return {
            "nodos": nodos,
            "aristas": aristas,
            "total": {"nodos": sum(nodos.values()), "aristas": sum(aristas.values())},
        }


def nombre_simbolo(indice: int) -> str:
    return f"fn_{indice:07d}"


def ruta_archivo(i: int) -> str:
    modulo = i // _ARCHIVOS_POR_MODULO
    paquete = (i // _ARCHIVOS_POR_PAQUETE) % (_ARCHIVOS_POR_MODULO // _ARCHIVOS_POR_PAQUETE)
    return f"src/mod{modulo:03d}/pkg{paquete:02d}/modulo_{i:05d}.py"


def _destinos_llamada(indice: int, escala: Escala) -> list[int]:
    """Índices de los símbolos a los que llama el símbolo `indice` (1 o 2, distintos y no él mismo)."""
    n = escala.simbolos
    destinos = [(indice + 1) % n]
    if indice < escala.llamadas_dobles:
        destinos.append((indice + n // 2 + 1) % n)
    return destinos


def contenido_archivo(i: int, escala: Escala) -> str:
    """Código Python del archivo `i`: `simbolos_por_archivo` funciones, cada una con sus llamadas."""
    base = i * escala.simbolos_por_archivo
    bloques: list[str] = [f'"""Módulo sintético {i} (KC-CM-11-1)."""\n']
    for j in range(escala.simbolos_por_archivo):
        k = base + j
        llamadas = " + ".join(f"{nombre_simbolo(d)}(x)" for d in _destinos_llamada(k, escala))
        bloques.append(f"\ndef {nombre_simbolo(k)}(x):\n    return {llamadas}\n")
    return "".join(bloques)


def escribir_repositorio(destino: Path, escala: Escala) -> Iterator[str]:
    """Escribe los archivos fuente en `destino` y devuelve (como generador) sus rutas relativas."""
    for i in range(escala.archivos):
        ruta = ruta_archivo(i)
        archivo = destino / ruta
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text(contenido_archivo(i, escala), encoding="utf-8", newline="\n")
        yield ruta


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def crear_repositorio_git(destino: Path, escala: Escala) -> str:
    """Crea el repositorio en `destino` con un commit inicial y devuelve su SHA."""
    destino.mkdir(parents=True, exist_ok=True)
    for _ in escribir_repositorio(destino, escala):
        pass
    _git(destino, "init", "-q", "-b", "main")
    _git(destino, "config", "user.email", "bench@kallicode.local")
    _git(destino, "config", "user.name", "bench")
    _git(destino, "config", "commit.gpgsign", "false")
    _git(destino, "add", "-A")
    _git(destino, "commit", "-q", "-m", "repositorio sintético")
    return _git(destino, "rev-parse", "HEAD")


def archivos_a_modificar(escala: Escala, cantidad: int) -> list[int]:
    """`cantidad` índices de archivo repartidos de forma uniforme por todo el repositorio."""
    cantidad = min(cantidad, escala.archivos)
    paso = max(escala.archivos // cantidad, 1)
    return [i * paso for i in range(cantidad)]


def modificar_archivos(destino: Path, escala: Escala, indices: Sequence[int]) -> str:
    """Segundo commit: cada archivo gana una función nueva que llama a una existente.

    Efecto esperado sobre el grafo (ver `esperado_tras_refresco`): +1 símbolo y +1 arista CALLS por
    archivo modificado; el resto, idéntico. Devuelve el SHA del nuevo commit.
    """
    for i in indices:
        archivo = destino / ruta_archivo(i)
        base = i * escala.simbolos_por_archivo
        extra = f"\n\ndef nuevo_{i:05d}(x):\n    return {nombre_simbolo(base)}(x)\n"
        with archivo.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(extra)
    _git(destino, "add", "-A")
    _git(destino, "commit", "-q", "-m", f"modifica {len(indices)} archivos")
    return _git(destino, "rev-parse", "HEAD")


def esperado_tras_refresco(escala: Escala, modificados: int) -> dict[str, dict[str, int]]:
    """Recuentos esperados tras `modificar_archivos` con `modificados` archivos."""
    esperado = escala.esperado()
    nodos = dict(esperado["nodos"])
    aristas = dict(esperado["aristas"])
    nodos["symbols"] += modificados
    aristas["CALLS"] += modificados
    return {
        "nodos": nodos,
        "aristas": aristas,
        "total": {"nodos": sum(nodos.values()), "aristas": sum(aristas.values())},
    }


# ---------------------------------------------------------------------------
# Esquema, capa semántica y enlaces (lo que en producción salen de la base del cliente y del LLM)
# ---------------------------------------------------------------------------
def esquema(escala: Escala) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(tablas, claves foráneas) con la forma que devuelve extract_schema."""
    tablas = [
        {
            "name": f"tbl_{t:04d}",
            "esquema": _ESQUEMA,
            "columns": [
                {
                    "name": f"col_{c:02d}",
                    "type": _TIPOS_COLUMNA[c % len(_TIPOS_COLUMNA)],
                    "nullable": c % 3 != 0,
                }
                for c in range(escala.columnas_por_tabla)
            ],
        }
        for t in range(escala.tablas)
    ]
    n = escala.tablas
    fks = [
        {
            "from_schema": _ESQUEMA,
            "from_table": f"tbl_{t:04d}",
            "to_schema": _ESQUEMA,
            "to_table": f"tbl_{(t + d) % n:04d}",
        }
        for t in range(n)
        for d in (1, 7)
    ]
    return tablas, fks


def usos_de_tablas(escala: Escala) -> list[dict[str, str]]:
    """Usos archivo -> tabla: `_USOS_POR_ARCHIVO` tablas distintas por archivo."""
    n = escala.tablas
    return [
        {
            "file": ruta_archivo(i),
            "table": f"tbl_{(i * _USOS_POR_ARCHIVO + k) % n:04d}",
            "esquema": _ESQUEMA,
        }
        for i in range(escala.archivos)
        for k in range(_USOS_POR_ARCHIVO)
    ]


def ids_miembros(escala: Escala, d: int) -> list[int]:
    """Índices de los símbolos que realizan la definición `d`."""
    n = escala.simbolos
    base = (d * 29) % n
    return [(base + m) % n for m in range(_MIEMBROS_POR_DEFINICION)]


def definiciones(escala: Escala, id_por_nombre: dict[str, str]) -> list[dict[str, Any]]:
    """Definiciones con sus símbolos miembros; `id_por_nombre` mapea nombre de símbolo -> id."""
    return [
        {
            "id": f"def_{d:05d}",
            "name_en": f"Definition {d:05d}",
            "module": f"mod{d % 16:03d}",
            "kind": "capability",
            "description_en": f"Capacidad sintética {d}",
            "member_symbol_ids": [
                id_por_nombre[nombre_simbolo(m)]
                for m in ids_miembros(escala, d)
                if nombre_simbolo(m) in id_por_nombre
            ],
        }
        for d in range(escala.definiciones)
    ]


def ontologia(escala: Escala) -> dict[str, Any]:
    """Conceptos y relaciones entre ellos (`_RELACIONES_POR_CONCEPTO` por concepto, sin repetir)."""
    n = escala.conceptos
    return {
        "concepts": [
            {
                "name_en": f"Concept {k:04d}",
                "name_local": f"Concepto {k:04d}",
                "description_en": f"Concepto sintético {k}",
                "confidence": 0.9,
            }
            for k in range(n)
        ],
        "relations": [
            {"from": f"Concept {k:04d}", "to": f"Concept {(k + o) % n:04d}", "kind": "related_to"}
            for k in range(n)
            for o in range(1, _RELACIONES_POR_CONCEPTO + 1)
        ],
    }


def enlaces(escala: Escala) -> list[dict[str, Any]]:
    """Un enlace definición -> concepto por definición (aristas EXPRESSES)."""
    return [
        {
            "definition_id": f"def_{d:05d}",
            "concept_name_en": f"Concept {d % escala.conceptos:04d}",
            "confidence": 0.9,
        }
        for d in range(escala.definiciones)
    ]
