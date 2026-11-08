"""Aplicación ASGI interna con listener independiente.

uvicorn kallicode_api.main_interna:app --host 127.0.0.1 --port 8001
"""

from fastapi import FastAPI

from .main import crear_app


def crear_app_interna() -> FastAPI:
    return crear_app(interna=True)


app = crear_app_interna()