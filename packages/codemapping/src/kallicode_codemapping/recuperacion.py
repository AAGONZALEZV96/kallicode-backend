"""Recuperación en dos fases para Diseño y detección de tickets duplicados (KC-CM-09-5).

Contrato y fórmula de puntuación: `kallicode_core.cm_recuperacion` (cm-recuperacion-v1).

Contexto para la etapa Diseño:

    with GraphLoader(settings=settings) as loader:
        ctx = contexto_diseno(loader, TEIEmbeddingClient(settings.tei_endpoint_url),
                              "El IVA se duplica al reemitir una factura",
                              ficheros=["src/billing/invoice.py"], settings=settings)
        ctx.texto               # para la plantilla de Diseño; nunca supera el presupuesto
        ctx.tokens_estimados <= ctx.presupuesto_tokens

  Fase 1: búsqueda vectorial amplia (k_amplio) con cm.buscar_hibrido.
  Fase 2: cada candidato que es un nodo del grafo se puntúa por similitud y por cercanía (saltos)
  al área del ticket. Sin área explícita, el área son los mejores nodos de la fase 1.
  Recorte: elementos en orden de puntuación mientras quepan en el presupuesto de tokens.

Duplicados semánticos:

    with GraphLoader(settings=settings) as loader:
        r = buscar_duplicados(loader, tei, "Error al exportar facturas a PDF",
                              umbral=settings.dedup_umbral)
        [(d.numero, d.similitud) for d in r.duplicados]
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from kallicode_core import cm_busqueda, cm_recuperacion
from kallicode_core.cm_busqueda import BusquedaHibridaIn, CapaBusqueda, ResultadoBusqueda
from kallicode_core.cm_impacto import ConsultaGrafoIn
from kallicode_core.cm_recuperacion import (
    CAPAS_DISENO_DEFECTO,
    CAPAS_NODO,
    ContextoDisenoOut,
    DuplicadosOut,
    ElementoContexto,
)

from kallicode_codemapping import cm_queries
from kallicode_codemapping.budget import estimar_tokens_texto
from kallicode_codemapping.cm_queries import CursorFabrica, Embebedor
from kallicode_codemapping.config import Settings

# La consulta de kallicode_core.cm_recuperacion.SQL_TICKETS_SIMILARES con `%(nombre)s` en lugar de
# `:nombre` (sintaxis de psycopg), derivada al importar para que no puedan desviarse.
_PARAMETRO = re.compile(r"(?<![:\w]):(\w+)")
SQL_TICKETS_SIMILARES = _PARAMETRO.sub(r"%(\1)s", cm_recuperacion.SQL_TICKETS_SIMILARES)

_ESPACIOS = re.compile(r"\s+")


def _embeber(embebedor: Embebedor, texto: str, que: str) -> list[float]:
    if not texto.strip():
        raise ValueError(f"{que} no puede estar vacío")
    vectores = embebedor.embed_texts_sync([texto])
    if len(vectores) != 1:
        raise ValueError(f"el embebedor devolvio {len(vectores)} vectores para 1 texto")
    return vectores[0]


# ---------------------------------------------------------------------------
# Fase 2: re-ranking topológico
# ---------------------------------------------------------------------------
def reordenar(
    candidatos: Sequence[ResultadoBusqueda],
    distancias: dict[str, int],
    peso_topologico: float,
) -> list[ElementoContexto]:
    """Puntúa y ordena los candidatos: puntuación desc y, a igualdad, similitud, capa e id.

    Solo los nodos del grafo (CAPAS_NODO) tienen distancia: `referencia` es su id en cm.nodes. La
    documentación y los nuggets no están en el grafo y compiten solo por similitud.
    """
    elementos: list[ElementoContexto] = []
    for c in candidatos:
        d = distancias.get(c.referencia) if c.capa.value in CAPAS_NODO else None
        elementos.append(
            ElementoContexto(
                capa=c.capa.value,
                id=c.id,
                referencia=c.referencia,
                modulo=c.modulo,
                titulo=c.titulo,
                contenido=c.contenido,
                similitud=c.similitud,
                distancia=d,
                puntuacion=cm_recuperacion.puntuacion(c.similitud, d, peso_topologico),
            )
        )
    return sorted(elementos, key=lambda e: (-e.puntuacion, -e.similitud, e.capa, e.id))


# ---------------------------------------------------------------------------
# Recorte al presupuesto de tokens
# ---------------------------------------------------------------------------
def _linea(e: ElementoContexto, max_caracteres: int | None = None) -> str:
    contenido = _ESPACIOS.sub(" ", e.contenido).strip()
    limite = cm_recuperacion.CARACTERES_MAX_ELEMENTO
    if max_caracteres is not None:
        limite = min(limite, max_caracteres)
    if len(contenido) > limite:
        contenido = contenido[: max(limite - 1, 0)].rstrip() + "…"
    donde = f" ({e.modulo})" if e.modulo else ""
    return f"- [{e.capa}] {e.titulo}{donde}: {contenido}"


def ajustar_a_presupuesto(
    elementos: Sequence[ElementoContexto],
    presupuesto_tokens: int,
    estimar: Callable[[str], int] = estimar_tokens_texto,
) -> tuple[str, list[ElementoContexto], int]:
    """Devuelve (texto, elementos incluidos, omitidos). Nunca supera `presupuesto_tokens`.

    Va en orden de puntuación. Un elemento que no cabe se omite y se prueba con el siguiente (uno
    más corto puede caber). Si ninguno ha entrado todavía, el primero se recorta a lo que quepa
    para no devolver un contexto vacío habiendo candidatos.
    """
    lineas: list[str] = []
    incluidos: list[ElementoContexto] = []
    for e in elementos:
        linea = _linea(e)
        if estimar("\n".join([*lineas, linea])) <= presupuesto_tokens:
            lineas.append(linea)
            incluidos.append(e)
        elif not incluidos:
            recortada = _recortar_para_caber(e, presupuesto_tokens, estimar)
            if recortada:
                lineas.append(recortada)
                incluidos.append(e)
    return "\n".join(lineas), incluidos, len(elementos) - len(incluidos)


def _recortar_para_caber(
    e: ElementoContexto, presupuesto_tokens: int, estimar: Callable[[str], int]
) -> str | None:
    """La línea de `e` con el contenido más largo que quepa; None si ni vacío cabe."""
    bajo, alto = 0, len(_linea(e))
    mejor: str | None = None
    while bajo <= alto:  # búsqueda binaria sobre los caracteres de contenido
        medio = (bajo + alto) // 2
        linea = _linea(e, medio)
        if estimar(linea) <= presupuesto_tokens:
            mejor, bajo = linea, medio + 1
        else:
            alto = medio - 1
    return mejor


# ---------------------------------------------------------------------------
# Contexto para Diseño
# ---------------------------------------------------------------------------
def contexto_diseno(
    loader: CursorFabrica,
    embebedor: Embebedor,
    texto: str,
    *,
    nodos: Sequence[str] = (),
    ficheros: Sequence[str] = (),
    modulo: str | None = None,
    capas: Sequence[CapaBusqueda | str] | None = None,
    k_amplio: int = cm_recuperacion.K_AMPLIO_DEFECTO,
    presupuesto_tokens: int | None = None,
    saltos: int | None = None,
    peso_topologico: float | None = None,
    settings: Settings | None = None,
) -> ContextoDisenoOut:
    """Contexto combinado (vectorial + grafo) para la etapa Diseño, dentro del presupuesto.

    `texto` es el del ticket (título y descripción). `nodos` y `ficheros` son el área que se
    estima que toca; sin ellos, la fase 2 usa como área los mejores nodos de la fase 1. Los valores
    por defecto de presupuesto, saltos y peso salen de `settings` o, sin él, de las constantes del
    contrato.
    """
    presupuesto = presupuesto_tokens or (
        settings.diseno_presupuesto_tokens
        if settings
        else cm_recuperacion.PRESUPUESTO_TOKENS_DISENO_DEFECTO
    )
    n_saltos = saltos or (settings.diseno_saltos if settings else cm_recuperacion.SALTOS_DEFECTO)
    peso = (
        peso_topologico
        if peso_topologico is not None
        else (
            settings.diseno_peso_topologico if settings else cm_recuperacion.PESO_TOPOLOGICO_DEFECTO
        )
    )
    if presupuesto < cm_recuperacion.PRESUPUESTO_TOKENS_MIN:
        raise ValueError(f"presupuesto_tokens debe ser >= {cm_recuperacion.PRESUPUESTO_TOKENS_MIN}")
    if not 1 <= n_saltos <= cm_recuperacion.SALTOS_MAX:
        raise ValueError(f"saltos debe estar entre 1 y {cm_recuperacion.SALTOS_MAX}")

    # Fase 1: búsqueda vectorial amplia.
    vector = _embeber(embebedor, texto, "el texto del ticket")
    consulta = BusquedaHibridaIn(
        embedding=vector,
        capas=[CapaBusqueda(c) for c in (capas or CAPAS_DISENO_DEFECTO)],
        modulo=modulo,
        k=min(max(k_amplio, 1), cm_recuperacion.K_AMPLIO_MAX),
    )
    candidatos = cm_queries.buscar_hibrido(loader, consulta).resultados

    # Fase 2: distancias en el grafo desde el área del ticket.
    area_nodos = list(nodos)
    area_ficheros = list(ficheros)
    if not area_nodos and not area_ficheros:
        area_nodos = [c.referencia for c in candidatos if c.capa.value in CAPAS_NODO][
            : cm_recuperacion.SEMILLAS_DESDE_BUSQUEDA
        ]
    distancias: dict[str, int] = {}
    semillas: list[str] = []
    if area_nodos or area_ficheros:
        sub = cm_queries.subgrafo(
            loader, ConsultaGrafoIn(nodos=area_nodos, ficheros=area_ficheros, k=n_saltos)
        )
        distancias = {n.id: n.distancia for n in sub.nodos}
        semillas = list(sub.semillas)
    reordenado = bool(semillas)
    elementos = reordenar(candidatos, distancias, peso if reordenado else 0.0)

    texto_ctx, incluidos, omitidos = ajustar_a_presupuesto(elementos, presupuesto)
    return ContextoDisenoOut(
        texto=texto_ctx,
        elementos=incluidos,
        candidatos=len(candidatos),
        omitidos_por_presupuesto=omitidos,
        presupuesto_tokens=presupuesto,
        tokens_estimados=estimar_tokens_texto(texto_ctx),
        reordenado=reordenado,
        semillas=semillas,
        saltos=n_saltos,
        peso_topologico=peso if reordenado else 0.0,
    )


# ---------------------------------------------------------------------------
# Duplicados semánticos de tickets
# ---------------------------------------------------------------------------
def buscar_duplicados(
    loader: CursorFabrica,
    embebedor: Embebedor,
    texto: str,
    *,
    ticket_id: str | None = None,
    umbral: float = cm_recuperacion.UMBRAL_DUPLICADO_DEFECTO,
    k: int = cm_recuperacion.K_DUPLICADOS_DEFECTO,
) -> DuplicadosOut:
    """Tickets del tenant del loader con coseno > `umbral` respecto de `texto`.

    `ticket_id` excluye al propio ticket cuando ya tiene embedding. Solo mira el histórico del
    tenant: el filtro `tenant_id = core.current_tenant()` va en la consulta además de RLS.
    """
    if not 1 <= k <= cm_recuperacion.K_DUPLICADOS_MAX:
        raise ValueError(f"k debe estar entre 1 y {cm_recuperacion.K_DUPLICADOS_MAX}")
    vector = _embeber(embebedor, texto, "el texto del ticket")
    params: dict[str, Any] = {
        "embedding": cm_busqueda.vector_literal(vector),
        "excluir": ticket_id,
        "k": k,
    }
    with loader.fase("dedup") as cur:
        cur.execute("SELECT vec.configurar_busqueda('dedup')")
        cur.execute(SQL_TICKETS_SIMILARES, params)
        columnas = [c.name for c in cur.description]
        filas = [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]
    return cm_recuperacion.construir_duplicados(umbral, filas)
