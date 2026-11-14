"""Acceso con psycopg y SQL crudo."""


def pedidos_pendientes(conn):
    return conn.execute(
        """
        SELECT o.id, u.email
          FROM orders AS o
          JOIN "users" u ON u.id = o.user_id
         WHERE o.status = 'pendiente'
        """
    ).fetchall()


def marcar_enviado(conn, pedido_id):
    conn.execute("UPDATE orders SET status = 'enviado' WHERE id = %s", (pedido_id,))
    conn.execute(
        "INSERT INTO audit_log (entidad, entidad_id, accion) VALUES ('orders', %s, 'enviado')",
        (pedido_id,),
    )


def productos_de_categoria(conn, categoria):
    sql = "SELECT p.* FROM products p, categories c " + "WHERE p.category_id = c.id AND c.slug = %s"
    return conn.execute(sql, (categoria,)).fetchall()
