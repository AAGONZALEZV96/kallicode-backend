"""Capa de incidentes: registro de la memoria de fallos del cliente (KC-CM-09-2).

Contrato, SQL y cotas en `kallicode_core.cm_incidentes` (cm-incidentes-v1); aquí vive el cliente
psycopg síncrono que registra un incidente (fila, enlaces a nodos y embedding) y el que lo arma a
partir del expediente de un ticket. La consulta para {{incidents}} está en `cm_queries`.

Punto de entrada para Backend (KC-BE-09-4) al cerrar un ticket con incidente:

    with GraphLoader(dsn=..., tenant_id=tenant) as loader:
        r = registrar_desde_expediente(loader, "tk_01H...", embebedor=TEIEmbeddingClient(url),
                                       correccion="Se valida el IVA antes de emitir la factura.")
        r.id, r.creado, r.nodos_enlazados

Registrar de nuevo el mismo ticket actualiza su incidente (no duplica) y re-embebe solo si el
texto cambió. El registro y los enlaces van en una transacción; el embedding, en otra posterior
(la llamada al servicio de embeddings no se hace con la transacción abierta).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from kallicode_core import cm_incidentes, ids
from kallicode_core.cm_incidentes import (
    CAUSA_MAX_CHARS,
    CORRECCION_MAX_CHARS,
    RESUMEN_MAX_CHARS,
    RegistroIncidenteIn,
    RegistroIncidenteOut,
    normalizar,
)

from kallicode_codemapping.cm_queries import (
    CONSULTAS_INCIDENTES,
    CursorFabrica,
    Embebedor,
)

MODELO_EMBEDDING = "bge-m3"
MAX_ENLACES = 2_000


class _Cursor(Protocol):
    description: Any

    def execute(self, query: str, params: Any = ...) -> Any: ...
    def fetchall(self) -> list[tuple[Any, ...]]: ...
    def fetchone(self) -> tuple[Any, ...] | None: ...


def _filas(cur: _Cursor, consulta: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    cur.execute(CONSULTAS_INCIDENTES[consulta], dict(params))
    columnas = [c.name for c in cur.description]
    return [dict(zip(columnas, fila, strict=True)) for fila in cur.fetchall()]


def recortar(texto: str | None, maximo: int) -> str | None:
    """Una línea de como mucho `maximo` caracteres, cortada en un espacio y con '…' si se cortó."""
    limpio = normalizar(texto)
    if not limpio:
        return None
    if len(limpio) <= maximo:
        return limpio
    corte = limpio[: maximo - 1]
    if " " in corte:
        corte = corte.rsplit(" ", 1)[0]
    return corte.rstrip() + "…"


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def registrar_incidente(
    loader: CursorFabrica,
    registro: RegistroIncidenteIn,
    *,
    embebedor: Embebedor | None = None,
    modelo: str = MODELO_EMBEDDING,
) -> RegistroIncidenteOut:
    """Alta (o actualización por ticket) del incidente, sus enlaces y su embedding."""
    with loader.fase("incidentes") as cur:
        nodos = [
            str(f["id"])
            for f in _filas(
                cur,
                "nodos_afectados",
                {"nodos": registro.nodos, "ficheros": registro.ficheros},
            )
        ][:MAX_ENLACES]
        fila = _filas(
            cur,
            "upsert_incidente",
            {
                "id": ids.nuevo("inc"),
                "ticket_id": registro.ticket_id,
                "origen": registro.origen,
                "resumen": registro.resumen,
                "causa": registro.causa,
                "correccion": registro.correccion,
                "evidencia_ref": registro.evidencia_ref,
                "nodos_afectados": json.dumps(nodos),
            },
        )[0]
        incidente_id, creado = str(fila["id"]), bool(fila["creado"])
        cur.execute(CONSULTAS_INCIDENTES["borrar_enlaces"], {"id": incidente_id})
        cur.execute(CONSULTAS_INCIDENTES["insertar_enlaces"], {"id": incidente_id, "nodos": nodos})
        previas = _filas(cur, "hash_embedding", {"id": incidente_id})

    embebido = False
    texto = registro.texto_embedding()
    huella = cm_incidentes.hash_texto(texto, modelo)
    if embebedor is not None and (not previas or previas[0]["texto_hash"] != huella):
        vectores = embebedor.embed_texts_sync([texto])
        if len(vectores) != 1:
            raise ValueError(f"el embebedor devolvio {len(vectores)} vectores para 1 texto")
        with loader.fase("incidentes_embedding") as cur:
            cur.execute(
                CONSULTAS_INCIDENTES["upsert_embedding"],
                {
                    "id": incidente_id,
                    "embedding": cm_incidentes.vector_literal(vectores[0]),
                    "modelo": modelo,
                    "texto_hash": huella,
                },
            )
        embebido = True

    return RegistroIncidenteOut(
        id=incidente_id, creado=creado, nodos_enlazados=nodos, embebido=embebido
    )


# ---------------------------------------------------------------------------
# Expediente del ticket -> incidente
# ---------------------------------------------------------------------------
def registro_desde_expediente(
    loader: CursorFabrica,
    ticket_id: str,
    *,
    correccion: str | None = None,
    nodos: Sequence[str] = (),
    ficheros: Sequence[str] = (),
) -> RegistroIncidenteIn:
    """Arma el incidente de un ticket a partir de su expediente.

    - resumen: título del ticket.
    - causa: el último extracto de fallo de cada criterio de QA en falla ("AC-n: extracto").
    - corrección: la indicada o, en su defecto, la URL del PR que lo corrigió.
    - nodos: los indicados, los ficheros de los módulos de `tickets.impacto` y sus tablas.
    LookupError si el ticket no existe en el tenant activo.
    """
    with loader.fase("lectura") as cur:
        tickets = _filas(cur, "ticket_expediente", {"ticket_id": ticket_id})
        if not tickets:
            raise LookupError(f"ticket {ticket_id} no encontrado en el tenant activo")
        ticket = tickets[0]
        fallos = _filas(cur, "fallos_qa", {"ticket_id": ticket_id})
        impacto = ticket["impacto"] if isinstance(ticket["impacto"], dict) else {}
        modulos = [str(m) for m in impacto.get("modulos") or [] if m]
        tablas = sorted({str(t).lower() for t in impacto.get("tablas") or [] if t})
        rutas_modulo = (
            [
                str(f["path"])
                for f in _filas(
                    cur, "ficheros_de_modulos", cm_incidentes.parametros_modulos(modulos)
                )
            ]
            if modulos
            else []
        )
        ids_tablas = (
            [str(f["id"]) for f in _filas(cur, "tablas_por_nombre", {"tablas": tablas})]
            if tablas
            else []
        )

    causa = "; ".join(
        f"{f['criterio_id']}: {normalizar(f['extracto_fallo'])}"
        for f in fallos
        if normalizar(f["extracto_fallo"])
    )
    return RegistroIncidenteIn(
        origen="ticket",
        ticket_id=str(ticket["id"]),
        resumen=recortar(ticket["titulo"], RESUMEN_MAX_CHARS) or str(ticket["id"]),
        causa=recortar(causa, CAUSA_MAX_CHARS),
        correccion=recortar(correccion or ticket["pr_url"], CORRECCION_MAX_CHARS),
        evidencia_ref=f"ticket:{ticket['numero'] or ticket['id']}",
        nodos=sorted({*nodos, *ids_tablas})[: cm_incidentes.MAX_NODOS_POR_INCIDENTE],
        ficheros=sorted({*ficheros, *rutas_modulo})[: cm_incidentes.MAX_NODOS_POR_INCIDENTE],
    )


def registrar_desde_expediente(
    loader: CursorFabrica,
    ticket_id: str,
    *,
    correccion: str | None = None,
    nodos: Sequence[str] = (),
    ficheros: Sequence[str] = (),
    embebedor: Embebedor | None = None,
) -> RegistroIncidenteOut:
    """Registra (o actualiza) el incidente de un ticket cerrado con fallo desde su expediente."""
    registro = registro_desde_expediente(
        loader, ticket_id, correccion=correccion, nodos=nodos, ficheros=ficheros
    )
    return registrar_incidente(loader, registro, embebedor=embebedor)
