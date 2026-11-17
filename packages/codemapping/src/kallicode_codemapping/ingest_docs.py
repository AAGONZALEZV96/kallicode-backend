"""Ingesta, extracción, troceado y clasificación de documentación técnica (KC-CM-08-1).

Cierra CM-F22 activando el paso 5/5 del pipeline: core.onboarding_documents -> vec.doc_chunks
-> cm.doc_chunk_links, y alimenta la inducción de ontología con extractos de la documentación.

Flujo por documento (`procesar_documento`):
    1. Leer el binario del blob (`LectorBlob`).
    2. Extraer texto según formato: Markdown / texto plano, PDF (pypdf) o DOCX (python-docx). La
       estructura de encabezados se conserva o se infiere y se expresa como Markdown (`#`).
    3. Normalizar texto degradado: mojibake, ligaduras, guiones de corte de línea, caracteres de
       control, cabeceras/pies de página repetidos. Si lo que queda no es utilizable, se falla con
       motivo en vez de embeber basura.
    4. Clasificar en manual / spec / adr / negocio / otro (respetando el tipo declarado al subir).
    5. Trocear por jerarquía de encabezados con solape dentro de las secciones largas.
    6. Embeber con TEI (BGE-M3, 1024 dims) solo los chunks cuyo hash cambió y persistirlos de forma
       idempotente (UNIQUE tenant_id + document_id + chunk_index).
    7. Enlazar los chunks a los nodos del grafo que describen (léxico + semántico).
    8. Dejar el documento en `procesado` o en `fallido` con motivo; nunca en `pendiente`.

Todo el texto extraído es contenido no confiable del cliente: nunca se interpola en prompts sin
delimitar (ver security.construir_payload_seguro_extracto_doc).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .budget import (
    ControlPresupuestoDocs,
    PresupuestoDocumentalExcedido,
    estimar_tokens_texto,
)
from .embeddings import (
    EmbeddingInvalidoError,
    TEIEmbeddingClient,
    embeber_en_lotes,
    id_doc_chunk,
    preparar_items_doc_chunks,
)

logger = logging.getLogger(__name__)

# Valores admitidos por od_tipo_chk / dc_tipo_chk.
TIPOS_DOCUMENTO = ("manual", "spec", "adr", "negocio", "otro")
TIPO_POR_DEFECTO = "otro"

SECCION_SIN_TITULO = "Introducción"
MIN_CARACTERES_UTILES = 40
MAX_MOTIVO = 500


# =====================================================================
# Modelos
# =====================================================================
@dataclass(frozen=True)
class DocumentChunk:
    """Representa un fragmento semántico extraído de un documento."""

    documento_id: str
    chunk_idx: int
    titulo: str
    seccion: str
    texto: str
    contenido_hash: str
    tipo_doc: str  # 'manual' | 'spec' | 'adr' | 'negocio' | 'otro'
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentoOnboarding:
    """Fila de core.onboarding_documents que la ingesta necesita."""

    id: str
    nombre_archivo: str
    content_type: str
    blob_ref: str
    tipo_documento: str = TIPO_POR_DEFECTO
    estado: str = "pendiente"
    hash_contenido: str | None = None  # SHA-256 del blob de la última ingesta exitosa


@dataclass(frozen=True)
class NodoGrafo:
    """Nodo del grafo candidato a ser descrito por un chunk (cm.nodes)."""

    id: str
    nombre: str
    kind: str


@dataclass(frozen=True)
class EnlaceChunkNodo:
    chunk_id: str
    nodo_ref: str
    metodo: str  # 'lexico' | 'semantico'
    score: float


@dataclass(frozen=True)
class ResultadoIngesta:
    documento_id: str
    estado: str  # 'procesado' | 'fallido' | 'omitido' (refresco: el hash no cambió)
    chunks: int = 0
    chunks_embebidos: int = 0
    enlaces: int = 0
    tipo_documento: str = TIPO_POR_DEFECTO
    motivo: str | None = None


class IngestaDocError(Exception):
    """Fallo esperado de la ingesta, con código estable para core.onboarding_documents.motivo_fallo.

    El código es lo que el portal traduce (es/en/pt); el detalle es técnico y no se muestra tal cual.
    """

    def __init__(self, codigo: str, detalle: str) -> None:
        super().__init__(f"{codigo}: {detalle}")
        self.codigo = codigo
        self.detalle = detalle


class LectorBlob(Protocol):
    def leer(self, blob_ref: str) -> bytes: ...


class RepositorioDocumentos(Protocol):
    """Persistencia que necesita la ingesta (GraphLoader la implementa sobre PostgreSQL)."""

    def obtener_documentos(self, estados: Sequence[str] = ...) -> list[DocumentoOnboarding]: ...

    def obtener_chunks_docs(
        self, documento_ids: Sequence[str] | None = ...
    ) -> list[tuple[str, str, str]]: ...

    def buscar_documento_por_hash(self, hash_contenido: str, excluir_id: str) -> str | None: ...

    def obtener_hashes_doc_chunks(self, documento_id: str) -> dict[int, str]: ...

    def guardar_doc_chunks(
        self,
        documento_id: str,
        items: list[dict[str, Any]],
        total_chunks: int,
        tipo_documento: str,
    ) -> int: ...

    def obtener_nodos_enlazables(self) -> list[NodoGrafo]: ...

    def reemplazar_enlaces_docs(
        self,
        documento_ids: list[str],
        enlaces_lexicos: list[EnlaceChunkNodo],
        umbral_semantico: float,
        top_k_semantico: int,
    ) -> int: ...

    def marcar_documento_procesado(
        self, documento_id: str, hash_contenido: str, chunks_count: int, tipo_documento: str
    ) -> None: ...

    def marcar_documento_fallido(self, documento_id: str, motivo: str) -> None: ...

    def obtener_documentos_a_purgar(self) -> list[str]: ...

    def purgar_documento(self, documento_id: str) -> int: ...


class LectorBlobHttp:
    """Lee blobs por HTTP GET de `{account_url}/{container}/{blob_ref}` (Azurite en local).

    TODO(produccion): igual que apps/api/servicios/blob.py, autenticar con managed identity /
    SAS de delegación. El contrato (`leer(blob_ref) -> bytes`) no cambia.
    """

    def __init__(self, account_url: str, container: str, timeout: float = 30.0) -> None:
        self.account_url = account_url.rstrip("/")
        self.container = container.strip("/")
        self.timeout = timeout

    def leer(self, blob_ref: str) -> bytes:
        url = f"{self.account_url}/{self.container}/{blob_ref.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers={"x-ms-version": "2021-08-06"})
                resp.raise_for_status()
                return resp.content
        except httpx.HTTPStatusError as exc:
            raise IngestaDocError(
                "BLOB_NO_DISPONIBLE", f"HTTP {exc.response.status_code} al leer {blob_ref}"
            ) from exc
        except httpx.HTTPError as exc:
            raise IngestaDocError(
                "BLOB_NO_DISPONIBLE", f"{type(exc).__name__} al leer {blob_ref}"
            ) from exc


# =====================================================================
# Normalización de texto degradado
# =====================================================================
# Controles (salvo \t y \n), guion blando, ancho cero, reordenamiento bidi (Trojan Source) y BOM.
_CARACTERES_BASURA = re.compile(
    "[\x00-\x08\x0b\x0e-\x1f\x7f\u00ad\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)
# Secuencias típicas de UTF-8 leído como cp1252/latin-1: "FacturaciÃ³n", "â€œ".
_MOJIBAKE = re.compile("[ÃÂ][^\x00-\x7f]|â€")
# "factu-\nración" -> "facturación" (solo si sigue minúscula: "Pre-\nTax" se conserva).
_GUION_CORTE = re.compile(r"([^\W\d_])-\n[ \t]*([a-záéíóúüñçàèìòùâêîôû])")
_ESPACIOS_INTERNOS = re.compile(r"(?<=\S)[ \t]{2,}")
_LINEAS_VACIAS = re.compile(r"\n{3,}")
_NUMERO_PAGINA = re.compile(
    r"^(?:p[aá]g(?:ina)?\.?|page)?\s*\d{1,4}(?:\s*(?:de|of|/)\s*\d{1,4})?$", re.IGNORECASE
)


def decodificar_bytes(datos: bytes) -> str:
    """UTF-8 (con o sin BOM); si no lo es, cp1252, que es lo que suelen exportar Word y Excel."""
    try:
        return datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        return datos.decode("cp1252", errors="replace")


_BYTES_CONTROL = frozenset(range(0x09)) | frozenset(range(0x0E, 0x20))


def es_binario(datos: bytes, muestra: int = 8192, umbral: float = 0.05) -> bool:
    """True si la muestra tiene más de `umbral` de bytes de control (no es texto, aunque decodifique)."""
    trozo = datos[:muestra]
    if not trozo:
        return False
    return sum(1 for b in trozo if b in _BYTES_CONTROL) / len(trozo) > umbral


def _bytes_originales(linea: str) -> bytes:
    """Bytes que produjeron `linea` al decodificarse como cp1252, con los 5 bytes que cp1252 no
    define (0x81, 0x8D, 0x8F, 0x90, 0x9D) recuperados como latin-1 -- p. ej. la comilla "”"."""
    salida = bytearray()
    for caracter in linea:
        try:
            salida += caracter.encode("cp1252")
        except UnicodeEncodeError:
            if ord(caracter) > 0xFF:
                raise
            salida.append(ord(caracter))
    return bytes(salida)


def reparar_mojibake(texto: str) -> str:
    """Revierte UTF-8 decodificado como cp1252/latin-1, línea a línea (tolera texto mixto).

    Una línea solo se reemplaza si sus bytes originales son UTF-8 válido: el texto legítimo con
    "Ã" o "Â" no cambia.
    """
    if not _MOJIBAKE.search(texto):
        return texto
    lineas = []
    for linea in texto.split("\n"):
        if _MOJIBAKE.search(linea):
            try:
                linea = _bytes_originales(linea).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        lineas.append(linea)
    return "\n".join(lineas)


def normalizar_texto(texto: str) -> str:
    """Limpia texto extraído conservando la estructura de líneas y encabezados."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n\n")
    texto = reparar_mojibake(texto)
    # NFKC: ligaduras (\ufb01 -> fi), anchos completos, espacios duros.
    texto = unicodedata.normalize("NFKC", texto)
    texto = _CARACTERES_BASURA.sub("", texto)
    texto = _GUION_CORTE.sub(r"\1\2", texto)
    texto = _ESPACIOS_INTERNOS.sub(" ", texto)
    texto = "\n".join(linea.rstrip() for linea in texto.split("\n"))
    texto = _LINEAS_VACIAS.sub("\n\n", texto)
    return texto.strip()


