.PHONY: bootstrap up down db api workers demo-llm workers-demo test lint check-db clean test-env-up test-env-down test-local mutation-codemapping

# Lee los parámetros de make de la base local. Los procesos Python reciben
# el archivo a través de uv, que interpreta las comillas y escapes de dotenv.
-include .env
DOTENV_ARGS = $(if $(wildcard .env),--env-file .env)

# Variables con valores por defecto parametrizados (se sobreescriben con el .env)
KC_DB_HOST ?= localhost
KC_DB_PORT ?= 5432
KC_DB_NAME ?= kallicode
KC_DB_PASSWORD ?= dev

bootstrap:
	@echo "=> Preparando entorno local seguro..."
	@if [ ! -f .env ]; then cp .env.example .env; echo "Creado .env desde .env.example. Revisa las contraseñas si es necesario."; fi
	uv sync --all-packages
	@if git config --get core.hooksPath >/dev/null 2>&1; then \
		echo "Hooks de Git ya configurados; se omite pre-commit install."; \
	else \
		pre-commit install; \
	fi

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

# Roles con los que se construye la base (KC-DB-01-5, ver db/00_roles.sql):
#   postgres        -> SOLO crea la base, los roles y las extensiones (db/00_*.sql)
#   kallicode_admin -> 01..05.sql, migraciones y seeds; queda dueño del esquema
# En local los tres roles usan KC_DB_PASSWORD (la misma de las URLs del .env).
PSQL_SUPER = docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -q
PSQL_ADMIN = docker compose exec -T postgres psql -U kallicode_admin -d $(KC_DB_NAME) -v ON_ERROR_STOP=1 -q

db:
	@echo "=> [postgres] Base de datos $(KC_DB_NAME), roles y extensiones..."
	@$(PSQL_SUPER) -tAc "SELECT 1 FROM pg_database WHERE datname = '$(KC_DB_NAME)'" | grep -q 1 \
		|| $(PSQL_SUPER) -c "CREATE DATABASE $(KC_DB_NAME)"
	@$(PSQL_SUPER) -d $(KC_DB_NAME) -v pw_app="$(KC_DB_PASSWORD)" \
		-v pw_worker="$(KC_DB_PASSWORD)" -v pw_admin="$(KC_DB_PASSWORD)" < db/00_roles.sql
	@$(PSQL_SUPER) -d $(KC_DB_NAME) < db/00_extensiones.sql
	@echo "=> [kallicode_admin] Esquema base (01..05.sql)..."
	@for f in db/0[1-9]_*.sql; do \
		$(PSQL_ADMIN) < "$$f" || exit 1; \
	done
	@echo "=> [kallicode_admin] Migraciones versionadas con el runner (KC-DB-01-1)..."
	@uv run python packages/core/migrate.py \
		--dsn "postgresql://kallicode_admin:$(KC_DB_PASSWORD)@$(KC_DB_HOST):$(KC_DB_PORT)/$(KC_DB_NAME)"
	@echo "=> [kallicode_admin] Datos de prueba (seeds)..."
	@if [ -f db/seeds/dev.sql ]; then $(PSQL_ADMIN) < db/seeds/dev.sql; fi

api:
	@echo "=> Iniciando API en http://localhost:8000 ..."
	PYTHONPATH=packages/core/src:apps/api/src uv run $(DOTENV_ARGS) uvicorn kallicode_api.main:app --reload --port 8000

workers:
	@echo "=> Iniciando Workers..."
	PYTHONPATH=packages/core/src:apps/workers/src uv run $(DOTENV_ARGS) python -m kallicode_workers.main

demo-llm:
	uv run uvicorn llm_demo:app --app-dir tests/e2e --host 127.0.0.1 --port 8082

workers-demo:
	KC_LLM_FLASH_BASE_URL=http://127.0.0.1:8082/v1 KC_LLM_FLASH_MODEL=demo-flash \
	KC_LLM_PRO_BASE_URL=http://127.0.0.1:8082/v1 KC_LLM_PRO_MODEL=demo-pro \
	KC_LLM_FABLE_BASE_URL=http://127.0.0.1:8082/v1 KC_LLM_FABLE_MODEL=demo-fable \
	PYTHONPATH=packages/core/src:apps/workers/src uv run $(DOTENV_ARGS) python -m kallicode_workers.main

test:
	uv run $(DOTENV_ARGS) pytest --cov -q

# KC-CM-06-6: pruebas de mutación de codemapping. Requiere Linux/macOS/WSL (mutmut no corre en
# Windows nativo). Resultado esperado: >= 90 % de mutantes muertos.
mutation-codemapping:
	cd packages/codemapping && uv run --with "mutmut>=3.8,<4" mutmut run
	cd packages/codemapping && uv run --with "mutmut>=3.8,<4" python scripts/verificar_mutacion.py --minimo 90

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
	uv run $(DOTENV_ARGS) pytest
	@echo "=> Limpiando entorno efímero..."
	$(MAKE) test-env-down
	

lint:
	uv run ruff check . && uv run ruff format --check .
	uv run mypy .

ci: lint test

# kallicode_admin (BYPASSRLS): valida los invariantes de TODOS los tenants.
check-db:
	$(PSQL_ADMIN) -c "SELECT * FROM core.validar_integridad() WHERE problemas > 0;"
