from . import modelos


def pagos_de(session, cliente):
    return session.query(modelos.Payment).filter_by(customer_id=cliente).all()


def sesion_activa(session, token):
    return session.get(modelos.UserSession, token)
