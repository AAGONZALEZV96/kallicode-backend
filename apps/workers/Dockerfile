# Etapa 1: Builder (Compilación y dependencias)
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps/workers ./apps/workers

RUN uv sync --frozen --no-dev

# Etapa 2: Final (Imagen ligera y segura)
FROM python:3.12-slim
WORKDIR /app

# Creamos el usuario y grupo 'app' sin privilegios
RUN addgroup --system app && adduser --system --group app

# Copiamos desde el builder asignando propiedad
COPY --from=builder --chown=app:app /app /app

# Ajustamos el PATH al entorno virtual
ENV PATH="/app/.venv/bin:$PATH"

# Cambiamos al usuario seguro
USER app

CMD ["python", "-m", "kallicode_workers.main"]