"""Auditor de logs de ingesta: ni credenciales, ni rutas absolutas, ni código (KC-CM-12-6).

Revisa un log ya escrito (una línea JSON por evento, la salida de logging_setup.JsonFormatter) y
lista cada valor que contenga algo sensible. Se usa en CI sobre la ingesta de prueba y en staging
sobre las últimas líneas de una ingesta real:

    python -m kallicode_codemapping.revision_logs ingesta.jsonl --minimo-lineas 1000 \
        --prohibido "$TOKEN_DEL_REPO" --prohibido /srv/kallicode/workdir

Las reglas son las de ``redaccion`` (lo que el formateador habría redactado) más los literales
de ``--prohibido`` (los secretos y rutas concretos de la ingesta revisada). Una línea que no es
JSON también se revisa entera: la escribió algo que no pasó por el formateador.

Sale con 1 si hay hallazgos o si el log tiene menos líneas que ``--minimo-lineas``. El informe
nunca repite el valor encontrado: solo línea, campo y categoría.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .redaccion import hallazgos


@dataclass(frozen=True)
class Hallazgo:
    linea: int
    campo: str
    categoria: str


@dataclass
class InformeLogs:
    lineas: int = 0
    lineas_no_json: int = 0
    hallazgos: list[Hallazgo] = field(default_factory=list)

    def a_dict(self) -> dict[str, Any]:
        por_categoria: dict[str, int] = {}
        for h in self.hallazgos:
            por_categoria[h.categoria] = por_categoria.get(h.categoria, 0) + 1
        return {
            "lineas": self.lineas,
            "lineas_no_json": self.lineas_no_json,
            "hallazgos": len(self.hallazgos),
            "por_categoria": por_categoria,
            "detalle": [asdict(h) for h in self.hallazgos[:200]],
        }


def _valores(valor: Any, ruta: str) -> Iterator[tuple[str, str]]:
    """(campo, texto) de cada clave y cada valor de texto del evento.

    Una clave también puede filtrar (p. ej. un dict indexado por rutas): se revisa como un valor
    más y, para no repetirla en el informe, el campo la nombra por su posición.
    """
    if isinstance(valor, str):
        yield ruta, valor
    elif isinstance(valor, dict):
        for i, (k, v) in enumerate(valor.items()):
            clave = str(k)
            nombre = clave if clave.isidentifier() and not hallazgos(clave) else f"<clave#{i}>"
            campo = f"{ruta}.{nombre}" if ruta else nombre
            yield campo, clave
            yield from _valores(v, campo)
    elif isinstance(valor, list):
        for i, v in enumerate(valor):
            yield from _valores(v, f"{ruta}[{i}]")


def auditar(lineas: Iterable[str], prohibidos: Sequence[str] = ()) -> InformeLogs:
    informe = InformeLogs()
    literales = [p for p in prohibidos if p]
    for n, cruda in enumerate(lineas, start=1):
        cruda = cruda.rstrip("\r\n")
        if not cruda.strip():
            continue
        informe.lineas += 1
        try:
            evento = json.loads(cruda)
        except json.JSONDecodeError:
            evento = None
        if isinstance(evento, dict):
            pares = list(_valores(evento, ""))
        else:
            informe.lineas_no_json += 1
            pares = [("<linea>", cruda)]
        vistos: set[tuple[str, str]] = set()
        for campo, texto in pares:
            categorias = hallazgos(texto)
            categorias += ["prohibido"] * any(lit in texto for lit in literales)
            for categoria in categorias:
                if (campo, categoria) not in vistos:
                    vistos.add((campo, categoria))
                    informe.hallazgos.append(Hallazgo(n, campo, categoria))
    return informe


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", type=Path, help="Fichero JSONL (una línea por evento); '-' = stdin")
    parser.add_argument("--minimo-lineas", type=int, default=0)
    parser.add_argument(
        "--prohibido",
        action="append",
        default=[],
        help="Literal que no puede aparecer (token, contraseña, ruta del workdir). Repetible.",
    )
    args = parser.parse_args(argv)
    if str(args.log) == "-":
        informe = auditar(sys.stdin, args.prohibido)
    else:
        with args.log.open(encoding="utf-8", errors="replace") as f:
            informe = auditar(f, args.prohibido)
    salida = informe.a_dict()
    salida["minimo_lineas"] = args.minimo_lineas
    salida["ok"] = not informe.hallazgos and informe.lineas >= args.minimo_lineas
    print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0 if salida["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
