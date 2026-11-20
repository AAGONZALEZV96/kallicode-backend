"""Cálculo del IVA con la tasa vigente."""

from .utilidades import redondear, validar


def tasa_vigente(conn, tipo):
    fila = conn.execute(
        "SELECT tasa FROM impuestos WHERE tipo = %s AND vigente_hasta IS NULL", (tipo,)
    ).fetchone()
    return fila[0]


def calcular_iva(conn, base, tipo="general"):
    validar(base)
    return redondear(base * tasa_vigente(conn, tipo))
