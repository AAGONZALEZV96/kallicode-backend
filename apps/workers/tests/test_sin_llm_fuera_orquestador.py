"""Verifica que no hay llamadas al LLM fuera del orquestador (KC-BE-04-2).

Tras KC-BE-04-2, el normalizador ya no invoca `ejecutar_paso`: solo el
orquestador (apps/workers/src/kallicode_workers/orquestador/) llama al
motor LLM. Este test es un guardarraíl estático contra regresiones.

No requiere DB.
"""

from __future__ import annotations

from pathlib import Path

# Raíz de los workers (donde NO debe aparecer `ejecutar_paso`)
_WORKERS_SRC = Path(__file__).resolve().parents[1] / "src" / "kallicode_workers"
# Excepción explícita: el orquestador es el único llamador permitido
_ORQUESTADOR = _WORKERS_SRC / "orquestador"


def test_no_hay_llamadas_a_ejecutar_paso_fuera_del_orquestador() -> None:
    """Ningún módulo de apps/workers, fuera del orquestador, invoca el LLM."""
    infracciones: list[str] = []
    for path in _WORKERS_SRC.rglob("*.py"):
        if _ORQUESTADOR in path.parents or path == _ORQUESTADOR / "__init__.py":
            continue
        contenido = path.read_text(encoding="utf-8")
        if "ejecutar_paso" in contenido:
            infracciones.append(str(path.relative_to(_WORKERS_SRC)))
    assert not infracciones, (
        f"Se ha encontrado 'ejecutar_paso' fuera del orquestador: {infracciones}. "
        "Solo apps/workers/src/kallicode_workers/orquestador/ puede invocar al LLM."
    )


def test_normalizer_no_importa_llm() -> None:
    """El normalizador no debe importar el cliente LLM."""
    normalizer = _WORKERS_SRC / "normalizer.py"
    contenido = normalizer.read_text(encoding="utf-8")
    assert "from kallicode_core.llm" not in contenido, (
        "normalizer.py no debe importar el cliente LLM: el triage vive en el orquestador."
    )
    assert "import kallicode_core.llm" not in contenido, (
        "normalizer.py no debe importar el cliente LLM: el triage vive en el orquestador."
    )
