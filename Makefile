.PHONY: bootstrap up down db api workers test lint check-db

bootstrap:            ## deps + hooks
	uv sync
	pre-commit install || true

up:                   ## infraestructura local
	docker compose up -d

down:
	docker compose down

db:                   ## aplica esquema + migraciones + seeds
	for f in db/0*.sql; do psql postgresql://postgres:dev@localhost/kallicode -v ON_ERROR_STOP=1 -f $$f; done
	for f in db/migrations/*.sql; do [ -e "$$f" ] && psql postgresql://postgres:dev@localhost/kallicode -v ON_ERROR_STOP=1 -f $$f || true; done
	[ -e db/seeds/dev.sql ] && psql postgresql://postgres:dev@localhost/kallicode -f db/seeds/dev.sql || true

api:                  ## API en caliente
	uv run uvicorn kallicode_api.main:app --reload --port 8000

workers:              ## workers en caliente
	uv run python -m kallicode_workers.main

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

check-db:             ## integridad de negocio
	psql postgresql://postgres:dev@localhost/kallicode -c "SELECT * FROM core.validar_integridad() WHERE problemas > 0;"
