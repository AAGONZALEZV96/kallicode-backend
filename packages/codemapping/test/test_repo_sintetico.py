"""Pruebas del generador de repositorio sintético de KC-CM-11-1 (sin base de datos)."""

from pathlib import Path

import pytest
from kallicode_codemapping.repo_sintetico import (
    Escala,
    crear_repositorio_git,
    definiciones,
    enlaces,
    esperado_tras_refresco,
    ids_miembros,
    modificar_archivos,
    nombre_simbolo,
    ontologia,
    usos_de_tablas,
)


def test_escala_completa_es_200k_nodos_y_260k_aristas() -> None:
    esperado = Escala.completa().esperado()
    assert esperado["total"] == {"nodos": 200_000, "aristas": 260_000}
    assert esperado["aristas"]["CALLS"] == 214_000


@pytest.mark.parametrize("divisor", [1, 10, 100])
def test_total_es_suma_de_las_capas(divisor: int) -> None:
    esperado = Escala.reducida(divisor).esperado()
    assert esperado["total"]["nodos"] == sum(esperado["nodos"].values())
    assert esperado["total"]["aristas"] == sum(esperado["aristas"].values())


def test_divisor_invalido() -> None:
    with pytest.raises(ValueError):
        Escala.reducida(0)
    with pytest.raises(ValueError):
        Escala(archivos=0)


def test_nombres_de_simbolo_unicos() -> None:
    nombres = {nombre_simbolo(i) for i in range(Escala.completa().simbolos)}
    assert len(nombres) == Escala.completa().simbolos


def test_esquema_y_semantica_sin_pares_duplicados() -> None:
    escala = Escala.reducida(100)
    usos = usos_de_tablas(escala)
    pares = {tuple(u.values()) for u in usos}
    assert len(pares) == len(usos) == escala.esperado()["aristas"]["USES_TABLE"]
    assert len(ids_miembros(escala, 0)) == 2
    onto = ontologia(escala)
    assert onto  # estructura no vacía
    assert definiciones(escala, {}) is not None
    assert enlaces(escala) is not None


def test_repositorio_git_y_modificacion(tmp_path: Path) -> None:
    escala = Escala.reducida(100)
    repo = tmp_path / "repo"
    repo.mkdir()
    base = crear_repositorio_git(repo, escala)
    assert base
    archivos = [p for p in repo.rglob("*.py")]
    assert len(archivos) == escala.archivos
    nuevo = modificar_archivos(repo, escala, [0, 1])
    assert nuevo and nuevo != base
    esperado = esperado_tras_refresco(escala, 2)
    assert esperado["nodos"]["symbols"] == escala.simbolos + 2
    assert esperado["aristas"]["CALLS"] == escala.esperado()["aristas"]["CALLS"] + 2
