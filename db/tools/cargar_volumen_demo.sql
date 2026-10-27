-- ============================================================================
-- KALLICODE · db/tools/cargar_volumen_demo.sql
-- Carga un volumen sintético para comparar planes de ejecución (KC-DB-06-2).
--
-- Con las tablas de demostración (pocas filas) el planificador recorre siempre la tabla entera
-- y un plan «antes y después» no dice nada. Este script crea varios tenants con tickets y los
-- datos que cuelgan de ellos (firmas de gate, pasos del LLM, specs, corridas de QA, consumo de
-- tokens y de LOC, adjuntos y notificaciones) y deja las estadísticas al día.
--
-- SOLO PARA ENTORNOS DE DEMOSTRACIÓN O DE PRUEBA. Crea organizaciones `org_vol_<n>` y no borra
-- nada; para repetirlo, descarta la base. Hay que ejecutarlo con un rol que ignore RLS
-- (kallicode_admin):
--
--   psql -d <base> -v ON_ERROR_STOP=1 -v tenants=3 -v tickets=1500 -f db/tools/cargar_volumen_demo.sql
-- ============================================================================
\set QUIET on
\if :{?tenants}
\else
\set tenants 3
\endif
\if :{?tickets}
\else
\set tickets 1500
\endif

\echo 'Cargando volumen de demostración:' :tenants 'tenants x' :tickets 'tickets...'

INSERT INTO core.organizations (id, nombre)
SELECT 'org_vol_' || t, 'Organización de volumen ' || t FROM generate_series(1, :tenants) t
ON CONFLICT (id) DO NOTHING;

-- 60 usuarios por tenant
INSERT INTO core.users (id, tenant_id, email, nombre)
SELECT 'us_vol_' || t || '_' || u, 'org_vol_' || t, 'u' || u || '@vol' || t || '.test', 'Usuario ' || u
  FROM generate_series(1, :tenants) t, generate_series(1, 60) u
ON CONFLICT DO NOTHING;

-- Tickets (el correlativo KC-#### lo asigna el trigger)
INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion)
SELECT 'tk_vol_' || t || '_' || n, 'org_vol_' || t, 'bug',
       'Ticket de volumen ' || n || ' del tenant ' || t, 'Descripción de prueba ' || n
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n
ON CONFLICT DO NOTHING;

-- Un job por ticket
INSERT INTO core.jobs (id, tenant_id, ticket_id, prioridad)
SELECT 'jb_vol_' || t || '_' || n, 'org_vol_' || t, 'tk_vol_' || t || '_' || n, 'P3'
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n
ON CONFLICT DO NOTHING;

-- Firmas de gate: gates 1 y 2 aprobados y una petición de cambios del gate 1
INSERT INTO core.gate_signatures (id, tenant_id, ticket_id, gate, accion, actor_id, sello, comentario, devolver_a)
SELECT 'gs_vol_' || t || '_' || n || '_' || g || a, 'org_vol_' || t, 'tk_vol_' || t || '_' || n, g, acc,
       'us_vol_' || t || '_' || (1 + n % 60), 'sello', com, dev
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n,
       (VALUES (1, 'aprobado', 'a', NULL, NULL),
               (2, 'aprobado', 'a', NULL, NULL),
               (1, 'cambios_pedidos', 'c', 'Ajustar el alcance', 'design')) AS x(g, acc, a, com, dev)
ON CONFLICT DO NOTHING;

-- Pasos del LLM: seis por ticket
INSERT INTO core.llm_steps (id, tenant_id, job_id, ticket_id, etapa, paso, version_plantilla, modelo,
                            tier, validacion, tokens_in, tokens_out, duracion_ms, entrada_ref, salida_ref)
SELECT 'st_vol_' || t || '_' || n || '_' || k, 'org_vol_' || t, 'jb_vol_' || t || '_' || n,
       'tk_vol_' || t || '_' || n, (ARRAY['triage','design','build','qa','security','deploy'])[k],
       'paso.' || k, 'v1', 'modelo-demo', 'flash', 'esquema_valido', 100 * k, 50 * k, 200 * k, 'e', 's'
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n, generate_series(1, 6) k
ON CONFLICT DO NOTHING;

