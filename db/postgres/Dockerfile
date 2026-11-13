# KC-DB-07-1 / KC-DB-08-2: PostgreSQL 16 con pgvector (imagen base) + pg_partman + pg_cron.
# pg_partman crea y mantiene las particiones mensuales; pg_cron programa el REINDEX CONCURRENTLY
# de los índices HNSW (docs/runbook-vectorial.md). pg_cron exige shared_preload_libraries=pg_cron y
# cron.database_name (docker-compose.yml).
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-partman postgresql-16-cron \
    && rm -rf /var/lib/apt/lists/*
