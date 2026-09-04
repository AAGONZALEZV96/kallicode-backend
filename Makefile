.PHONY: bootstrap up down db api workers test lint check-db

export KC_DB_PASSWORD ?= KalliCode_Dev2026!
export DB_URL = postgresql://postgres:$(KC_DB_PASSWORD)@localhost:5432/kallicode

bootstrap:
	uv sync
	pre-commit install

up:
	docker compose up -d

down:
	docker compose down

db:
	@echo "=> Aplicando esquema inicial..."
	for f in db/0*.sql; do psql $(DB_URL) -v ON_ERROR_STOP=1 -f $$f; done
	@echo "=> Aplicando migraciones pendientes..."
	for f in db/migrations/*.sql; do \
		if [ -f "$$f" ]; then psql $(DB_URL) -v ON_ERROR_STOP=1 -f "$$f"; fi \
	done
	@echo "=> Inyectando seeds..."
	if [ -f db/seeds/dev.sql ]; then psql $(DB_URL) -v ON_ERROR_STOP=1 -f db/seeds/dev.sql; fi

api:
	uv run uvicorn apps.api.src.kallicode_api.main:app --reload --port 8000

workers:
	uv run python -m apps.workers.src.kallicode_workers.main

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .
	uv run mypy packages/core apps/api apps/workers --strict

check-db:
	psql $(DB_URL) -c "SELECT * FROM core.validar_integridad() WHERE problemas > 0;"