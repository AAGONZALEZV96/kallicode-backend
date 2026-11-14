"""SQL crudo vía sqlalchemy.text con nombres cualificados por esquema."""

from sqlalchemy import text

TABLA_AUDITORIA = "events"

MOROSOS = text(
    """
    WITH pendientes AS (
        SELECT i.customer_id, sum(il.amount) AS deuda
          FROM billing.invoices i
          JOIN billing.invoice_lines il ON il.invoice_id = i.id
         WHERE i.paid_at IS NULL
         GROUP BY i.customer_id
    )
    SELECT bc.legal_name, p.deuda
      FROM pendientes p
      JOIN billing.customers bc ON bc.id = p.customer_id
    """
)


def registrar(session, tipo):
    # Nombre de tabla dinámico: no se puede resolver estáticamente (acceso no detectable).
    session.execute(text(f"INSERT INTO {TABLA_AUDITORIA} (tipo) VALUES (:tipo)"), {"tipo": tipo})


def reembolsar(session, pago_id):
    session.execute(
        text("UPDATE billing.payments SET refunded = true WHERE id = :id"), {"id": pago_id}
    )
    session.execute(
        text(
            "INSERT INTO billing.refunds (payment_id) "
            "SELECT id FROM billing.payments WHERE id = :id"
        ),
        {"id": pago_id},
    )
