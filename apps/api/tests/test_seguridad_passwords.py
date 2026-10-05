import pytest
from kallicode_core.errors import AppError
from kallicode_core.seguridad import validar_politica_password


def test_password_falla_por_longitud():
    """Falla si tiene menos de 12 caracteres, aunque sea compleja."""
    with pytest.raises(AppError) as exc_info:
        validar_politica_password("Cort1!")

    # Extraemos todos los datos del error como texto bruto
    error_data = str(exc_info.value.args) + str(vars(exc_info.value))

    assert "AUTH_PASSWORD_DEBIL" in error_data
    assert "mínimo 12 caracteres" in error_data


def test_password_falla_por_entropia_zxcvbn():
    """Falla si la entropía es baja (ej. patrones súper comunes de diccionario)."""
    # Cumple todo: 14 caracteres, mayúscula, minúsculas y números.
    # Pero zxcvbn le dará score 0 o 1 porque es un patrón hiper predecible.
    password_debil = "Password123456"

    with pytest.raises(AppError) as exc_info:
        validar_politica_password(
            password_debil, email="usuario.prueba@empresa.com", org_nombre="kallicode"
        )

    error_data = str(exc_info.value.args) + str(vars(exc_info.value))

    assert "AUTH_PASSWORD_DEBIL" in error_data
    assert "entropía" in error_data


def test_password_exitosa():
    """Pasa silenciosamente si la contraseña es verdaderamente robusta."""
    password_fuerte = "Tr4kt0r_M4d3ra_2026!$"

    validar_politica_password(
        password_fuerte, email="usuario.prueba@empresa.com", org_nombre="kallicode"
    )
