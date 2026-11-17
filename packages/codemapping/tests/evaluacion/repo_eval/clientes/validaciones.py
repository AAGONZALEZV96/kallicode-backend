"""Validación de los datos del cliente."""

import re

_NIF = re.compile(r"^[0-9XYZ][0-9]{7}[A-Z]$")


def validar(dato):
    if not dato:
        raise ValueError("dato obligatorio")
    return dato


def validar_nif(nif):
    if not _NIF.match(nif):
        raise ValueError("NIF no válido")
    return nif


def validar_cliente(cliente):
    validar(cliente)
    validar_nif(cliente.nif)
    if not cliente.activo:
        raise ValueError("cliente dado de baja")
    return cliente
