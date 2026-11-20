"""Exportación de informes: el motor se elige por formato en tiempo de ejecución."""

from .motores import render_html, render_pdf

MOTORES = {"pdf": render_pdf, "html": render_html}


def exportar(filas, formato):
    return MOTORES[formato](filas)


def exportar_csv(filas):
    return "\n".join(";".join(str(c) for c in fila) for fila in filas)
