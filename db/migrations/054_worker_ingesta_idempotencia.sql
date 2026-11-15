-- ============================================================================
-- KALLICODE · 054_worker_ingesta_idempotencia.sql
-- KC-CM-10-1 — Worker de ingesta de CodeMapping sobre Redis Streams.
--
-- El worker reserva la idempotencia de cada evento (tenant_id, repo_id, commit_sha) en
-- core.idempotency_keys (endpoint 'codemapping.ingesta') con el mismo patrón que la API:
--   INSERT ... ON CONFLICT DO NOTHING    reserva atómica (status_code 0 = en proceso)
--   UPDATE ... status_code = 200         procesado
--   UPDATE ... creado_en = now()         latido de una reserva viva / toma de una vencida
--   DELETE ... WHERE status_code = 0     libera la reserva tras un fallo
--
-- El worker corre como kallicode_worker (RLS de colas: política worker_colas, 010), que hasta
-- ahora solo tenía SELECT y DELETE sobre esta tabla (la purga por TTL). Se le agregan INSERT y
-- UPDATE. kallicode_app ya los tiene.
--
-- Aditiva y reejecutable. Sin tablas ni índices nuevos.
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kallicode_worker') THEN
        GRANT INSERT, UPDATE ON core.idempotency_keys TO kallicode_worker;
    END IF;
END $$;
