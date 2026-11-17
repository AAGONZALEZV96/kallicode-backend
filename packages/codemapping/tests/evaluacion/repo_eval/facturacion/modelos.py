"""Modelos declarativos de la facturación (SQLAlchemy 2.0)."""

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .utilidades import redondear


class Base(DeclarativeBase):
    pass


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nif: Mapped[str] = mapped_column(String(12))
    activo: Mapped[bool] = mapped_column(default=True)


class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(primary_key=True)
    precio: Mapped[float] = mapped_column(Numeric(12, 2))


class Factura(Base):
    __tablename__ = "facturas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"))
    lineas = relationship("LineaFactura")

    def total_bruto(self):
        return redondear(sum(linea.importe() for linea in self.lineas))


class LineaFactura(Base):
    __tablename__ = "lineas_factura"

    id: Mapped[int] = mapped_column(primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id"))
    producto_id: Mapped[int] = mapped_column(ForeignKey("productos.id"))
    cantidad: Mapped[int]
    precio: Mapped[float] = mapped_column(Numeric(12, 2))

    def importe(self):
        return redondear(self.cantidad * self.precio)


class Pago(Base):
    __tablename__ = "pagos"

    id: Mapped[int] = mapped_column(primary_key=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id"))
    importe_pagado: Mapped[float] = mapped_column(Numeric(12, 2))
