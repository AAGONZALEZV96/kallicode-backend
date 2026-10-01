"""Módulo de facturación — incluye identificadores y textos no ASCII (ñ, á, ü).

Los acentos antes de cada definición desplazan los offsets en bytes respecto
de los offsets en caracteres: sirve para detectar cortes de snippet corridos.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache


def retry(times: int):
    """Decorador con closure de dos niveles."""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            for _ in range(times - 1):
                try:
                    return fn(*args, **kwargs)
                except ValueError:
                    continue
            return fn(*args, **kwargs)

        return wrapper

    return decorator


@dataclass
class Factura:
    número: int
    año: int

    class Linea:
        """Clase anidada nivel 2."""

        def __init__(self, glosa: str, monto: int) -> None:
            self.glosa = glosa
            self.monto = monto

        class Impuesto:
            """Clase anidada nivel 3."""

            def __init__(self, tasa: float = 0.19) -> None:
                self.tasa = tasa

            def aplicar(self, monto: int) -> int:
                return round(monto * (1 + self.tasa))

    @property
    def folio(self) -> str:
        return f"{self.año}-{self.número:06d}"

    @retry(times=3)
    def emitir(self) -> str:
        ordenar = lambda xs: sorted(xs, key=lambda x: x.monto)
        return self.folio + str(len(ordenar([])))


@lru_cache(maxsize=128)
def calcular_año_fiscal(mes: int, año: int) -> int:
    return año if mes >= 4 else año - 1


async def enviar_sii(factura: Factura) -> bool:
    await asyncio.sleep(0)
    return factura.Linea.Impuesto().aplicar(100) > 0
