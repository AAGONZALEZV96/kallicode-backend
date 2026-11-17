"""Motores de renderizado de informes."""


def render_pdf(filas):
    return b"%PDF-1.7\n" + "\n".join(str(f) for f in filas).encode()


def render_html(filas):
    return "<table>" + "".join(f"<tr><td>{f}</td></tr>" for f in filas) + "</table>"
