"""Cobro y conciliación de pagos."""

from notificaciones.avisos import notificar_pago

from .modelos import Factura, Pago
from .utilidades import redondear


def registrar_pago(session, factura_id, importe):
    factura = session.get(Factura, factura_id)
    pago = Pago(factura_id=factura.id, importe_pagado=redondear(importe))
    session.add(pago)
    notificar_pago(factura, pago)
    return pago


def saldo_pendiente(session, factura):
    pagado = sum(p.importe_pagado for p in session.query(Pago).filter_by(factura_id=factura.id))
    return redondear(factura.total_bruto() - pagado)


def conciliar_pagos(session, extracto):
    conciliados = []
    for movimiento in extracto:
        conciliados.append(registrar_pago(session, movimiento.factura_id, movimiento.importe))
    return conciliados
