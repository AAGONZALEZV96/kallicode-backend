# Etapa 1: Builder (Compilación y dependencias)
FROM python:3.12-slim AS builder
# Traemos el binario de uv directamente desde su imagen oficial (más rápido y seguro que pip)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

# Copiamos solo lo necesario para la instalación
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/api ./apps/api

# Instalamos en un entorno virtual aislado (.venv) sin dependencias de desarrollo
RUN uv sync --frozen --no-dev

# Etapa 2: Final (Imagen ligera y segura)
FROM python:3.12-slim
WORKDIR /app

# Creamos el usuario y grupo 'app' sin privilegios de root
RUN addgroup --system app && adduser --system --group app

# Copiamos desde el builder el código y el entorno virtual, asignando propiedad al usuario 'app'
COPY --from=builder --chown=app:app /app /app

# Exponemos el entorno virtual al PATH para no tener que prefijar comandos con "uv run"
ENV PATH="/app/.venv/bin:$PATH"

# Cambiamos al usuario seguro antes de arrancar
USER app

EXPOSE 8000
CMD ["uvicorn", "kallicode_api.main:app", "--host", "0.0.0.0", "--port", "8000"]