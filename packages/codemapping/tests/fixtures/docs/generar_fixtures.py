"""Genera las 5 fixtures de documentación de KC-CM-08-1.

    uv run python packages/codemapping/tests/fixtures/docs/generar_fixtures.py

Mencionan símbolos reales de tests/fixtures/repo_ref (BillingService, calculate_total, Factura,
InvoiceService...) para que el enlace chunk -> nodo sea verificable, y conceptos de negocio que el
código NO nombra (nota de crédito, dunning, conciliación, contracargo, castigo) para que la
ontología cambie de forma medible al incluir la documentación.

| Archivo                               | Formato  | Tipo    | Degradación                          |
|---------------------------------------|----------|---------|--------------------------------------|
| manual_facturacion.md                 | Markdown | manual  | —                                    |
| spec_cobros.pdf                       | PDF      | spec    | —                                    |
| ADR-021-conciliacion.docx             | DOCX     | adr     | —                                    |
| politicas_negocio_escaneado.pdf       | PDF      | negocio | cabeceras/pies repetidos, nº página, |
|                                       |          |         | guiones de corte, espacios múltiples |
| reglas_negocio_legado.txt             | TXT      | negocio | mojibake cp1252, CRLF, NUL, ancho    |
|                                       |          |         | cero, ligaduras, títulos en MAYÚS.   |

El .docx incluye marcas de tiempo en sus metadatos: regenerarlo cambia sus bytes, no su texto.
"""

from __future__ import annotations

from pathlib import Path

DIR = Path(__file__).resolve().parent

MANUAL_MD = """# Manual de facturación

Guía de uso del módulo de facturación para el equipo de operaciones.

## Emisión de facturas

Cada `Invoice` se crea con su monto neto. El total con impuestos lo calcula
`BillingService.calculate_total`, que aplica la tasa de IVA configurada (19 % por defecto).

```bash
# Esto es un comentario dentro de un bloque de código, no un encabezado
make facturar
```

### Formato de montos

Los montos se muestran con `format_currency`, siempre con dos decimales.

## Notas de crédito

Una nota de crédito anula total o parcialmente una factura emitida. Nunca se edita una
factura pagada: se emite una nota de crédito y una factura nueva. Las notas de crédito
quedan asociadas a la factura original para la conciliación contable.

## Pagos

Al confirmar el pago, operaciones ejecuta `mark_as_paid`. Una factura pagada no puede
volver a estado pendiente.
"""

SPEC_PDF_LINEAS = [
    "Especificación de cobros",
    "",
    "1. Alcance",
    "Este documento define los requisitos del proceso de cobranza (dunning).",
    "",
    "2. Requisitos",
    "2.1 Cálculo de impuestos",
    "El total de cada factura se obtiene con calculate_total del BillingService.",
    "El servicio InvoiceService expone los totales brutos mediante Totals.",
    "",
    "2.2 Proceso de dunning",
    "Si una factura vence sin pago se inicia el dunning: recordatorio al día 3,",
    "segundo aviso al día 10 y suspensión del servicio al día 30.",
    "",
    "3. Criterios de aceptación",
    "Ninguna factura pagada entra al dunning. Todo aviso queda en AuditTrail.",
]

ADR_DOCX = {
    "titulo": "ADR-021: Conciliación bancaria diaria",
    "secciones": [
        ("Estado", ["Aceptado."]),
        (
            "Contexto",
            [
                "Los pagos se registran con mark_as_paid pero no se cruzan con la cartola "
                "bancaria, lo que deja diferencias sin detectar.",
            ],
        ),
        (
            "Decisión",
            [
                "Se ejecutará una conciliación bancaria diaria que cruza cada pago con su "
                "movimiento bancario. Cada diferencia se registra en AuditTrail.",
            ],
        ),
        ("Consecuencias", ["Las facturas conciliadas pasan a estado final."]),
    ],
    "lista": ["Cartola bancaria importada a las 06:00", "Tolerancia de 1 peso por redondeo"],
    "tabla": [("Campo", "Origen"), ("monto", "Invoice"), ("fecha", "cartola")],
}

POLITICAS_PAGINAS = [
    [
        "ACME S.A. - Documento confidencial",
        "POLÍTICAS COMERCIALES",
        "1. Contracargos",
        "Un contracargo  ocurre cuando el banco del cliente revierte un pago ya",
        "aplicado a una Factura. El área comercial debe responder el contra-",
        "cargo dentro de 7 días con la evidencia de entrega.",
        "Página 1 de 3",
    ],
    [
        "ACME S.A. - Documento confidencial",
        "2. Impuestos",
        "El Impuesto se calcula sobre el monto neto de cada línea de la factu-",
        "ración y   nunca sobre descuentos ya aplicados.",
        "Página 2 de 3",
    ],
    [
        "ACME S.A. - Documento confidencial",
        "3. Descuentos comerciales",
        "Los descuentos por volumen requieren aprobación de la gerencia comer-",
        "cial cuando superan el 15 % del monto neto.",
        "Página 3 de 3",
    ],
]

