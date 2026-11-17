"""Bandeja de salida de correo (patrón outbox)."""

PLANTILLAS = {
    "emision": "Le enviamos la factura {numero} por {importe}.",
    "pago": "Hemos recibido su pago de {importe}.",
}


def plantilla(nombre, **datos):
    return PLANTILLAS[nombre].format(**datos)


def enviar_correo(conn, destino, asunto, cuerpo):
    conn.execute(
        "INSERT INTO notificaciones (destino, asunto, cuerpo) VALUES (%s, %s, %s)",
        (destino, asunto, cuerpo),
    )
