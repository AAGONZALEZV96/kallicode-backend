"""Ingesta, extracción y troceado jerárquico de documentación técnica (KC-CM-08-1).

Cierra CM-F22 activando el paso 5/5 del pipeline hacia vec.doc_chunks.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocumentChunk:
    """Representa un fragmento semántico extraído de un documento."""

    documento_id: str
    chunk_idx: int
    titulo: str
    seccion: str
    texto: str
    contenido_hash: str
    tipo_doc: str  # 'manual' | 'spec' | 'adr' | 'negocio' | 'general'
    metadata: dict[str, Any] = field(default_factory=dict)


def clasificar_tipo_documento(ruta_o_nombre: str, contenido: str) -> str:
    """Clasifica el tipo de documento según convención de ruta y contenido."""
    ruta_lower = str(ruta_o_nombre).lower()
    contenido_head = (contenido or "")[:500].lower()

    if "adr" in ruta_lower or "architecture decision" in contenido_head:
        return "adr"
    if "spec" in ruta_lower or "especificacion" in ruta_lower or "requirements" in contenido_head:
        return "spec"
    if "manual" in ruta_lower or "guia" in ruta_lower or "guide" in ruta_lower:
        return "manual"
    if "negocio" in ruta_lower or "business" in ruta_lower:
        return "negocio"
    return "general"


def extraer_texto_markdown(contenido_md: str) -> str:
    """Limpia caracteres de escape preservando encabezados y estructura semántica."""
    return contenido_md.strip()


def trocear_documento_markdown(
    documento_id: str,
    contenido: str,
    ruta: str = "",
    tamano_max_caracteres: int = 1500,
    solape_caracteres: int = 200,
) -> list[DocumentChunk]:
    """Trocea documentos respetando la jerarquía de títulos Markdown (#, ##, ###)."""
    if not contenido.strip():
        return []

    tipo = clasificar_tipo_documento(ruta or documento_id, contenido)
    lineas = contenido.splitlines()

    secciones: list[tuple[str, list[str]]] = []
    seccion_actual = "Introducción"
    buffer_lineas: list[str] = []

    patron_header = re.compile(r"^(#{1,4})\s+(.+)$")

    for linea in lineas:
        match = patron_header.match(linea.strip())
        if match:
            if buffer_lineas:
                secciones.append((seccion_actual, buffer_lineas))
                buffer_lineas = []
            seccion_actual = match.group(2).strip()
        else:
            buffer_lineas.append(linea)

    if buffer_lineas:
        secciones.append((seccion_actual, buffer_lineas))

    chunks: list[DocumentChunk] = []
    idx = 0

    for sec_nombre, sec_lineas in secciones:
        texto_seccion = "\n".join(sec_lineas).strip()
        if not texto_seccion:
            continue

        # Si la sección cabe completa en un bloque
        if len(texto_seccion) <= tamano_max_caracteres:
            texto_final = (
                f"## {sec_nombre}\n{texto_seccion}"
                if sec_nombre != "Introducción"
                else texto_seccion
            )
            c_hash = hashlib.sha256(texto_final.encode("utf-8")).hexdigest()
            chunks.append(
                DocumentChunk(
                    documento_id=documento_id,
                    chunk_idx=idx,
                    titulo=sec_nombre,
                    seccion=sec_nombre,
                    texto=texto_final,
                    contenido_hash=c_hash,
                    tipo_doc=tipo,
                    metadata={"ruta": ruta, "chars": len(texto_final)},
                )
            )
            idx += 1
        else:
            # Ventana deslizante con solape si la sección es extensa
            inicio = 0
            while inicio < len(texto_seccion):
                fin = inicio + tamano_max_caracteres
                corte = texto_seccion[inicio:fin]

                # Intentar cortar por párrafo o salto de línea
                if fin < len(texto_seccion):
                    ultimo_salto = corte.rfind("\n\n")
                    if ultimo_salto > tamano_max_caracteres // 2:
                        corte = corte[:ultimo_salto]
                        fin = inicio + ultimo_salto

                texto_final = f"## {sec_nombre} (cont.)\n{corte.strip()}"
                c_hash = hashlib.sha256(texto_final.encode("utf-8")).hexdigest()
                chunks.append(
                    DocumentChunk(
                        documento_id=documento_id,
                        chunk_idx=idx,
                        titulo=sec_nombre,
                        seccion=sec_nombre,
                        texto=texto_final,
                        contenido_hash=c_hash,
                        tipo_doc=tipo,
                        metadata={"ruta": ruta, "chars": len(texto_final)},
                    )
                )
                idx += 1
                inicio = max(inicio + 1, fin - solape_caracteres)

    return chunks