def evaluar_calidad(texto: str) -> dict[str, float]:
    """Métricas simples de legibilidad del texto ya normalizado."""
    visibles = [c for c in texto if not c.isspace()]
    total = len(visibles) or 1
    letras = sum(1 for c in visibles if c.isalpha())
    reemplazos = texto.count("\ufffd")
    return {
        "caracteres": float(len(texto)),
        "ratio_letras": round(letras / total, 3),
        "ratio_reemplazos": round(reemplazos / total, 3),
    }


def validar_texto_utilizable(texto: str, min_caracteres: int = MIN_CARACTERES_UTILES) -> None:
    """Corta la ingesta si el texto no merece embeberse (escaneado sin OCR, binario, basura)."""
    calidad = evaluar_calidad(texto)
    if calidad["caracteres"] < min_caracteres:
        raise IngestaDocError(
            "TEXTO_INSUFICIENTE",
            f"{int(calidad['caracteres'])} caracteres útiles (mínimo {min_caracteres}); "
            "¿PDF escaneado sin capa de texto?",
        )
    if calidad["ratio_letras"] < 0.5 or calidad["ratio_reemplazos"] > 0.05:
        raise IngestaDocError(
            "TEXTO_ILEGIBLE",
            f"ratio_letras={calidad['ratio_letras']} ratio_reemplazos={calidad['ratio_reemplazos']}",
        )


