"""Asientos contables de las facturas (esquema contab)."""

from .auditoria import registrar_auditoria


def generar_asiento(conn, factura_id, importe):
    conn.execute(
        "INSERT INTO contab.asientos (factura_id, debe, haber) VALUES (%s, %s, 0)",
        (factura_id, importe),
    )


def revertir_asiento(conn, factura_id):
    conn.execute("UPDATE contab.asientos SET anulado = true WHERE factura_id = %s", (factura_id,))
    registrar_auditoria(conn, "asiento", factura_id, "revertido")
