"""Pruebas unitarias para ingesta en streaming y pool de procesos (KC-CM-04-1)."""

from kallicode_codemapping.ingest_repo import (
    SourceFile,
    dividir_en_lotes,
    parsear_fuentes_en_streaming,
)


def test_dividir_en_lotes() -> None:
    items = list(range(10))
    lotes = list(dividir_en_lotes(items, tamano_lote=3))
    assert len(lotes) == 4
    assert lotes[0] == [0, 1, 2]
    assert lotes[-1] == [9]


def test_parsear_fuentes_en_streaming_procesa_todos() -> None:
    archivos = [
        SourceFile(
            path=f"modulo_{i}.py",
            language="python",
            content=f"def funcion_{i}() -> int:\n    return {i}\n",
            sha256=f"hash_{i}",
        )
        for i in range(12)
    ]

    total_simbolos = 0
    for lote in parsear_fuentes_en_streaming(archivos, tamano_lote=4, max_workers=2):
        assert len(lote) == 4
        total_simbolos += len(lote)

    assert total_simbolos == 12
