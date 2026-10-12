import pytest
from kallicode_core.errors import CATALOGO_ERRORES, AppError


def test_catalogo_tiene_definiciones_validas() -> None:
    assert CATALOGO_ERRORES

    for codigo, definicion in CATALOGO_ERRORES.items():
        assert codigo == codigo.upper()
        http = definicion["http"]
        assert isinstance(http, int)
        assert 400 <= http <= 599
        assert definicion["descripcion"]


def test_app_error_acepta_codigo_catalogado() -> None:
    error = AppError(
        "NO_AUTENTICADO",
        401,
        "Token inválido",
    )

    assert error.codigo == "NO_AUTENTICADO"
    assert error.http == 401


def test_app_error_rechaza_codigo_no_catalogado() -> None:
    with pytest.raises(ValueError, match="no está registrado"):
        AppError(
            "CODIGO_NO_DOCUMENTADO",
            400,
            "Error de prueba",
        )


def test_app_error_rechaza_codigo_http_incorrecto() -> None:
    with pytest.raises(ValueError, match="declara HTTP"):
        AppError(
            "NO_AUTENTICADO",
            403,
            "HTTP incorrecto",
        )
