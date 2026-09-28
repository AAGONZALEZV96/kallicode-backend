"""Construcción centralizada de claves de Redis, siempre con tenant_id incluido.

Evita que una clave se construya sin tenant por olvido — un solo punto de
verdad para el formato, en vez de que cada router arme el string a mano.
"""


def clave_redis(tenant_id: str, *partes: str) -> str:
    """
    Construye una clave de Redis con el tenant_id incluido en el namespace.

    Uso:
        clave_redis(tenant_id, "preflight", numero, destino)
        -> "preflight:<tenant_id>:<numero>:<destino>"
    """
    if not tenant_id:
        raise ValueError("clave_redis requiere tenant_id no vacío")
    prefijo, *resto = partes
    return f"{prefijo}:{tenant_id}:{':'.join(str(p) for p in resto)}"
