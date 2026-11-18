"""Redacción de credenciales, rutas absolutas y código del cliente en los logs (KC-CM-12-6).

El paquete procesa el repositorio y la base productiva del cliente: nada de eso puede acabar en un
log. Este módulo es la única definición de qué se considera sensible y lo usan tres consumidores:

- ``logging_setup.JsonFormatter``, que redacta cada línea antes de escribirla;
- ``worker.error_estructurado``, que redacta el error que va a la DLQ;
- ``revision_logs``, el auditor que revisa un log ya escrito y falla si encuentra algo.

Las reglas son idempotentes (``redactar(redactar(x)) == redactar(x)``): el auditor detecta un
hallazgo cuando aplicar una regla cambiaría el texto.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

MAX_CARACTERES_VALOR = 1000
MAX_CARACTERES_ERROR = 500
OMITIDO_CODIGO = "<omitido: posible código, {n} caracteres>"
RUTA = "<ruta>"
OCULTO = "***"


@dataclass(frozen=True)
class Regla:
    categoria: str  # credencial | ruta_absoluta
    patron: re.Pattern[str]
    reemplazo: str


# El orden importa: primero los bloques y las URL completas, después los fragmentos sueltos.
REGLAS: tuple[Regla, ...] = (
    Regla(
        "credencial",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
            re.DOTALL,
        ),
        "<clave privada omitida>",
    ),
    # Userinfo de una URL (https://usuario:token@host, postgresql://app:clave@host).
    Regla(
        "credencial",
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)(?!\*\*\*@)[^/\s@\"'<>]+@"),
        r"\1***@",
    ),
    # Tokens con formato reconocible (GitHub, GitLab, OpenAI/DeepSeek, Slack, AWS, JWT).
    Regla(
        "credencial",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[\w-]{20,}"
            r"|sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}"
            r"|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,})"
        ),
        OCULTO,
    ),
    # Cabecera Authorization (sensible a mayúsculas: "basic authentication" no es una credencial).
    Regla(
        "credencial",
        re.compile(r"\b(Bearer|Basic|bearer)\s+(?!\*\*\*)(?=[\w.~+/=-]*\d)[\w.~+/=-]{12,}"),
        r"\1 ***",
    ),
    # clave=valor y clave: valor (DSN de libpq, cadenas de conexión de Azure, querystrings, SAS).
    Regla(
        "credencial",
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|client_secret|token|access_token|api[_-]?key"
            r"|accountkey|sharedaccesskey|sig|authorization)(\s*[=:]\s*)([\"']?)(?!\*\*\*)"
            r"[^\s\"',;&]+"
        ),
        r"\1\2\3***",
    ),
    # file:///ruta/absoluta
    Regla("ruta_absoluta", re.compile(r"(?i)\bfile://(?!<ruta>)[^\s\"'<>]+"), "file://" + RUTA),
    # Rutas Windows (C:\..., C:/...) y UNC (\\servidor\recurso).
    Regla(
        "ruta_absoluta",
        re.compile(r"(?<![\w<])(?:[A-Za-z]:[\\/]|\\\\[\w.$-]+\\)[^\s\"'<>|*?]*"),
        RUTA,
    ),
    # Rutas POSIX absolutas de dos o más segmentos (/home/x, /tmp/workdir/tenant/src/a.py).
    # La mirada atrás excluye URLs (host/ruta), fracciones (3/7) y lo ya redactado (<ruta>/x).
    Regla(
        "ruta_absoluta",
        re.compile(r"(?<![\w.:/~>-])(?:/[\w.@+-]+)+(/[\w.@+-]+)"),
        RUTA + r"\1",
    ),
    # Valor de entrada que pydantic y jsonschema copian en sus mensajes de error.
    Regla(
        "codigo",
        re.compile(r"input_value=(?!<omitido>).*?(?=, input_type=|$)", re.DOTALL),
        "input_value=<omitido>",
    ),
)

# Indicios de código fuente. Un texto con dos indicios distintos, o con uno y tres o más líneas,
# se trata como fragmento de código del cliente.
_INDICIOS_CODIGO: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bdef\s+\w+\s*\("),
    re.compile(r"\bfunction\b\s*\w*\s*\("),
    re.compile(r"\bclass\s+\w+\s*[:({]"),
    re.compile(r"(?m)^\s*(?:import\s+[\w.]+|from\s+[\w.]+\s+import\b)"),
    re.compile(r"\b(?:public|private|protected|static)\s+[\w<>\[\],]+\s+\w+\s*\("),
    re.compile(r"\breturn\b[^\n]*;"),
    re.compile(r"=>\s*[{(]"),
    re.compile(r"(?m)[{;][ \t]*\n.*[{;][ \t]*$"),  # dos líneas seguidas que acaban en ; o {
    re.compile(r"\b(?:const|let|var)\s+\w+\s*="),
    re.compile(r"(?is)\bselect\b.{1,200}?\bfrom\b\s+[\w.\"]+"),
    re.compile(r"#include\s*[<\"]"),
    re.compile(r"\bif\s*\(.*\)\s*\{"),
    re.compile(r"(?m)^(?: {4,}|\t)\S"),
)

_CLAVES_SECRETAS = re.compile(
    r"(?i)^(password|passwd|pwd|secret|client_secret|token|access_token|api_?key|llm_api_key"
    r"|authorization|credenciales?|db_url|dsn)$"
)


def parece_codigo(texto: str) -> bool:
    """True si el texto tiene pinta de ser un fragmento de código fuente."""
    if len(texto) < 20:
        return False
    indicios = sum(1 for p in _INDICIOS_CODIGO if p.search(texto))
    return indicios >= 2 or (indicios >= 1 and texto.count("\n") >= 2)


def redactar(texto: str, max_caracteres: int = MAX_CARACTERES_VALOR) -> str:
    """Texto apto para un log: sin credenciales, sin rutas absolutas y sin código del cliente."""
    for regla in REGLAS:
        texto = regla.patron.sub(regla.reemplazo, texto)
    if parece_codigo(texto):
        return OMITIDO_CODIGO.format(n=len(texto))
    if len(texto) > max_caracteres:
        return texto[:max_caracteres] + f"… <{len(texto) - max_caracteres} caracteres omitidos>"
    return texto


def redactar_valor(valor: Any, clave: str | None = None) -> Any:
    """Redacta recursivamente un valor de log (str, dict, list, tuple); el resto pasa intacto."""
    if clave is not None and _CLAVES_SECRETAS.match(clave) and valor not in (None, ""):
        return OCULTO
    if isinstance(valor, str):
        return redactar(valor)
    if isinstance(valor, Mapping):
        return {str(k): redactar_valor(v, str(k)) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [redactar_valor(v) for v in valor]
    return valor


def mensaje_de_error(exc: BaseException, max_caracteres: int = MAX_CARACTERES_ERROR) -> str:
    """Mensaje de una excepción listo para log, DLQ o columna de motivo."""
    return redactar(str(exc), max_caracteres)


def hallazgos(texto: str) -> list[str]:
    """Categorías sensibles presentes en un texto ya escrito (lo que redactar() cambiaría)."""
    encontrados: list[str] = []
    for regla in REGLAS:
        if regla.categoria not in encontrados and regla.patron.sub(regla.reemplazo, texto) != texto:
            encontrados.append(regla.categoria)
    if "codigo" not in encontrados and parece_codigo(texto):
        encontrados.append("codigo")
    return encontrados
