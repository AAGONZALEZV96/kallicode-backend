"""Emisión y anulación de facturas."""

from clientes.validaciones import validar_cliente
from notificaciones.avisos import notificar_emision

from .auditoria import registrar_auditoria
from .contabilidad import generar_asiento, revertir_asiento
from .impuestos import calcular_iva
from .modelos import Cliente, Factura, LineaFactura


def emitir_factura(session, conn, cliente_id, lineas):
    cliente = session.get(Cliente, cliente_id)
    validar_cliente(cliente)
    factura = Factura(cliente_id=cliente.id)
    factura.lineas = [
        LineaFactura(producto_id=p, cantidad=c, precio=precio) for p, c, precio in lineas
    ]
    session.add(factura)
    session.flush()
    total = total_con_impuestos(conn, factura)
    generar_asiento(conn, factura.id, total)
    registrar_auditoria(conn, "factura", factura.id, "emitida")
    notificar_emision(cliente, factura, total)
    return factura


def total_con_impuestos(conn, factura):
    bruto = factura.total_bruto()
    return bruto + calcular_iva(conn, bruto)


def anular_factura(session, conn, factura_id):
    factura = session.get(Factura, factura_id)
    factura.anulada = True
    revertir_asiento(conn, factura_id)
    registrar_auditoria(conn, "factura", factura_id, "anulada")
