"""Registro de cuarentena de CodeMapping (KC-CM-12-2, criterio 3).

Ninguna prueba en cuarentena sin ticket asociado y con fecha de resolución: el registro se valida
al cargar, el hook convierte en fallo un skip no registrado o vencido, y el registro real no puede
tener entradas vencidas ni apuntar a pruebas que ya no existen.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

RAIZ_PAQUETE = Path(__file__).resolve().parents[1]


def _cargar_conftest() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cm_conftest", RAIZ_PAQUETE / "conftest.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # dataclass lo busca al procesar la clase
    spec.loader.exec_module(modulo)
    return modulo


cf = _cargar_conftest()
HOY = date(2026, 11, 18)


def _escribir(tmp_path: Path, contenido: str) -> Path:
    ruta = tmp_path / "cuarentena.yaml"
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def test_registro_valido_se_carga(tmp_path: Path) -> None:
    ruta = _escribir(
        tmp_path,
        """
cuarentena:
  - prueba: tests/test_x.py::test_y
    motivo: depende de un servicio externo
    ticket: KC-CM-12-4
    resolver_antes: 2027-01-31
""",
    )
    registro = cf.cargar_registro(ruta)
    entrada = registro["tests/test_x.py::test_y"]
    assert entrada.ticket == "KC-CM-12-4"
    assert entrada.resolver_antes == date(2027, 1, 31)


def test_registro_inexistente_o_vacio_es_valido(tmp_path: Path) -> None:
    assert cf.cargar_registro(tmp_path / "no_existe.yaml") == {}
    assert cf.cargar_registro(_escribir(tmp_path, "cuarentena: []\n")) == {}


@pytest.mark.parametrize(
    ("fila", "mensaje"),
    [
        ("    motivo: m\n    ticket: KC-CM-12-2\n    resolver_antes: 2027-01-01", "sin prueba"),
        ("    prueba: t::a\n    ticket: KC-CM-12-2\n    resolver_antes: 2027-01-01", "sin motivo"),
        ("    prueba: t::a\n    motivo: m\n    resolver_antes: 2027-01-01", "sin ticket"),
        ("    prueba: t::a\n    motivo: m\n    ticket: KC-CM-12-2", "sin resolver_antes"),
        (
            "    prueba: t::a\n    motivo: m\n    ticket: JIRA-1\n    resolver_antes: 2027-01-01",
            "ticket inválido",
        ),
        (
            "    prueba: t::a\n    motivo: m\n    ticket: KC-CM-12-2\n    resolver_antes: pronto",
            "no es una fecha",
        ),
    ],
)
def test_entrada_incompleta_detiene_la_sesion(tmp_path: Path, fila: str, mensaje: str) -> None:
    ruta = _escribir(tmp_path, "cuarentena:\n  -\n" + fila + "\n")
    with pytest.raises(pytest.UsageError, match=mensaje):
        cf.cargar_registro(ruta)


def test_entrada_repetida_detiene_la_sesion(tmp_path: Path) -> None:
    fila = (
        "  - prueba: t::a\n    motivo: m\n    ticket: KC-CM-12-2\n    resolver_antes: 2027-01-01\n"
    )
    with pytest.raises(pytest.UsageError, match="repetida"):
        cf.cargar_registro(_escribir(tmp_path, "cuarentena:\n" + fila + fila))


def test_veredicto_skip_no_registrado_falla() -> None:
    problema = cf.veredicto("tests/test_x.py::test_y", {}, HOY)
    assert problema is not None and "no está en cuarentena.yaml" in problema


def test_veredicto_registrado_vigente_se_permite_y_vencido_falla() -> None:
    entrada = cf.EntradaCuarentena("t::a", "m", "KC-CM-12-2", date(2026, 11, 18))
    assert cf.veredicto("t::a", {"t::a": entrada}, HOY) is None
    vencida = cf.veredicto("t::a", {"t::a": entrada}, date(2026, 11, 19))
    assert vencida is not None and "KC-CM-12-2" in vencida


def test_el_registro_real_no_tiene_entradas_vencidas_ni_huerfanas() -> None:
    registro = cf.cargar_registro(cf.REGISTRO_CUARENTENA)
    hoy = date.today()
    vencidas = [e.prueba for e in registro.values() if e.vencida(hoy)]
    assert vencidas == [], f"entradas vencidas: {vencidas}"
    for prueba in registro:
        archivo, _, nombre = prueba.partition("::")
        ruta = RAIZ_PAQUETE / archivo
        assert ruta.is_file(), f"{prueba}: el archivo no existe"
        funcion = nombre.split("[", 1)[0].split("::")[-1]
        assert f"def {funcion}(" in ruta.read_text(encoding="utf-8"), f"{prueba}: no existe"


MUESTRA = """
import pytest

def test_pasa():
    assert True

@pytest.mark.skip(reason="sin registrar")
def test_sin_registrar():
    pass

@pytest.mark.skip(reason="registrada")
def test_registrada():
    pass

@pytest.mark.xfail(reason="sin registrar", strict=True)
def test_xfail_sin_registrar():
    raise AssertionError
"""


def _correr_muestra(tmp_path: Path, estricto: bool) -> subprocess.CompletedProcess[str]:
    """Suite mínima con el conftest real, en un directorio fuera del repo."""
    shutil.copy(RAIZ_PAQUETE / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "cuarentena.yaml").write_text(
        "cuarentena:\n"
        "  - prueba: test_muestra.py::test_registrada\n"
        "    motivo: servicio externo\n"
        "    ticket: KC-CM-12-2\n"
        "    resolver_antes: 2999-12-31\n",
        encoding="utf-8",
    )
    (tmp_path / "test_muestra.py").write_text(MUESTRA, encoding="utf-8")
    entorno = {k: v for k, v in os.environ.items() if k != cf.VARIABLE_ESTRICTA}
    entorno["PYTHONIOENCODING"] = "utf-8"
    if estricto:
        entorno[cf.VARIABLE_ESTRICTA] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "--rootdir", "."],
        check=False,
        cwd=tmp_path,
        env=entorno,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_el_hook_convierte_un_skip_no_registrado_en_fallo(tmp_path: Path) -> None:
    salida = _correr_muestra(tmp_path, estricto=True)
    assert salida.returncode == 1, salida.stdout
    # El skip por marca ocurre en setup (sale como error); el xfail, en la llamada (fallo).
    assert "1 failed, 1 passed, 1 skipped, 1 error" in salida.stdout, salida.stdout
    assert "[cuarentena] test_muestra.py::test_sin_registrar" in salida.stdout
    assert "[cuarentena] test_muestra.py::test_xfail_sin_registrar" in salida.stdout


def test_sin_modo_estricto_un_skip_sigue_siendo_skip(tmp_path: Path) -> None:
    salida = _correr_muestra(tmp_path, estricto=False)
    assert salida.returncode == 0, salida.stdout
    assert "1 passed, 2 skipped, 1 xfailed" in salida.stdout, salida.stdout
