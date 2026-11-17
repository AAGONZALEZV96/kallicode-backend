"""Utilidades numéricas compartidas por la facturación."""

from decimal import ROUND_HALF_EVEN, Decimal


def redondear(valor):
    return Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def formatear_importe(valor):
    return f"{redondear(valor):,.2f} EUR"


def validar(valor):
    if valor is None or valor < 0:
        raise ValueError("importe no válido")
    return valor
