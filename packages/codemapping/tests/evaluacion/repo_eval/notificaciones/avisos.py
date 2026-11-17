"""Avisos al cliente sobre sus facturas y pagos."""

from facturacion.utilidades import formatear_importe

from .correo import enviar_correo, plantilla


def notificar_emision(cliente, factura, total):
    cuerpo = plantilla("emision", numero=factura.id, importe=formatear_importe(total))
    enviar_correo(cliente.conn, cliente.email, "Nueva factura", cuerpo)


def notificar_pago(factura, pago):
    cuerpo = plantilla("pago", importe=formatear_importe(pago.importe_pagado))
    enviar_correo(factura.conn, factura.email, "Pago recibido", cuerpo)
