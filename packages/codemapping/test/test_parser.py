"""Pruebas para corrección de AST y llamadas (KC-CM-04-5, CM-F15)."""

from kallicode_codemapping.ingest_repo import SourceFile
from kallicode_codemapping.parser_treesitter import parse_file


def test_parser_firma_python_sin_cuerpo() -> None:
    codigo_py = """def calcular_total(precio: float, tasa: float = 0.19) -> float:
        resultado = precio * (1 + tasa)
        return resultado
"""
    sf = SourceFile(path="servicio.py", language="python", content=codigo_py, sha256="abc1")
    simbolos = parse_file(sf)

    assert len(simbolos) >= 1
    fn = next(s for s in simbolos if s.name == "calcular_total")
    assert "resultado = precio" not in fn.signature
    # La firma en Python debe terminar en ':'
    assert fn.signature == "def calcular_total(precio: float, tasa: float = 0.19) -> float:"


def test_parser_llamadas_anidadas_no_contaminan_padre() -> None:
    codigo_ts = """class Servicio {
        metodoPadre() {
            llamadaExternaDirecta();
            const callback = () => {
                llamadaInternaAnidada();
            };
        }
    }
"""
    sf = SourceFile(path="servicio.ts", language="typescript", content=codigo_ts, sha256="abc2")
    simbolos = parse_file(sf)

    metodo = next((s for s in simbolos if s.name == "metodoPadre"), None)
    if metodo:
        assert "llamadaExternaDirecta" in metodo.calls
        # La llamada del callback anidado no debe figurar en las llamadas directas del padre
        assert "llamadaInternaAnidada" not in metodo.calls


def test_parser_no_produce_recursion_error_con_anidamiento_profundo() -> None:
    # Simulación de código minificado profundamente anidado
    profundidad = 80
    codigo_anidado = "(((" * profundidad + "1" + ")))" * profundidad
    codigo_completo = f"function minificado() {{ return {codigo_anidado}; }}"

    sf = SourceFile(
        path="bundle.min.js", language="javascript", content=codigo_completo, sha256="abc3"
    )
    simbolos = parse_file(sf)
    # Debe procesarse sin RecursionError
    assert isinstance(simbolos, list)
