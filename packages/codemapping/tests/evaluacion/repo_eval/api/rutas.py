"""Controladores HTTP de la API de facturación."""

from facturacion.pagos import registrar_pago, saldo_pendiente
from facturacion.servicio_facturas import anular_factura, emitir_factura
from informes.morosidad import clientes_morosos

from .cache import Cache

CACHE = Cache(ttl_s=300)


def post_factura(req):
    factura = emitir_factura(req.session, req.conn, req.json["cliente_id"], req.json["lineas"])
    return {"id": factura.id}, 201


def delete_factura(req, factura_id):
    anular_factura(req.session, req.conn, factura_id)
    return None, 204


def post_pago(req, factura_id):
    pago = registrar_pago(req.session, factura_id, req.json["importe"])
    return {"id": pago.id, "saldo": saldo_pendiente(req.session, pago.factura)}, 201


def get_morosos(req):
    morosos = CACHE.get("morosos")
    if morosos is None:
        morosos = clientes_morosos(req.conn)
        CACHE.guardar("morosos", morosos)
    return morosos, 200
