.PHONY: bootstrap up down db api workers test lint check-db

# Carga silenciosa del archivo .env para no exponer credenciales en este archivo
-include .env
export

# Usamos la variable del .env, si no existe fallará de forma segura en lugar de usar un default inseguro
export DB_URL = postgresql://postgres:$(KC_DB_PASSWORD)@localhost:5432/kallicode

bootstrap:
	@echo "=> Preparando entorno local seguro..."
	@if [ ! -f .env ]; then cp .env.example .env; echo "Creado .env desde .env.example. Revisa las contraseñas si es necesario."; fi
	uv sync --all-packages
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
	@echo "=> Iniciando API en http://localhost:8000 ..."
	PYTHONPATH=packages/core/src:apps/api/src uv run uvicorn kallicode_api.main:app --reload --port 8000

workers:
	@echo "=> Iniciando Workers..."
	PYTHONPATH=packages/core/src:apps/workers/src uv run python -m kallicode_workers.main

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .
	uv run mypy .

check-db:
	psql $(DB_URL) -c "SELECT * FROM core.validar_integridad() WHERE problemas > 0;"