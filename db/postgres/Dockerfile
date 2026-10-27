# KC-DB-07-1: PostgreSQL 16 con pgvector (imagen base) + pg_partman.
# pg_partman crea y mantiene las particiones mensuales de audit.audit_events.
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-partman \
    && rm -rf /var/lib/apt/lists/*
