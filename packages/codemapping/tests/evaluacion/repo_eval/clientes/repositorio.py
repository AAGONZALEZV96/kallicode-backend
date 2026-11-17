"""Acceso a clientes en SQL crudo."""

from facturacion.auditoria import registrar_auditoria


def buscar_cliente(conn, cliente_id):
    return conn.execute(
        """
        SELECT c.id, c.nif, count(f.id) AS facturas
          FROM clientes c
          LEFT JOIN facturas f ON f.cliente_id = c.id
         WHERE c.id = %s
         GROUP BY c.id, c.nif
        """,
        (cliente_id,),
    ).fetchone()


def dar_de_baja(conn, cliente_id):
    conn.execute("UPDATE clientes SET activo = false WHERE id = %s", (cliente_id,))
    registrar_auditoria(conn, "cliente", cliente_id, "baja")