-- Dos versiones de spec por ticket y dos corridas de QA
INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref, scope_paths)
SELECT 'sp_vol_' || t || '_' || n || '_' || v, 'org_vol_' || t, 'tk_vol_' || t || '_' || n, v,
       'blob/spec.md', ARRAY['src/']
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n, generate_series(1, 2) v
ON CONFLICT DO NOTHING;

INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion)
SELECT 'qr_vol_' || t || '_' || n || '_' || c, 'org_vol_' || t, 'tk_vol_' || t || '_' || n,
       'sp_vol_' || t || '_' || n || '_1', c, '{"dirigidos": 3, "dirigidos_ok": 3}'::jsonb
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n, generate_series(1, 2) c
ON CONFLICT DO NOTHING;

-- Consumo de tokens (cuatro filas por ticket) y de LOC (una por ticket)
INSERT INTO core.usage_tokens (tenant_id, ticket_id, etapa, modelo, tier, tokens_in, tokens_out, duracion_ms)
SELECT 'org_vol_' || t, 'tk_vol_' || t || '_' || n, (ARRAY['triage','design','build','qa'])[k],
       'modelo-demo', 'flash', 100 * k, 40 * k, 300 * k
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n, generate_series(1, 4) k;

INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, pr_url, anadidas, eliminadas, netas)
SELECT 'org_vol_' || t, 'tk_vol_' || t || '_' || n, '2026-10', 'pr',
       'https://git.demo/vol/' || t || '/' || n, 20, 5, 15
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n
ON CONFLICT DO NOTHING;

-- Adjuntos: dos por ticket
INSERT INTO core.ticket_attachments (id, tenant_id, ticket_id, nombre_archivo, content_type, tamano_bytes, blob_ref)
SELECT 'at_vol_' || t || '_' || n || '_' || k, 'org_vol_' || t, 'tk_vol_' || t || '_' || n,
       'captura' || k || '.png', 'image/png', 1024, 'blob/' || t || '/' || n || '/' || k
  FROM generate_series(1, :tenants) t, generate_series(1, :tickets) n, generate_series(1, 2) k
ON CONFLICT DO NOTHING;

-- Notificaciones: 150 por usuario, el 5 % sin leer
INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, leida)
SELECT 'nt_vol_' || t || '_' || u || '_' || k, 'org_vol_' || t, 'us_vol_' || t || '_' || u,
       'sistema', 'Notificación ' || k, (k % 20) <> 0
  FROM generate_series(1, :tenants) t, generate_series(1, 60) u, generate_series(1, 150) k
ON CONFLICT DO NOTHING;

-- Tokens de refresco: tres por usuario
INSERT INTO core.refresh_tokens (id, tenant_id, user_id, token_hash, expira_en)
SELECT 'rt_vol_' || t || '_' || u || '_' || k, 'org_vol_' || t, 'us_vol_' || t || '_' || u,
       md5('rt_vol_' || t || '_' || u || '_' || k), now() + interval '7 days'
  FROM generate_series(1, :tenants) t, generate_series(1, 60) u, generate_series(1, 3) k
ON CONFLICT DO NOTHING;

ANALYZE;

\echo 'Volumen cargado. Filas por tabla:'
SELECT 'tickets' AS tabla, count(*) FROM core.tickets WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'gate_signatures', count(*) FROM core.gate_signatures WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'llm_steps', count(*) FROM core.llm_steps WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'specs', count(*) FROM core.specs WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'qa_runs', count(*) FROM core.qa_runs WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'usage_tokens', count(*) FROM core.usage_tokens WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'usage_loc', count(*) FROM core.usage_loc WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'ticket_attachments', count(*) FROM core.ticket_attachments WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'notifications', count(*) FROM core.notifications WHERE tenant_id LIKE 'org_vol_%'
UNION ALL SELECT 'refresh_tokens', count(*) FROM core.refresh_tokens WHERE tenant_id LIKE 'org_vol_%';
