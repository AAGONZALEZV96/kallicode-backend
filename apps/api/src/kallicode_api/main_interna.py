"""Aplicación ASGI interna con listener independiente.

uvicorn kallicode_api.main_interna:app --host 127.0.0.1 --port 8001
"""

from fastapi import FastAPI

from .main import crear_app
from .metricas import metrics


def crear_app_interna() -> FastAPI:
    interna = crear_app(interna=True)
    # Prometheus accede desde la red del listener privado, fuera del router de servicios.
    interna.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)
    return interna


app = crear_app_interna()
