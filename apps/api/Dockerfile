FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY packages ./packages
COPY apps/api ./apps/api
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev || uv sync --no-dev
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "kallicode_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
