"""Compuerta de cobertura por ámbito (KC-CM-12-2): scripts/verificar_cobertura_modulos.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verificar_cobertura_modulos.py"


def _cargar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verificar_cobertura_modulos", SCRIPT)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


vc = _cargar()


def _clase(nombre: str, lineas: list[tuple[int, str | None]]) -> str:
    filas = []
    for i, (hits, ramas) in enumerate(lineas, start=1):
        extra = f' branch="true" condition-coverage="50% {ramas}"' if ramas else ""
        filas.append(f'<line number="{i}" hits="{hits}"{extra}/>')
    return (
        f'<class name="{nombre}" filename="packages/codemapping/src/kallicode_codemapping/{nombre}">'
        f"<lines>{''.join(filas)}</lines></class>"
    )


def _xml(tmp_path: Path, cargador: list[tuple[int, str | None]]) -> Path:
    completo = [(1, None)] * 4
    clases = [
        _clase(m, completo)
        for m in (
            "cm_queries.py",
            "consulta_grafo.py",
            "recuperacion.py",
            "cm_db.py",
            "security.py",
        )
    ]
    clases.append(_clase("graph_loader.py", cargador))
    clases.append('<class name="otro" filename="packages/core/src/x.py"><lines/></class>')
    ruta = tmp_path / "coverage.xml"
    ruta.write_text(
        f"<coverage><packages><package><classes>{''.join(clases)}</classes>"
        "</package></packages></coverage>",
        encoding="utf-8",
    )
    return ruta


def test_cuenta_lineas_y_ramas_como_coverage_report(tmp_path: Path) -> None:
    # 3 de 4 líneas y 1 de 2 ramas: (3 + 1) / (4 + 2) = 66,7 %
    ruta = _xml(tmp_path, [(1, "(1/2)"), (1, None), (1, None), (0, None)])
    cob = vc.leer_coverage_xml(ruta)["graph_loader.py"]
    assert (cob.lineas, cob.lineas_cubiertas, cob.ramas, cob.ramas_cubiertas) == (4, 3, 2, 1)
    assert cob.porcentaje == pytest.approx(66.666, abs=0.01)
    assert "x.py" not in vc.leer_coverage_xml(ruta)


def test_falla_si_un_ambito_queda_por_debajo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ruta = _xml(tmp_path, [(1, None), (0, None)])
    assert vc.main([str(ruta), "--minimo", "90"]) == 1
    salida = capsys.readouterr().out
    assert "FALLA cargador: 50.0 %" in salida
    assert "OK    consulta: 100.0 %" in salida


def test_pasa_si_todos_los_ambitos_cumplen(tmp_path: Path) -> None:
    assert vc.main([str(_xml(tmp_path, [(1, None)] * 10)), "--minimo", "90"]) == 0


def test_falla_si_falta_un_modulo_del_ambito() -> None:
    informe = vc.evaluar({"graph_loader.py": vc.Cobertura(1, 1, 0, 0)}, 90)
    assert any(linea.startswith("FALLA consulta: sin datos") for linea in informe)
    assert vc.Cobertura().porcentaje == 100.0
