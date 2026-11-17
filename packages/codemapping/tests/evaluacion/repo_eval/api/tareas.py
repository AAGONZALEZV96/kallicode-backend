"""Tareas programadas."""

from facturacion.pagos import conciliar_pagos
from informes.exportar import exportar_csv
from informes.morosidad import informe_mensual


def tarea_conciliacion(session, banco):
    return conciliar_pagos(session, banco.extracto_del_dia())


def tarea_informe(conn, destino):
    destino.subir("morosos.pdf", informe_mensual(conn, "pdf"))


def tarea_informe_csv(conn, destino):
    destino.subir("morosos.csv", exportar_csv(informe_mensual(conn, "html")))
