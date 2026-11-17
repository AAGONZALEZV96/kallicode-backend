from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .modelos import Fantasma, TaxRate, Warehouse, eventos


def almacenes_activos(session: Session):
    return session.scalars(select(Warehouse).where(Warehouse.active)).all()


def subir_iva(session: Session, nueva):
    session.execute(update(TaxRate).where(TaxRate.code == "IVA").values(rate=nueva))


def registrar_evento(conn, tipo):
    conn.execute(eventos.insert().values(tipo=tipo))


def fantasmas(session: Session):
    # Trampa: modelo cuya tabla no existe.
    return session.query(Fantasma).all()
