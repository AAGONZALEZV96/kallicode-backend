FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps ./apps
COPY packages ./packages
ENV PYTHONPATH="/app/apps/api/src:/app/packages/core/src:/app/apps/workers/src:/app/packages/codemapping/src"
CMD ["sh", "-c", "uvicorn kallicode_api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]