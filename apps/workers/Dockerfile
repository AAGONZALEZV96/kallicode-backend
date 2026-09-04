FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY packages ./packages
COPY apps/workers ./apps/workers
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev || uv sync --no-dev
CMD ["uv", "run", "python", "-m", "kallicode_workers.main"]
