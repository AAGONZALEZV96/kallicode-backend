"""Informes de morosidad."""

from facturacion.utilidades import formatear_importe

from .exportar import exportar

SQL_MOROSOS = """
    WITH pendientes AS (
        SELECT f.cliente_id, f.id AS factura_id
          FROM facturas f
         WHERE NOT EXISTS (SELECT 1 FROM pagos p WHERE p.factura_id = f.id)
    )
    SELECT c.nif, count(*) AS impagadas
      FROM pendientes pe
      JOIN clientes c ON c.id = pe.cliente_id
     GROUP BY c.nif
"""


def clientes_morosos(conn):
    return [(nif, formatear_importe(n)) for nif, n in conn.execute(SQL_MOROSOS).fetchall()]


def informe_mensual(conn, formato="pdf"):
    return exportar(clientes_morosos(conn), formato)