def quitar_cabeceras_repetidas(paginas: list[str]) -> list[str]:
    """Elimina números de página y cabeceras/pies que se repiten en la mayoría de las páginas.

    Una línea es cabecera/pie si aparece idéntica entre las 2 primeras o 2 últimas de al menos el
    60 % de las páginas (mínimo 3 páginas). Los números de página variables los quita
    _NUMERO_PAGINA, así que la comparación es exacta: dos líneas de contenido que solo difieren
    en una cifra no se confunden con una cabecera.
    """

    def clave(linea: str) -> str:
        return " ".join(linea.lower().split())

    repetidas: set[str] = set()
    if len(paginas) >= 3:
        conteo: Counter[str] = Counter()
        for pagina in paginas:
            lineas = [ln for ln in pagina.split("\n") if ln.strip()]
            extremos = {clave(ln) for ln in lineas[:2] + lineas[-2:] if len(ln.strip()) <= 80}
            conteo.update(extremos)
        repetidas = {c for c, n in conteo.items() if n >= max(2, int(len(paginas) * 0.6))}

    limpias = []
    for pagina in paginas:
        lineas = [
            ln
            for ln in pagina.split("\n")
            if not _NUMERO_PAGINA.match(ln.strip()) and clave(ln) not in repetidas
        ]
        limpias.append("\n".join(lineas))
    return limpias


_ENCABEZADO_NUMERADO = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-ZÁÉÍÓÚÑ][^\n]{1,78})$")


def marcar_encabezados_inferidos(texto: str) -> str:
    """Convierte en encabezados Markdown los títulos de formatos sin marcas (PDF, TXT).

    - Numerados: "2.1 Reglas de facturación" -> "## 2.1 Reglas de facturación" (nivel = profundidad).
    - En mayúsculas: "POLÍTICA DE DEVOLUCIONES" -> "# POLÍTICA DE DEVOLUCIONES".
    Las líneas que terminan en puntuación de oración se consideran texto, no títulos.
    """
    salida = []
    for linea in texto.split("\n"):
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or limpia[-1] in ".,;:":
            salida.append(linea)
            continue
        m = _ENCABEZADO_NUMERADO.match(limpia)
        if m and len(limpia.split()) <= 12:
            nivel = min(m.group(1).count(".") + 1, 4)
            salida.append(f"{'#' * nivel} {limpia}")
            continue
        letras = [c for c in limpia if c.isalpha()]
        if 3 <= len(letras) and len(limpia) <= 80 and limpia.isupper():
            salida.append(f"# {limpia}")
            continue
        salida.append(linea)
    return "\n".join(salida)


# =====================================================================
# Extracción por formato
# =====================================================================
def extraer_texto_markdown(contenido_md: str) -> str:
    """Normaliza Markdown preservando encabezados y estructura semántica."""
    return normalizar_texto(contenido_md)


