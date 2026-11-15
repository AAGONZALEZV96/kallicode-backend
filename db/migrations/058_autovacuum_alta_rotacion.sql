-- Migración 058: Ajuste de autovacuum y fillfactor en tablas de alta rotación (KC-DB-10-5)
-- Optimiza el mantenimiento automático y reduce el bloat en tablas con alta frecuencia de UPDATE/DELETE.

ALTER TABLE core.jobs SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01,
    fillfactor = 80
);

ALTER TABLE core.production_lines SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02,
    fillfactor = 85
);

ALTER TABLE cm.curation_queue SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01,
    fillfactor = 80
);

ALTER TABLE core.idempotency_keys SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01,
    fillfactor = 85
);