"""Interfaz y cliente PostgreSQL del grafo de código (contrato con CodeMapping).

El backend consume el subgrafo de un ticket, el impacto a k saltos y el
inventario de elementos de interfaz sobre el esquema `cm` con aislamiento
multi-tenant vía RLS (`core.current_tenant()`). El doble (`GrafoClienteDoble`)
se conserva para pruebas unitarias deterministas sin base de datos.

Uso:
    from kallicode_core.grafo import grafo_cliente, Subgrafo, Impacto
    cliente = grafo_cliente()
    sub = await cliente.subgrafo_ticket(tenant_id, ticket_id)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def fase_confirmada_ingesta(stats: dict[str, object]) -> int | None:
    """Lee el último checkpoint confirmado por el pipeline de CodeMapping."""
    fase = stats.get("checkpoint_fase")
    if type(fase) is int and 0 <= fase <= 7:
        return fase
    return None


def pendientes_grafo(fase: int | None, cobertura: float | None) -> list[str]:
    pendientes: list[str] = []
    if fase is None or fase < 6:
        pendientes.append("fase_grafo")
    if cobertura is None or cobertura < 70:
        pendientes.append("cobertura_grafo")
    return pendientes


@dataclass(frozen=True)
class Subgrafo:
    """Subgrafo de código asociado a un ticket.

    Los nodos y aristas se representan como tuplas de diccionarios para que
    el objeto sea inmutable y hashable, y para no acoplar el contrato a la
    forma exacta del esquema cm (que pertenece a CodeMapping).
    """

    nodos: tuple[dict, ...] = field(default_factory=tuple)
    aristas: tuple[dict, ...] = field(default_factory=tuple)

    def esta_vacio(self) -> bool:
        return not self.nodos and not self.aristas


@dataclass(frozen=True)
class Impacto:
    """Resultado del análisis de impacto sobre un nodo del grafo.

    `radio` es el número de saltos hasta el nodo más lejano afectado.
    `riesgo` es una etiqueta cualitativa (bajo | medio | alto).
    `modulos` y `tablas` son los identificadores afectados.
    """

    radio: int = 0
    riesgo: str = "bajo"
    modulos: tuple[str, ...] = field(default_factory=tuple)
    tablas: tuple[str, ...] = field(default_factory=tuple)

    def esta_vacio(self) -> bool:
        return self.radio == 0 and not self.modulos and not self.tablas


@runtime_checkable
class GrafoCliente(Protocol):
    """Contrato estable que consume el orquestador.

    La implementación real (KC-BE-10-6) ejecuta SQL sobre el esquema cm en
    sesión RLS confinada al tenant. El doble (KC-BE-04-1) devuelve subgrafos
    grabados o vacíos, sin tocar base de datos.
    """

    async def subgrafo_ticket(self, tenant_id: str, ticket_id: str) -> Subgrafo:
        """Subgrafo de código relevante para el ticket."""
        ...

    async def impacto(self, tenant_id: str, nodo_ref: str, k: int = 2) -> Impacto:
        """Impacto en el grafo al tocar `nodo_ref`, con radio máximo `k`."""
        ...

    async def inventario_ui(self, tenant_id: str) -> list[dict]:
        """Inventario de elementos de interfaz del tenant."""
        ...


class GrafoClientePostgres:
    """Cliente real de grafo sobre PostgreSQL 16 (esquemas `cm` y `vec` con RLS).

    Puede reutilizar una `AsyncSession` ya confinada al tenant (por ejemplo,
    dentro de un endpoint de API) o abrir una transacción `sesion_tenant`
    propia cuando lo invoca el orquestador.
    """

    def __init__(self, sesion: AsyncSession | None = None) -> None:
        self._sesion = sesion

    @asynccontextmanager
    async def _db(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        if self._sesion is not None:
            yield self._sesion
            return
        from . import cm_impacto
        from .db import sesion_tenant

        async with sesion_tenant(
            tenant_id,
            actor="svc:grafo",
            statement_timeout_ms=cm_impacto.TIEMPO_MAXIMO_MS,
        ) as db:
            yield db

    async def subgrafo_ticket(self, tenant_id: str, ticket_id: str) -> Subgrafo:
        from . import consultas_cm

        async with self._db(tenant_id) as db:
            return await consultas_cm.consultar_subgrafo_ticket(db, ticket_id)

    async def impacto(self, tenant_id: str, nodo_ref: str, k: int = 2) -> Impacto:
        from . import consultas_cm

        async with self._db(tenant_id) as db:
            return await consultas_cm.consultar_impacto(db, nodo_ref, k=k)

    async def inventario_ui(self, tenant_id: str) -> list[dict]:
        from . import consultas_cm

        async with self._db(tenant_id) as db:
            return await consultas_cm.consultar_inventario_ui(db)


class GrafoClienteDoble:
    """Doble de prueba: subgrafos vacíos o grabados, sin tocar la base.

    Se inyecta en los tests para que las etapas del orquestador se ejecuten
    de forma determinista sin depender de CodeMapping ni del esquema cm.
    """

    def __init__(self, subgrafos: dict[str, Subgrafo] | None = None) -> None:
        self._subgrafos: dict[str, Subgrafo] = subgrafos or {}
        self._impactos: dict[str, Impacto] = {}

    def registrar_subgrafo(self, ticket_id: str, subgrafo: Subgrafo) -> None:
        self._subgrafos[ticket_id] = subgrafo

    def registrar_impacto(self, nodo_ref: str, impacto: Impacto) -> None:
        self._impactos[nodo_ref] = impacto

    async def subgrafo_ticket(self, tenant_id: str, ticket_id: str) -> Subgrafo:
        return self._subgrafos.get(ticket_id, Subgrafo())

    async def impacto(self, tenant_id: str, nodo_ref: str, k: int = 2) -> Impacto:
        return self._impactos.get(nodo_ref, Impacto())

    async def inventario_ui(self, tenant_id: str) -> list[dict]:
        return []


def grafo_cliente(sesion: AsyncSession | None = None) -> GrafoCliente:
    """Fábrica del cliente de grafo sobre PostgreSQL (`GrafoClientePostgres`)."""
    return GrafoClientePostgres(sesion=sesion)
