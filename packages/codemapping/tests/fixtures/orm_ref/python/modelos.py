"""Modelos declarativos de SQLAlchemy 2.0 y una tabla Core."""

from sqlalchemy import Column, Integer, MetaData, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = {"schema": "inventory"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(primary_key=True)
    active: Mapped[bool]


class TaxRate(Base):
    __tablename__ = "tax_rates"
    __table_args__ = (UniqueConstraint("code"), {"schema": "billing"})

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8))


class UserSession(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(primary_key=True)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = {"schema": "billing"}  # noqa: RUF012

    id: Mapped[int] = mapped_column(primary_key=True)


class Fantasma(Base):
    # Trampa: tabla que no existe en la base del cliente.
    __tablename__ = "tabla_inventada"

    id: Mapped[int] = mapped_column(primary_key=True)


metadata = MetaData()

eventos = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("tipo", String(40)),
    schema="audit",
)
