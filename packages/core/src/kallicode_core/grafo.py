"""Interfaz del cliente de grafo de código (contrato con CodeMapping).

El backend consume el subgrafo de un ticket, el impacto a k saltos y el
inventario de elementos de interfaz. En el Sprint 4 (KC-BE-04-1) se sirve
con un doble que devuelve subgrafos vacíos o grabados; la implementación
real sobre el esquema cm llega en el Sprint 10 (KC-BE-10-6).

Uso:
    from kallicode_core.grafo import grafo_cliente, Subgrafo, Impacto
    cliente = grafo_cliente()
    sub = await cliente.subgrafo_ticket(tenant_id, ticket_id)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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

    La implementación real (Sprint 10) ejecuta SQL sobre el esquema cm en la
    misma sesión RLS que el resto de la petición. El doble (Sprint 4) devuelve
    subgrafos grabados o vacíos, sin tocar base de datos.
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


def grafo_cliente() -> GrafoCliente:
    """Fábrica del cliente de grafo.

    Sprint 4: devuelve siempre el doble.
    Sprint 10: devolverá la implementación real sobre el esquema cm
    (KC-BE-10-6), sin cambiar la firma de esta función.
    """
    return GrafoClienteDoble()
