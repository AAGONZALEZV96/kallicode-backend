"""Registro de auditoría en SQL crudo."""


def registrar_auditoria(conn, entidad, entidad_id, accion):
    conn.execute(
        "INSERT INTO auditoria (entidad, entidad_id, accion) VALUES (%s, %s, %s)",
        (entidad, entidad_id, accion),
    )
