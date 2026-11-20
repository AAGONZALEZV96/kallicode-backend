FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY apps ./apps
COPY packages ./packages

# Copiamos el script de arranque para que exista dentro del contenedor
COPY start.sh ./start.sh

RUN chmod +x ./start.sh

ENV PYTHONPATH="/app/apps/api/src:/app/packages/core/src:/app/apps/workers/src:/app/packages/codemapping/src"

# CMD por defecto: Arranca la API pública principal
CMD ["sh", "-c", "uvicorn kallicode_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]