def extraer_texto_pdf(datos: bytes) -> str:
    """Texto de un PDF con capa de texto; los títulos se infieren (pypdf no expone estilos)."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        lector = PdfReader(io.BytesIO(datos))
        if lector.is_encrypted and not lector.decrypt(""):
            raise IngestaDocError("DOCUMENTO_CIFRADO", "el PDF requiere contraseña")
        paginas = [pagina.extract_text() or "" for pagina in lector.pages]
    except IngestaDocError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise IngestaDocError("FORMATO_INVALIDO", f"PDF ilegible: {type(exc).__name__}") from exc

    texto = "\n\n".join(quitar_cabeceras_repetidas(paginas))
    return marcar_encabezados_inferidos(normalizar_texto(texto))


_ESTILO_ENCABEZADO = re.compile(r"(?:heading|t[ií]tulo|encabezado)\s*(\d)", re.IGNORECASE)


def _nivel_estilo_docx(nombre_estilo: str) -> int:
    if nombre_estilo.strip().lower() in ("title", "título", "titulo"):
        return 1
    m = _ESTILO_ENCABEZADO.search(nombre_estilo)
    return min(int(m.group(1)), 4) if m else 0


def extraer_texto_docx(datos: bytes) -> str:
    """Texto de un DOCX en orden de documento; estilos de título -> encabezados Markdown."""
    import zipfile

    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        documento = Document(io.BytesIO(datos))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise IngestaDocError("FORMATO_INVALIDO", f"DOCX ilegible: {type(exc).__name__}") from exc

    bloques: list[str] = []
    for elemento in documento.element.body.iterchildren():
        etiqueta = str(elemento.tag).rsplit("}", 1)[-1]
        if etiqueta == "p":
            parrafo = Paragraph(elemento, documento)  # type: ignore[arg-type]
            texto = parrafo.text.strip()
            if not texto:
                continue
            estilo = parrafo.style.name if parrafo.style is not None else ""
            nivel = _nivel_estilo_docx(estilo or "")
            if nivel:
                bloques.append(f"{'#' * nivel} {texto}")
            elif "list" in (estilo or "").lower():
                bloques.append(f"- {texto}")
            else:
                bloques.append(texto)
        elif etiqueta == "tbl":
            tabla = Table(elemento, documento)  # type: ignore[arg-type]
            filas = [" | ".join(c.text.strip() for c in fila.cells) for fila in tabla.rows]
            bloques.append("\n".join(filas))
    return normalizar_texto("\n\n".join(bloques))


_FORMATO_POR_CONTENT_TYPE = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/plain": "txt",
}
_FORMATO_POR_EXTENSION = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
}


def resolver_formato(content_type: str, nombre_archivo: str) -> str:
    """Formato por content-type declarado; si es genérico, por extensión."""
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in _FORMATO_POR_CONTENT_TYPE:
        return _FORMATO_POR_CONTENT_TYPE[ct]
    nombre = (nombre_archivo or "").lower()
    for ext, formato in _FORMATO_POR_EXTENSION.items():
        if nombre.endswith(ext):
            return formato
    raise IngestaDocError("FORMATO_NO_SOPORTADO", f"content_type={ct or '?'} archivo={nombre}")


def extraer_texto(datos: bytes, content_type: str, nombre_archivo: str) -> str:
    """Texto normalizado, con encabezados en Markdown, de un documento en cualquier formato admitido."""
    formato = resolver_formato(content_type, nombre_archivo)
    if formato == "pdf":
        return extraer_texto_pdf(datos)
    if formato == "docx":
        return extraer_texto_docx(datos)
    if es_binario(datos):
        raise IngestaDocError("TEXTO_ILEGIBLE", f"contenido binario declarado como {formato}")
    texto = normalizar_texto(decodificar_bytes(datos))
    return marcar_encabezados_inferidos(texto) if formato == "txt" else texto


# =====================================================================
# Clasificación
# =====================================================================
_TOKENS_RUTA = re.compile(r"[^a-z0-9áéíóúñ]+")
_REGLAS_RUTA: tuple[tuple[str, frozenset[str]], ...] = (
    ("adr", frozenset({"adr", "adrs", "decision", "decisiones", "decisions"})),
    (
        "spec",
        frozenset(
            {
                "spec",
                "specs",
                "especificacion",
                "especificación",
                "requirements",
                "requisitos",
                "rfc",
                "openapi",
                "api",
            }
        ),
    ),
    (
        "manual",
        frozenset(
            {
                "manual",
                "guia",
                "guía",
                "guide",
                "tutorial",
                "howto",
                "runbook",
                "instalacion",
                "instalación",
                "usuario",
                "onboarding",
            }
        ),
    ),
    (
        "negocio",
        frozenset(
            {
                "negocio",
                "business",
                "politica",
                "política",
                "politicas",
                "políticas",
                "policy",
                "reglas",
                "glosario",
                "glossary",
                "procesos",
                "comercial",
            }
        ),
    ),
)
_REGLAS_CONTENIDO: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "adr",
        re.compile(
            r"architecture decision|registro de decisi[oó]n|\badr[- _]?\d+|"
            r"^#+\s*(status|estado)\b[\s\S]*^#+\s*(decision|decisi[oó]n)\b",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
    (
        "spec",
        re.compile(
            r"\brequirements\b|\brequisitos\b|especificaci[oó]n|\bendpoint\b|"
            r"criterios de aceptaci[oó]n|acceptance criteria",
            re.IGNORECASE,
        ),
    ),
    (
        "manual",
        re.compile(
            r"manual de usuario|user guide|paso \d|step \d|c[oó]mo (instalar|usar|configurar)",
            re.IGNORECASE,
        ),
    ),
    (
        "negocio",
        re.compile(
            r"reglas? de negocio|business rules?|pol[ií]tica comercial|modelo de negocio|"
            r"glosario|glossary",
            re.IGNORECASE,
        ),
    ),
)


def clasificar_tipo_documento(ruta_o_nombre: str, contenido: str) -> str:
    """Clasifica en manual / spec / adr / negocio / otro: primero por ruta, luego por contenido."""
    tokens = {t for t in _TOKENS_RUTA.split(str(ruta_o_nombre or "").lower()) if t}
    for tipo, claves in _REGLAS_RUTA:
        if tokens & claves:
            return tipo
    cabeza = (contenido or "")[:3000]
    for tipo, patron in _REGLAS_CONTENIDO:
        if patron.search(cabeza):
            return tipo
    return TIPO_POR_DEFECTO


# =====================================================================
# Troceado jerárquico con solape
# =====================================================================
_PATRON_ENCABEZADO = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_VALLA_CODIGO = re.compile(r"^(```|~~~)")


def _secciones_por_encabezado(contenido: str) -> list[tuple[list[str], int, list[str]]]:
    """Divide en secciones (ruta_de_encabezados, nivel, líneas), ignorando '#' dentro de código."""
    secciones: list[tuple[list[str], int, list[str]]] = []
    pila: list[tuple[int, str]] = []
    buffer: list[str] = []
    en_codigo = False

    def cerrar() -> None:
        if any(ln.strip() for ln in buffer):
            ruta = [t for _, t in pila] or [SECCION_SIN_TITULO]
            nivel = pila[-1][0] if pila else 0
            secciones.append((ruta, nivel, list(buffer)))
        buffer.clear()

    for linea in contenido.splitlines():
        if _VALLA_CODIGO.match(linea.strip()):
            en_codigo = not en_codigo
            buffer.append(linea)
            continue
        m = None if en_codigo else _PATRON_ENCABEZADO.match(linea.strip())
        if m:
            cerrar()
            nivel = len(m.group(1))
            while pila and pila[-1][0] >= nivel:
                pila.pop()
            pila.append((nivel, m.group(2).strip()))
        else:
            buffer.append(linea)
    cerrar()
    return secciones


def _ventanas(texto: str, tamano_max: int, solape: int) -> list[str]:
    """Ventanas de hasta `tamano_max` caracteres con `solape`, cortando en párrafo/oración/espacio."""
    if len(texto) <= tamano_max:
        return [texto]
    partes: list[str] = []
    inicio = 0
    while inicio < len(texto):
        fin = min(inicio + tamano_max, len(texto))
        if fin < len(texto):
            corte = texto[inicio:fin]
            for separador in ("\n\n", "\n", ". ", " "):
                pos = corte.rfind(separador)
                if pos > tamano_max // 2:
                    fin = inicio + pos + len(separador)
                    break
        parte = texto[inicio:fin].strip()
        if parte:
            partes.append(parte)
        if fin >= len(texto):
            break
        inicio = max(inicio + 1, fin - solape)
    return partes


def trocear_documento_markdown(
    documento_id: str,
    contenido: str,
    ruta: str = "",
    tamano_max_caracteres: int = 1500,
    solape_caracteres: int = 200,
    tipo_doc: str | None = None,
) -> list[DocumentChunk]:
    """Trocea respetando la jerarquía de encabezados Markdown (# .. ######).

    Cada sección (texto bajo un encabezado hasta el siguiente) es la unidad semántica; si excede
    `tamano_max_caracteres` se divide en ventanas con `solape_caracteres` de solape. Cada chunk
    lleva como prefijo la ruta de encabezados ("Facturación > Notas de crédito") para que el
    embedding conserve el contexto de la sección. chunk_idx es determinista para un mismo texto.
    """
    if solape_caracteres < 0 or solape_caracteres >= tamano_max_caracteres:
        raise ValueError("solape_caracteres debe estar en [0, tamano_max_caracteres)")
    if not contenido.strip():
        return []

    tipo = tipo_doc or clasificar_tipo_documento(ruta or documento_id, contenido)
    chunks: list[DocumentChunk] = []

    for ruta_encabezados, nivel, lineas in _secciones_por_encabezado(contenido):
        cuerpo = "\n".join(lineas).strip()
        if not cuerpo:
            continue
        titulo = ruta_encabezados[-1]
        con_titulo = titulo != SECCION_SIN_TITULO
        prefijo = " > ".join(ruta_encabezados) if con_titulo else ""
        margen = len(prefijo) + 1 if prefijo else 0
        ventanas = _ventanas(
            cuerpo, max(tamano_max_caracteres - margen, solape_caracteres + 1), solape_caracteres
        )
        for parte_idx, ventana in enumerate(ventanas):
            texto_final = f"{prefijo}\n{ventana}" if prefijo else ventana
            chunks.append(
                DocumentChunk(
                    documento_id=documento_id,
                    chunk_idx=len(chunks),
                    titulo=titulo,
                    seccion=titulo,
                    texto=texto_final,
                    contenido_hash=hashlib.sha256(texto_final.encode("utf-8")).hexdigest(),
                    tipo_doc=tipo,
                    metadata={
                        "ruta": ruta,
                        "chars": len(texto_final),
                        "encabezados": ruta_encabezados if con_titulo else [],
                        "nivel": nivel,
                        "parte": parte_idx,
                        "partes": len(ventanas),
                        "tipo_documento": tipo,
                    },
                )
            )
    return chunks


# =====================================================================
# Enlace chunk -> nodo (léxico; el semántico lo resuelve SQL sobre pgvector)
# =====================================================================
_IDENTIFICADOR = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NOMBRES_GENERICOS = frozenset(
    {
        "main",
        "init",
        "test",
        "tests",
        "data",
        "index",
        "utils",
        "util",
        "helper",
        "helpers",
        "get",
        "set",
        "run",
        "app",
        "config",
        "settings",
        "base",
        "default",
        "value",
        "item",
        "items",
        "list",
        "name",
        "type",
        "id",
        "self",
        "this",
        "new",
        "create",
        "update",
        "delete",
        "handler",
        "service",
        "model",
        "models",
        "table",
        "user",
        "users",
    }
)
MAX_NODOS_POR_NOMBRE = 5
SCORE_IDENTIFICADOR_CODIGO = 1.0
SCORE_IDENTIFICADOR = 0.9
SCORE_FRASE = 0.7


def _palabras_de_nombre(nombre: str) -> list[str]:
    return [p for p in _CAMEL.sub(" ", nombre).replace("_", " ").lower().split() if p]


def enlazar_chunks_lexico(
    chunks: Sequence[tuple[str, str]],
    nodos: Sequence[NodoGrafo],
    max_por_chunk: int = 20,
) -> list[EnlaceChunkNodo]:
    """Enlaza cada chunk (chunk_id, texto) con los nodos cuyo nombre menciona.

    - Identificador exacto (`InvoiceService`, crear_factura, invoices): 0.9; entre backticks, 1.0.
    - Frase equivalente de un nombre compuesto ("invoice service" para InvoiceService): 0.7.
    Se ignoran nombres cortos o genéricos y los que comparten demasiados nodos (ambiguos).
    """
    por_identificador: dict[str, list[str]] = defaultdict(list)
    por_frase: dict[str, list[str]] = defaultdict(list)
    for nodo in nodos:
        nombre = (nodo.nombre or "").strip()
        if len(nombre) < 4 or nombre.lower() in _NOMBRES_GENERICOS:
            continue
        por_identificador[nombre.lower()].append(nodo.id)
        palabras = _palabras_de_nombre(nombre)
        if len(palabras) >= 2:
            por_frase[" ".join(palabras)].append(nodo.id)

    enlaces: list[EnlaceChunkNodo] = []
    for chunk_id, texto in chunks:
        mejores: dict[str, float] = {}
        en_codigo = {
            i.lower()
            for bloque in re.findall(r"`([^`]+)`", texto)
            for i in _IDENTIFICADOR.findall(bloque)
        }
        for ident in {i.lower() for i in _IDENTIFICADOR.findall(texto)}:
            destinos = por_identificador.get(ident)
            if not destinos or len(destinos) > MAX_NODOS_POR_NOMBRE:
                continue
            score = SCORE_IDENTIFICADOR_CODIGO if ident in en_codigo else SCORE_IDENTIFICADOR
            for nodo_id in destinos:
                mejores[nodo_id] = max(mejores.get(nodo_id, 0.0), score)

        plano = f" {' '.join(re.sub(r'[^0-9a-záéíóúñ]+', ' ', texto.lower()).split())} "
        for frase, destinos in por_frase.items():
            if len(destinos) > MAX_NODOS_POR_NOMBRE or f" {frase} " not in plano:
                continue
            for nodo_id in destinos:
                mejores[nodo_id] = max(mejores.get(nodo_id, 0.0), SCORE_FRASE)

        ordenados = sorted(mejores.items(), key=lambda kv: (-kv[1], kv[0]))[:max_por_chunk]
        enlaces.extend(EnlaceChunkNodo(chunk_id, n, "lexico", s) for n, s in ordenados)
    return enlaces


# =====================================================================
# Orquestación
# =====================================================================
_URL_CON_QUERY = re.compile(r"(https?://[^\s?#]+)[?#]\S*")


def describir_fallo(exc: BaseException) -> str:
    """Motivo persistible: código estable + detalle técnico, sin tokens SAS ni querystrings."""
    if isinstance(exc, IngestaDocError):
        motivo = f"{exc.codigo}: {exc.detalle}"
    elif isinstance(exc, EmbeddingInvalidoError):
        motivo = f"EMBEDDING_INVALIDO: {exc}"
    elif isinstance(exc, httpx.HTTPError):
        motivo = f"TEI_NO_DISPONIBLE: {type(exc).__name__}: {exc}"
    else:
        motivo = f"ERROR_INTERNO: {type(exc).__name__}: {exc}"
    motivo = _URL_CON_QUERY.sub(r"\1?[redactado]", motivo).strip()
    return (motivo or "ERROR_INTERNO: sin detalle")[:MAX_MOTIVO]


@dataclass(frozen=True)
class DocumentoPreparado:
    """Documento leído, validado y troceado, listo para embeber (sin haber llamado a TEI)."""

    documento: DocumentoOnboarding
    hash_contenido: str
    tipo: str
    chunks: list[DocumentChunk]
    nuevos: list[DocumentChunk]  # chunks cuyo hash cambió: lo único que se embebe

    @property
    def tokens_estimados(self) -> int:
        return sum(estimar_tokens_texto(c.texto) for c in self.nuevos)


@dataclass
class ResumenDocumental:
    """Resultado de una corrida de la fase documental (va a las stats del snapshot)."""

    resultados: list[ResultadoIngesta] = field(default_factory=list)
    purgados: int = 0
    pendientes_restantes: int = 0

    def _contar(self, estado: str) -> int:
        return sum(1 for r in self.resultados if r.estado == estado)

    @property
    def procesados(self) -> int:
        return self._contar("procesado")

    @property
    def omitidos(self) -> int:
        return self._contar("omitido")

    @property
    def fallidos(self) -> int:
        return self._contar("fallido")

    def a_stats(self) -> dict[str, Any]:
        return {
            "procesados": self.procesados,
            "omitidos": self.omitidos,
            "fallidos": self.fallidos,
            "purgados": self.purgados,
            "pendientes_restantes": self.pendientes_restantes,
            "chunks_embebidos": sum(r.chunks_embebidos for r in self.resultados),
        }


def preparar_documento(
    documento: DocumentoOnboarding,
    *,
    repositorio: RepositorioDocumentos,
    lector: LectorBlob,
    tamano_max_caracteres: int = 1500,
    solape_caracteres: int = 200,
    comparar_hash: bool = False,
) -> DocumentoPreparado | None:
    """Lee, valida y trocea un documento sin tocar TEI ni persistir chunks.

    Con `comparar_hash=True` (refresco), un documento ya procesado cuyo SHA-256 no cambió devuelve
    None: no se extrae, no se trocea y no se embebe nada. Cualquier otro caso devuelve el
    documento preparado o lanza la excepción que lo deja fallido.
    """
    datos = lector.leer(documento.blob_ref)
    if not datos:
        raise IngestaDocError("DOCUMENTO_VACIO", "el blob no tiene contenido")
    hash_contenido = hashlib.sha256(datos).hexdigest()
    if (
        comparar_hash
        and documento.estado == "procesado"
        and documento.hash_contenido == hash_contenido
    ):
        return None
    duplicado = repositorio.buscar_documento_por_hash(hash_contenido, documento.id)
    if duplicado:
        raise IngestaDocError("DOCUMENTO_DUPLICADO", f"mismo contenido que {duplicado}")

    texto = extraer_texto(datos, documento.content_type, documento.nombre_archivo)
    validar_texto_utilizable(texto)

    declarado = (documento.tipo_documento or "").lower()
    tipo = (
        declarado
        if declarado in TIPOS_DOCUMENTO and declarado != TIPO_POR_DEFECTO
        else clasificar_tipo_documento(documento.nombre_archivo, texto)
    )
    chunks = trocear_documento_markdown(
        documento.id,
        texto,
        ruta=documento.nombre_archivo,
        tamano_max_caracteres=tamano_max_caracteres,
        solape_caracteres=solape_caracteres,
        tipo_doc=tipo,
    )
    if not chunks:
        raise IngestaDocError("TEXTO_INSUFICIENTE", "el documento no produjo fragmentos")

    existentes = repositorio.obtener_hashes_doc_chunks(documento.id)
    nuevos = [c for c in chunks if existentes.get(c.chunk_idx) != c.contenido_hash]
    return DocumentoPreparado(documento, hash_contenido, tipo, chunks, nuevos)


def ingerir_preparado(
    preparado: DocumentoPreparado,
    *,
    repositorio: RepositorioDocumentos,
    tei: TEIEmbeddingClient,
    tenant_id: str,
    umbral_semantico: float = 0.6,
    top_k_semantico: int = 5,
    tamano_lote_tei: int = 32,
) -> ResultadoIngesta:
    """Embebe los chunks nuevos, los persiste, recalcula los enlaces y marca el documento."""
    documento, chunks, nuevos = preparado.documento, preparado.chunks, preparado.nuevos
    vectores = embeber_en_lotes(tei, [c.texto for c in nuevos], tamano_lote=tamano_lote_tei)
    items = preparar_items_doc_chunks(nuevos, vectores, tenant_id=tenant_id)
    repositorio.guardar_doc_chunks(documento.id, items, len(chunks), preparado.tipo)

    lexicos = enlazar_chunks_lexico(
        [(id_doc_chunk(tenant_id, c.documento_id, c.chunk_idx), c.texto) for c in chunks],
        repositorio.obtener_nodos_enlazables(),
    )
    enlaces = repositorio.reemplazar_enlaces_docs(
        [documento.id], lexicos, umbral_semantico, top_k_semantico
    )
    repositorio.marcar_documento_procesado(
        documento.id, preparado.hash_contenido, len(chunks), preparado.tipo
    )
    logger.info(
        "documento ingerido",
        extra={
            "tenant_id": tenant_id,
            "documento_id": documento.id,
            "chunks": len(chunks),
            "chunks_embebidos": len(nuevos),
            "enlaces": enlaces,
            "tipo_documento": preparado.tipo,
        },
    )
    return ResultadoIngesta(
        documento.id,
        "procesado",
        chunks=len(chunks),
        chunks_embebidos=len(nuevos),
        enlaces=enlaces,
        tipo_documento=preparado.tipo,
    )


def _registrar_fallo(
    documento: DocumentoOnboarding,
    exc: BaseException,
    repositorio: RepositorioDocumentos,
    tenant_id: str,
) -> ResultadoIngesta:
    motivo = describir_fallo(exc)
    logger.warning(
        "ingesta de documento fallida",
        extra={"tenant_id": tenant_id, "documento_id": documento.id, "motivo": motivo},
    )
    repositorio.marcar_documento_fallido(documento.id, motivo)
    return ResultadoIngesta(documento.id, "fallido", motivo=motivo)


def procesar_documento(
    documento: DocumentoOnboarding,
    *,
    repositorio: RepositorioDocumentos,
    lector: LectorBlob,
    tei: TEIEmbeddingClient,
    tenant_id: str,
    tamano_max_caracteres: int = 1500,
    solape_caracteres: int = 200,
    umbral_semantico: float = 0.6,
    top_k_semantico: int = 5,
    tamano_lote_tei: int = 32,
) -> ResultadoIngesta:
    """Ingiere un documento de punta a punta. Siempre termina en 'procesado' o 'fallido' con motivo.

    Reprocesar es idempotente: los chunks se identifican por (documento, chunk_idx), solo se
    re-embeben los que cambiaron de hash y los índices sobrantes de una versión anterior se borran.
    """
    try:
        preparado = preparar_documento(
            documento,
            repositorio=repositorio,
            lector=lector,
            tamano_max_caracteres=tamano_max_caracteres,
            solape_caracteres=solape_caracteres,
        )
        assert preparado is not None  # sin comparar_hash siempre hay documento preparado
        return ingerir_preparado(
            preparado,
            repositorio=repositorio,
            tei=tei,
            tenant_id=tenant_id,
            umbral_semantico=umbral_semantico,
            top_k_semantico=top_k_semantico,
            tamano_lote_tei=tamano_lote_tei,
        )
    except Exception as exc:
        return _registrar_fallo(documento, exc, repositorio, tenant_id)


def _ejecutar_lote(
    documentos: Sequence[DocumentoOnboarding],
    *,
    repositorio: RepositorioDocumentos,
    lector: LectorBlob,
    tei: TEIEmbeddingClient,
    tenant_id: str,
    control: ControlPresupuestoDocs,
    comparar_hash: bool,
    tamano_max_caracteres: int = 1500,
    solape_caracteres: int = 200,
    umbral_semantico: float = 0.6,
    top_k_semantico: int = 5,
    tamano_lote_tei: int = 32,
) -> tuple[list[ResultadoIngesta], int]:
    """Estimación en seco + ingesta con presupuesto. Devuelve (resultados, pendientes_restantes).

    1) Prepara todos los documentos (lectura, hash, extracción, troceado: sin TEI) y fija la
       estimación en seco de tokens en `control`.
    2) Ingiere en orden; antes de embeber cada documento pide autorización al presupuesto. Al
       superarlo se detiene solo esta fase: los documentos que faltan quedan como estaban
       (pendiente) y los ya ingeridos se conservan.
    """
    resultados: list[ResultadoIngesta] = []
    preparados: list[DocumentoPreparado] = []
    for documento in documentos:
        try:
            preparado = preparar_documento(
                documento,
                repositorio=repositorio,
                lector=lector,
                tamano_max_caracteres=tamano_max_caracteres,
                solape_caracteres=solape_caracteres,
                comparar_hash=comparar_hash,
            )
        except Exception as exc:
            resultados.append(_registrar_fallo(documento, exc, repositorio, tenant_id))
            continue
        if preparado is None:
            resultados.append(
                ResultadoIngesta(documento.id, "omitido", tipo_documento=documento.tipo_documento)
            )
            continue
        preparados.append(preparado)

    control.registrar_estimacion(sum(p.tokens_estimados for p in preparados), len(preparados))
    logger.info(
        "estimacion en seco documental",
        extra={
            "tenant_id": tenant_id,
            "documentos": len(preparados),
            "tokens_estimados": control.estimado_tokens,
            "presupuesto_tokens": control.tokens_max,
        },
    )

    restantes = 0
    for i, preparado in enumerate(preparados):
        try:
            control.autorizar(preparado.tokens_estimados)
        except PresupuestoDocumentalExcedido as exc:
            restantes = len(preparados) - i
            control.documentos_sin_procesar = restantes
            logger.warning(
                "presupuesto_documental_excedido",
                extra={
                    "tenant_id": tenant_id,
                    "codigo": exc.codigo,
                    "detalle": exc.detalle,
                    "documentos_sin_procesar": restantes,
                },
            )
            break
        inicio = time.monotonic()
        try:
            resultado = ingerir_preparado(
                preparado,
                repositorio=repositorio,
                tei=tei,
                tenant_id=tenant_id,
                umbral_semantico=umbral_semantico,
                top_k_semantico=top_k_semantico,
                tamano_lote_tei=tamano_lote_tei,
            )
        except Exception as exc:
            resultados.append(_registrar_fallo(preparado.documento, exc, repositorio, tenant_id))
            continue
        control.registrar_real(
            sum(estimar_tokens_texto(c.texto) for c in preparado.nuevos),
            preparado.tokens_estimados,
            int((time.monotonic() - inicio) * 1000),
        )
        resultados.append(resultado)
    return resultados, restantes


def procesar_documentos_pendientes(
    repositorio: RepositorioDocumentos,
    lector: LectorBlob,
    tei: TEIEmbeddingClient,
    tenant_id: str,
    *,
    control: ControlPresupuestoDocs | None = None,
    **parametros: Any,
) -> list[ResultadoIngesta]:
    """Ingiere todos los documentos 'pendiente' del tenant; cada uno termina procesado o fallido.

    Con `control` (KC-CM-08-6) la fase hace estimación en seco y respeta el presupuesto: si se
    supera, deja el resto en 'pendiente' sin fallar nada más. Sin `control`, ingiere todo.
    """
    pendientes = repositorio.obtener_documentos(("pendiente",))
    if control is None:
        return [
            procesar_documento(
                documento,
                repositorio=repositorio,
                lector=lector,
                tei=tei,
                tenant_id=tenant_id,
                **parametros,
            )
            for documento in pendientes
        ]
    resultados, _ = _ejecutar_lote(
        pendientes,
        repositorio=repositorio,
        lector=lector,
        tei=tei,
        tenant_id=tenant_id,
        control=control,
        comparar_hash=False,
        **parametros,
    )
    return resultados


def refrescar_documentos(
    repositorio: RepositorioDocumentos,
    lector: LectorBlob,
    tei: TEIEmbeddingClient,
    tenant_id: str,
    *,
    control: ControlPresupuestoDocs,
    **parametros: Any,
) -> ResumenDocumental:
    """Refresco de la capa documental (KC-CM-08-5): purga, reutilización por hash e ingesta.

    1. Purga los documentos dados de baja: sus chunks y, en cascada, sus enlaces al grafo, en una
       sola transacción por documento.
    2. Para cada documento activo (pendiente o procesado) lee el blob y calcula su SHA-256. Si es
       un documento procesado con el mismo hash, lo omite sin llamar a TEI. Si cambió, o es nuevo,
       lo ingiere: solo se re-embeben los chunks cuyo hash cambió.
    3. Respeta el presupuesto documental (KC-CM-08-6).
    """
    resumen = ResumenDocumental()
    for documento_id in repositorio.obtener_documentos_a_purgar():
        repositorio.purgar_documento(documento_id)
        resumen.purgados += 1
    candidatos = repositorio.obtener_documentos(("pendiente", "procesado"))
    resumen.resultados, resumen.pendientes_restantes = _ejecutar_lote(
        candidatos,
        repositorio=repositorio,
        lector=lector,
        tei=tei,
        tenant_id=tenant_id,
        control=control,
        comparar_hash=True,
        **parametros,
    )
    return resumen


def reenlazar_documentos(
    repositorio: RepositorioDocumentos,
    umbral_semantico: float = 0.6,
    top_k_semantico: int = 5,
) -> int:
    """Recalcula los enlaces de TODOS los documentos procesados contra el grafo actual.

    build_graph lo llama tras persistir la fase 7: los documentos ingeridos antes de que existieran
    las definiciones y conceptos de este snapshot quedan enlazados también a ellos.
    """
    chunks = repositorio.obtener_chunks_docs(None)
    if not chunks:
        return 0
    documento_ids = sorted({doc for _, doc, _ in chunks})
    lexicos = enlazar_chunks_lexico(
        [(chunk_id, texto) for chunk_id, _, texto in chunks],
        repositorio.obtener_nodos_enlazables(),
    )
    return repositorio.reemplazar_enlaces_docs(
        documento_ids, lexicos, umbral_semantico, top_k_semantico
    )
