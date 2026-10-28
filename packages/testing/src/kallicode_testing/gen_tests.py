"""Generador de código de pruebas automatizado."""


def format_test_code(test_name: str, test_body: str) -> str:
    """Formatea un bloque de prueba con la firma estándar de pytest."""
    if not test_name.startswith("test_"):
        test_name = f"test_{test_name}"
    return f"def {test_name}() -> None:\n    {test_body}\n"
