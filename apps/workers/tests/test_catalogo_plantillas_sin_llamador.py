"""Test estático: ninguna plantilla del catálogo queda sin llamador.

Criterio de aceptación de KC-BE-06-2: la métrica «plantillas del catálogo
sin llamador» vale 0. Se verifica comparando PLANTILLAS con las llamadas
literales a ejecutar_paso / ejecutar_y_publicar en el paquete orquestador.

Excepciones documentadas: orchestrator.route se invocará desde politica.py
cuando decidir() no sea trivial; se cierra en un ticket posterior.
"""

from __future__ import annotations

import ast
from pathlib import Path

from kallicode_core.llm.plantillas import PLANTILLAS

ORQUESTADOR = Path(__file__).resolve().parents[1] / "src" / "kallicode_workers" / "orquestador"

EXCLUIDAS: dict[str, str] = {
    "orchestrator.route": "KC-BE-06-3 (llamador desde politica.py, fuera de este ticket)",
}

_FUNCIONES_LLAMADORAS = frozenset(
    {
        "ejecutar_paso",
        "_ejecutar_y_publicar",
        "ejecutar_y_publicar",
    }
)


def _pasos_llamados() -> set[str]:
    llamados: set[str] = set()
    for py in ORQUESTADOR.glob("*.py"):
        try:
            arbol = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            if not _es_llamadora(nodo):
                continue
            if nodo.args and isinstance(nodo.args[0], ast.Constant):
                valor = nodo.args[0].value
                if isinstance(valor, str):
                    llamados.add(valor)
    return llamados


def _es_llamadora(nodo: ast.Call) -> bool:
    fn = nodo.func
    return (isinstance(fn, ast.Name) and fn.id in _FUNCIONES_LLAMADORAS) or (
        isinstance(fn, ast.Attribute) and fn.attr in _FUNCIONES_LLAMADORAS
    )


def test_ninguna_plantilla_sin_llamador() -> None:
    llamados = _pasos_llamados()
    huerfanas = set(PLANTILLAS) - llamados - set(EXCLUIDAS)
    assert not huerfanas, (
        "Plantillas del catálogo sin llamador: "
        f"{sorted(huerfanas)}. "
        "Enchúfalas o añádelas a EXCLUIDAS con su ticket."
    )


def test_exclusiones_son_plantillas_reales() -> None:
    fantasma = set(EXCLUIDAS) - set(PLANTILLAS)
    assert not fantasma, f"Exclusiones que no son plantillas: {sorted(fantasma)}"
