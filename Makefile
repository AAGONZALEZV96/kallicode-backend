.PHONY: bootstrap up down db api workers test lint check-db clean test-env-up test-env-down test-local

# Carga silenciosa del archivo .env si existe
-include .env
export

# Variables con valores por defecto parametrizados (se sobreescriben con el .env)
KC_DB_HOST ?= localhost
KC_DB_PORT ?= 5432
KC_DB_NAME ?= kallicode
KC_DB_PASSWORD ?= dev

bootstrap:
	@echo "=> Preparando entorno local seguro..."
	@if [ ! -f .env ]; then cp .env.example .env; echo "Creado .env desde .env.example. Revisa las contraseñas si es necesario."; fi
	uv sync --all-packages
	pre-commit install

up:
	@echo "=> Levantando servicios locales (PostgreSQL, Redis, Azurite)..."
	docker compose up -d --remove-orphans

down:
	@echo "=> Deteniendo servicios locales..."
	docker compose down --remove-orphans

clean:
	@echo "=> RESET LOCAL: Destruyendo contenedores y volúmenes locales..."
	@echo "=> (Uso exclusivo de desarrollo local - NUNCA usar en producción)"
	docker compose down -v --remove-orphans

db:
	@echo "=> Asegurando existencia de base de datos y roles de sistema..."
	@docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE $(KC_DB_NAME);" 2>/dev/null || true
	@docker compose exec -T postgres psql -U postgres -d $(KC_DB_NAME) -c "CREATE ROLE kallicode_app WITH LOGIN PASSWORD '$(KC_DB_PASSWORD)';" 2>/dev/null || true
	@docker compose exec -T postgres psql -U postgres -d $(KC_DB_NAME) -c "CREATE ROLE kallicode_admin WITH LOGIN PASSWORD '$(KC_DB_PASSWORD)' BYPASSRLS;" 2>/dev/null || true
	@echo "=> Aplicando esquemas basales (0*.sql)..."
	@for f in db/0*.sql; do \
		if [ -f "$$f" ]; then \
			docker compose exec -T postgres psql -U postgres -d $(KC_DB_NAME) -v ON_ERROR_STOP=1 < "$$f" || exit 1; \
		fi \
	done
	@echo "=> Otorgando permisos de migración al administrador..."
	@docker compose exec -T postgres psql -U postgres -d $(KC_DB_NAME) -c "GRANT ALL ON SCHEMA core, vec, audit TO kallicode_admin;" 2>/dev/null || true
	@docker compose exec -T postgres psql -U postgres -d $(KC_DB_NAME) -c "GRANT CREATE ON DATABASE $(KC_DB_NAME) TO kallicode_admin;" 2>/dev/null || true
	@echo "=> Aplicando migraciones versionadas con el runner (KC-DB-01-1)..."
	@# TODO (KC-DB-01-5): pasar a kallicode_admin cuando 01..05 se apliquen con ese rol;
	@# hoy el dueño del esquema local es postgres y solo él puede alterar sus tablas.
	@uv run python packages/core/migrate.py \
		--dsn "postgresql://postgres:$(KC_DB_PASSWORD)@$(KC_DB_HOST):$(KC_DB_PORT)/$(KC_DB_NAME)"
	@echo "=> Cargando datos de prueba (seeds)..."
	@if [ -f db/seeds/dev.sql ]; then \
		docker compose exec -T postgres psql -U postgres -d $(KC_DB_NAME) -v ON_ERROR_STOP=1 < db/seeds/dev.sql; \
	fi

api:
	@echo "=> Iniciando API en http://localhost:8000 ..."
	PYTHONPATH=packages/core/src:apps/api/src uv run uvicorn kallicode_api.main:app --reload --port 8000

workers:
	@echo "=> Iniciando Workers..."
	PYTHONPATH=packages/core/src:apps/workers/src uv run python -m kallicode_workers.main

test:
	uv run pytest --cov -q

test-env-up:
	@echo "=> Levantando infraestructura efímera de pruebas..."
	docker compose -f docker-compose.test.yml up -d
	@echo "=> Ejecutando healthcheck automatizado..."
	python3 scripts/healthcheck_local.py

test-env-down:
	@echo "=> Destruyendo infraestructura efímera de pruebas..."
	docker compose -f docker-compose.test.yml down -v --remove-orphans
	
test-local: test-env-up
	@echo "=> Preparando base de datos aislada para pruebas..."
	$(MAKE) db KC_DB_NAME=kallicode_test
	@echo "=> Ejecutando suite de pruebas..."
	uv run pytest
	@echo "=> Limpiando entorno efímero..."
	$(MAKE) test-env-down
	

lint:
	uv run ruff check . && uv run ruff format --check .
	uv run mypy .

ci: lint test

check-db:
	docker compose exec -T postgres psql -U kallicode_admin -d $(KC_DB_NAME) -c "SELECT * FROM core.validar_integridad() WHERE problemas > 0;"