LEGADO_TXT = (
    "REGLAS DE NEGOCIO DE COBRANZA\r\n"
    "\r\n"
    "Documento heredado del sistema anterior.\x00 Exportado con codificación incorrecta.\r\n"
    "\r\n"
    "CASTIGO DE DEUDAS\r\n"
    "Una deuda impaga por más de 180 días se castiga (write-off): la Invoice se marca como\r\n"
    "incobrable y su monto deja de contar en los Totals de cartera.\u200b\r\n"
    "\r\n"
    "CLASIFICACIÓN DE CLIENTES\r\n"
    "Los clientes se clasi\ufb01can según su historial de pago: un cliente con dos castigos\r\n"
    "pierde el crédito directo. La de\ufb01nición del segmento la \ufb02exibiliza solo la gerencia.\r\n"
)


def _pdf_texto(cadena: str) -> bytes:
    crudo = cadena.encode("cp1252")
    return crudo.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def escribir_pdf(ruta: Path, paginas: list[list[str]]) -> None:
    """PDF mínimo válido (Helvetica, WinAnsiEncoding), una línea de texto por línea de entrada."""
    objetos: list[bytes] = []

    def agregar(cuerpo: bytes) -> int:
        objetos.append(cuerpo)
        return len(objetos)

    agregar(b"<< /Type /Catalog /Pages 2 0 R >>")
    agregar(b"")  # Pages: se completa al final
    fuente = agregar(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    hijos = []
    for lineas in paginas:
        flujo = b"BT /F1 11 Tf 14 TL 50 800 Td\n"
        for linea in lineas:
            flujo += b"(" + _pdf_texto(linea) + b") Tj T*\n"
        flujo += b"ET"
        contenido = agregar(
            b"<< /Length " + str(len(flujo)).encode() + b" >>\nstream\n" + flujo + b"\nendstream"
        )
        hijos.append(
            agregar(
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                b"/Resources << /Font << /F1 " + str(fuente).encode() + b" 0 R >> >> "
                b"/Contents " + str(contenido).encode() + b" 0 R >>"
            )
        )
    kids = b" ".join(f"{h} 0 R".encode() for h in hijos)
    objetos[1] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(hijos)).encode() + b" >>"
    )

    salida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, cuerpo in enumerate(objetos, start=1):
        offsets.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        salida += f"{off:010d} 00000 n \n".encode()
    salida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    ruta.write_bytes(bytes(salida))


def escribir_docx(ruta: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading(ADR_DOCX["titulo"], level=1)
    for titulo, parrafos in ADR_DOCX["secciones"]:
        doc.add_heading(titulo, level=2)
        for p in parrafos:
            doc.add_paragraph(p)
    doc.add_heading("Supuestos", level=2)
    for item in ADR_DOCX["lista"]:
        doc.add_paragraph(item, style="List Bullet")
    filas = ADR_DOCX["tabla"]
    tabla = doc.add_table(rows=len(filas), cols=2)
    for fila, valores in zip(tabla.rows, filas, strict=True):
        for celda, valor in zip(fila.cells, valores, strict=True):
            celda.text = valor
    doc.save(str(ruta))


def main() -> None:
    (DIR / "manual_facturacion.md").write_text(MANUAL_MD, encoding="utf-8", newline="\n")
    escribir_pdf(DIR / "spec_cobros.pdf", [SPEC_PDF_LINEAS])
    escribir_docx(DIR / "ADR-021-conciliacion.docx")
    escribir_pdf(DIR / "politicas_negocio_escaneado.pdf", POLITICAS_PAGINAS)
    # Mojibake: UTF-8 leído como cp1252 y vuelto a guardar en UTF-8 (doble codificación), en las
    # líneas sin ligaduras (el byte 0x81 de "\ufb01" no existe en cp1252): un archivo mixto, como los
    # que deja un copia-pega entre sistemas.
    lineas = []
    for linea in LEGADO_TXT.split("\r\n"):
        if any(c in linea for c in "\ufb01\ufb02"):
            lineas.append(linea)
        else:
            lineas.append(linea.encode("utf-8").decode("cp1252"))
    (DIR / "reglas_negocio_legado.txt").write_bytes("\r\n".join(lineas).encode("utf-8"))


if __name__ == "__main__":
    main()
