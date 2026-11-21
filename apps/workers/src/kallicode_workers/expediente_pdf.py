"""PDF del expediente de auditoría de un ticket (KC-PT-08-3), con reportlab.

Muestra lo mismo que GET /audit/tickets/{numero} (servicios.expediente): solicitante,
integridad de la cadena, firmas de gates, decisiones del modelo por etapa y eventos.
Las fuentes estándar de PDF cubren el juego occidental (WinAnsi: tildes, «·», «—»); lo que
quede fuera, como un emoji, se reemplaza por «?» en vez de dibujar un recuadro.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_ESTILOS = getSampleStyleSheet()
_TITULO = _ESTILOS["Title"]
_SECCION = _ESTILOS["Heading2"]
_SUBSECCION = _ESTILOS["Heading4"]
_NORMAL = _ESTILOS["BodyText"]
_CELDA = ParagraphStyle("celda", parent=_NORMAL, fontSize=7.5, leading=9)
_CABECERA = ParagraphStyle("cabecera", parent=_CELDA, fontName="Helvetica-Bold")
_NOTA = ParagraphStyle("nota", parent=_NORMAL, fontSize=8, textColor=colors.grey)


def _texto(valor: object) -> str:
    """Texto plano imprimible con las fuentes estándar (sin escapar)."""
    if valor is None or valor == "":
        texto = "—"
    elif isinstance(valor, datetime):
        texto = valor.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    else:
        texto = str(valor)
    return texto.encode("cp1252", errors="replace").decode("cp1252")


def _limpio(valor: object) -> str:
    """Texto imprimible y escapado para Paragraph (que interpreta <b>, &, etc.)."""
    return escape(_texto(valor))


def _sello(sello: str | None) -> str:
    """Sello abreviado, en texto plano."""
    if not sello or len(sello) <= 20:
        return _texto(sello)
    return f"{sello[:8]}…{sello[-4:]}"


def _tabla(cabeceras: list[str], filas: list[list[object]], anchos: list[float]) -> Table:
    datos = [[Paragraph(_limpio(c), _CABECERA) for c in cabeceras]]
    datos += [[Paragraph(_limpio(c), _CELDA) for c in f] for f in filas]
    tabla = Table(datos, colWidths=[a * mm for a in anchos], repeatRows=1)
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0f4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8ccd4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return tabla


def generar_pdf(
    numero: str,
    datos: dict,
    *,
    generado_en: datetime,
    export_id: str,
    comprimir: bool = True,
) -> bytes:
    """PDF del expediente. `comprimir=False` deja el texto legible en los bytes (tests)."""
    buffer = io.BytesIO()
    titulo = f"Expediente de auditoría · {numero}"
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=titulo,
        author="Kallicode",
        pageCompression=1 if comprimir else 0,
    )

    def pie(canvas, documento) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        # drawString no interpreta marcas: texto plano, sin escapar.
        canvas.drawString(15 * mm, 8 * mm, _texto(f"Kallicode · {titulo}"))
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Página {documento.page}")
        canvas.restoreState()

    s = datos["solicitante"]
    integridad = datos["integridad"]
    partes: list = [
        Paragraph(_limpio(titulo), _TITULO),
        Paragraph(_limpio(f"Generado el {_texto(generado_en)} · exportación {export_id}"), _NOTA),
        Spacer(0, 4 * mm),
        Paragraph("Solicitante", _SECCION),
        Paragraph(
            f"<b>{_limpio(s['nombre'])}</b> · {_limpio(s['origen'])} · {_limpio(s['fecha'])}",
            _NORMAL,
        ),
        Paragraph("Integridad", _SECCION),
        Paragraph(
            "Cadena de hashes íntegra" if integridad["integra"] else "<b>Cadena de hashes ROTA</b>",
            _NORMAL,
        ),
        Paragraph(
            f"Hash raíz: <font name='Courier'>{_limpio(integridad['hash_raiz'])}</font>", _CELDA
        ),
        Paragraph("Firmas de gates", _SECCION),
    ]

    firmas = datos["firmas"]
    if firmas:
        partes.append(
            _tabla(
                ["Gate", "Acción", "Actor", "Fecha", "Comentario", "Sello"],
                [
                    [
                        f["gate"],
                        f["accion"],
                        f["actor"],
                        f["fecha"],
                        f["comentario"],
                        _sello(f["sello"]),
                    ]
                    for f in firmas
                ],
                [12, 22, 32, 30, 58, 26],
            )
        )
    else:
        partes.append(Paragraph("Sin firmas registradas.", _NOTA))

    partes.append(Paragraph("Decisiones del modelo por etapa", _SECCION))
    if datos["etapas"]:
        for etapa in datos["etapas"]:
            partes.append(Paragraph(_limpio(etapa["etapa"]), _SUBSECCION))
            partes.append(
                _tabla(
                    [
                        "Paso",
                        "Modelo",
                        "Tier",
                        "Confianza",
                        "Tokens",
                        "Duración (ms)",
                        "Validación",
                    ],
                    [
                        [
                            p["paso"],
                            p["modelo"],
                            p["tier"],
                            p["confianza"],
                            p["tokens"],
                            p["duracion_ms"],
                            p["validacion"],
                        ]
                        for p in etapa["pasos"]
                    ],
                    [36, 34, 16, 18, 16, 22, 38],
                )
            )
    else:
        partes.append(Paragraph("Sin pasos del modelo registrados.", _NOTA))

    partes.append(Paragraph("Eventos", _SECCION))
    eventos = datos["eventos"]
    if eventos:
        partes.append(
            _tabla(
                ["Fecha", "Actor", "Evento", "Resumen", "Sello"],
                [
                    [
                        e["creado_en"],
                        f"{e['actor_tipo']}: {e['actor_id'] or 'sistema'}",
                        e["evento"],
                        e["resumen"],
                        _sello(e["sello"]),
                    ]
                    for e in eventos
                ],
                [28, 32, 30, 64, 26],
            )
        )
    else:
        partes.append(Paragraph("Sin eventos registrados.", _NOTA))

    doc.build(partes, onFirstPage=pie, onLaterPages=pie)
    return buffer.getvalue()
