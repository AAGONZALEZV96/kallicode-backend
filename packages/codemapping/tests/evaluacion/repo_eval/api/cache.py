"""Caché en memoria de las respuestas de la API."""

import time


class Cache:
    def __init__(self, ttl_s=60):
        self.ttl_s = ttl_s
        self._datos = {}

    def get(self, clave):
        valor, caduca = self._datos.get(clave, (None, 0))
        return valor if caduca > time.monotonic() else None

    def guardar(self, clave, valor):
        self._datos[clave] = (valor, time.monotonic() + self.ttl_s)
