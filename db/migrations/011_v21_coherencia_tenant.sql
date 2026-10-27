-- ============================================================================
-- KALLICODE · 011_v21_coherencia_tenant.sql
-- KC-DB-03-3 (DB-F01) — Chequeo V21: coherencia de tenant entre padre e hijo.
--
-- Las claves ajenas escalares (specs.ticket_id -> tickets.id) solo exigen que
-- el padre EXISTA, no que sea del mismo tenant: la base admite una spec de
-- org_A colgada de un ticket de org_B. V21 cuenta esas filas.
--
-- Las relaciones se generan desde pg_constraint (no hay lista a mano): toda
-- FK nueva entre dos tablas con tenant_id queda cubierta sola. Se excluyen:
--   - las FK que ya incluyen tenant_id (la base ya garantiza la coherencia);
--   - las FK hacia tablas sin tenant_id (core.organizations, catálogos).
--
-- Va ANTES de KC-DB-03-2 (FKs compuestas): si V21 encuentra filas, el
-- VALIDATE CONSTRAINT de esa migración fallaría. Repararlas primero con:
--   SELECT * FROM core.incoherencias_tenant();
--
-- El ticket proponía 004b; se usa 011 para respetar el orden cronológico
-- (009 y 010 ya existen). Sin BEGIN/COMMIT: la transacción la abre el runner.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Consulta de reparación: una fila por cada registro incoherente.
--    Ejecutar como kallicode_admin (BYPASSRLS) para ver todos los tenants.
--      SELECT * FROM core.incoherencias_tenant();
--    `fila` identifica la fila hija por su clave primaria, p. ej. {"id": "sp_9"}.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.incoherencias_tenant()
RETURNS TABLE (relacion text, tabla_hija text, tabla_padre text,
               fila jsonb, tenant_hija text, tenant_padre text)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.conname,
               c.conrelid::regclass  AS hija,
               c.confrelid::regclass AS padre,
               -- Condición de unión hija/padre, columna a columna de la FK:
               (SELECT string_agg(format('p.%I = h.%I', pa.attname, ha.attname), ' AND ')
                  FROM unnest(c.conkey, c.confkey) AS k(hijo_n, padre_n)
                  JOIN pg_attribute ha ON ha.attrelid = c.conrelid  AND ha.attnum = k.hijo_n
                  JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.padre_n
               ) AS union_sql,
               -- Clave primaria de la hija como jsonb, para identificar la fila:
               (SELECT string_agg(format('%L, h.%I', a.attname, a.attname), ', ')
                  FROM pg_index i
                  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                 WHERE i.indrelid = c.conrelid AND i.indisprimary
               ) AS pk_sql
          FROM pg_constraint c
         WHERE c.contype = 'f'
           -- la hija y el padre tienen tenant_id ...
           AND EXISTS (SELECT 1 FROM pg_attribute
                        WHERE attrelid = c.conrelid AND attname = 'tenant_id' AND NOT attisdropped)
           AND EXISTS (SELECT 1 FROM pg_attribute
                        WHERE attrelid = c.confrelid AND attname = 'tenant_id' AND NOT attisdropped)
           -- ... y la FK no incluye ya tenant_id (si lo incluye, la base lo garantiza).
           AND NOT EXISTS (SELECT 1 FROM unnest(c.conkey) AS k(n)
                             JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n
                            WHERE a.attname = 'tenant_id')
         ORDER BY c.conrelid::regclass::text, c.conname
    LOOP
        RETURN QUERY EXECUTE format(
            'SELECT %L::text, %L::text, %L::text, %s, h.tenant_id::text, p.tenant_id::text
               FROM %s h JOIN %s p ON %s
              WHERE h.tenant_id <> p.tenant_id',
            r.conname, r.hija::text, r.padre::text,
            CASE WHEN r.pk_sql IS NULL THEN 'jsonb_build_object(''ctid'', h.ctid::text)'
                 ELSE 'jsonb_build_object(' || r.pk_sql || ')' END,
            r.hija, r.padre, r.union_sql);
    END LOOP;
END $$;

COMMENT ON FUNCTION core.incoherencias_tenant() IS
'KC-DB-03-3: filas hijas cuyo tenant_id no coincide con el de su padre, en toda FK
escalar entre dos tablas con tenant_id (generadas desde pg_constraint). Base de V21
y consulta de reparación previa a KC-DB-03-2.';

-- ----------------------------------------------------------------------------
-- 2. validar_integridad(): V01-V20 copiados sin cambios de db/05_validacion.sql
--    + V21. PostgreSQL no permite añadir un chequeo: se reemplaza la función.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.validar_integridad()
RETURNS TABLE (chequeo text, descripcion text, problemas bigint)
LANGUAGE plpgsql STABLE AS $$
BEGIN

-- V01 · Suscripción vigente: todo tenant activo debe tener exactamente una.
chequeo := 'V01_suscripcion_vigente';
descripcion := 'Tenants sin suscripción vigente (o con más de una)';
SELECT count(*) INTO problemas FROM (
    SELECT o.id
      FROM core.organizations o
      LEFT JOIN core.subscriptions s
             ON s.tenant_id = o.id AND s.finaliza_el IS NULL
     WHERE o.eliminado_en IS NULL
     GROUP BY o.id
    HAVING count(s.id) <> 1
) q;
RETURN NEXT;

-- V02 · Ticket ⇄ job: la etapa visible del ticket debe corresponder al job activo.
chequeo := 'V02_espejo_ticket_job';
descripcion := 'Tickets activos cuya etapa no corresponde al estado de su job';
SELECT count(*) INTO problemas
  FROM core.tickets t
  JOIN core.jobs j ON j.ticket_id = t.id
             AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
 WHERE t.etapa <> CASE j.estado
        WHEN 'gate1' THEN 'design'
        WHEN 'gate2' THEN 'qa'
        WHEN 'gate3' THEN 'deploy'
        WHEN 'deploy_preflight' THEN 'deploy'
        WHEN 'pendiente_asignacion' THEN 'triage'
        WHEN 'pausado_por_cuota' THEN 'en_cola_por_cuota'
        WHEN 'escalado_humano' THEN t.etapa
        ELSE j.estado END;
RETURN NEXT;

-- V03 · Gate 3 sin gates 1 y 2 (el trigger lo impide; verificación de defensa).
chequeo := 'V03_gate3_sin_previos';
descripcion := 'Aprobaciones de Gate 3 sin aprobación previa de gates 1 y 2';
SELECT count(*) INTO problemas
  FROM core.gate_signatures g3
 WHERE g3.gate = 3 AND g3.accion = 'aprobado'
   AND 2 > (SELECT count(DISTINCT g.gate) FROM core.gate_signatures g
             WHERE g.ticket_id = g3.ticket_id
               AND g.gate IN (1,2) AND g.accion = 'aprobado'
               AND g.creado_en <= g3.creado_en);
RETURN NEXT;

-- V04 · Tickets en producción con las tres firmas.
chequeo := 'V04_produccion_sin_firmas';
descripcion := 'Tickets en producción sin las tres firmas de gates';
SELECT count(*) INTO problemas
  FROM core.tickets t
 WHERE t.etapa = 'produccion'
   AND 3 > (SELECT count(DISTINCT gate) FROM core.gate_signatures
             WHERE ticket_id = t.id AND accion = 'aprobado');
RETURN NEXT;

-- V05 · Specs: exactamente una alternativa recomendada por versión.
chequeo := 'V05_spec_recomendada';
descripcion := 'Specs sin exactamente una alternativa recomendada';
SELECT count(*) INTO problemas FROM (
    SELECT s.id FROM core.specs s
      LEFT JOIN core.spec_alternatives a ON a.spec_id = s.id AND a.recomendada
     GROUP BY s.id HAVING count(a.alt_id) <> 1
) q;
RETURN NEXT;

-- V06 · Specs aprobadas con alternativa elegida.
chequeo := 'V06_spec_aprobada_sin_eleccion';
descripcion := 'Specs aprobadas (Gate 1) sin alternativa elegida';
SELECT count(*) INTO problemas FROM (
    SELECT s.id FROM core.specs s
     WHERE s.estado = 'aprobada'
       AND NOT EXISTS (SELECT 1 FROM core.spec_alternatives a
                        WHERE a.spec_id = s.id AND a.elegida)
       AND EXISTS (SELECT 1 FROM core.spec_alternatives a WHERE a.spec_id = s.id)
) q;
RETURN NEXT;

-- V07 · Matriz de QA completa: cada corrida cubre todos los criterios de su spec.
chequeo := 'V07_matriz_qa_completa';
descripcion := 'Corridas de QA que no cubren todos los criterios de la spec';
SELECT count(*) INTO problemas FROM (
    SELECT r.id
      FROM core.qa_runs r
      JOIN core.spec_criteria c ON c.spec_id = r.spec_id
      LEFT JOIN core.qa_results q ON q.qa_run_id = r.id AND q.criterio_id = c.criterio_id
     GROUP BY r.id
    HAVING count(c.criterio_id) <> count(q.criterio_id)
) q;
RETURN NEXT;

-- V08 · Resultados de QA que referencian criterios inexistentes en la spec.
chequeo := 'V08_qa_criterio_fantasma';
descripcion := 'Resultados de QA con criterio_id que no existe en la spec de la corrida';
SELECT count(*) INTO problemas
  FROM core.qa_results q
  JOIN core.qa_runs r ON r.id = q.qa_run_id
 WHERE NOT EXISTS (SELECT 1 FROM core.spec_criteria c
                    WHERE c.spec_id = r.spec_id AND c.criterio_id = q.criterio_id);
RETURN NEXT;

-- V09 · Deploy bloqueado: tickets en produccion con hallazgos bloqueantes abiertos.
chequeo := 'V09_produccion_con_bloqueantes';
descripcion := 'Tickets en producción con hallazgos de seguridad bloqueantes abiertos';
SELECT count(*) INTO problemas
  FROM core.tickets t
 WHERE t.etapa = 'produccion'
   AND EXISTS (SELECT 1 FROM core.security_findings f
                WHERE f.ticket_id = t.id AND f.bloquea AND f.estado = 'abierto');
RETURN NEXT;

-- V10 · Agregado de cuota: usage_cycles debe cuadrar con la suma de usage_loc.
chequeo := 'V10_cuota_cuadra';
descripcion := 'Ciclos cuyo agregado no cuadra con la suma de usage_loc';
SELECT count(*) INTO problemas FROM (
    SELECT c.tenant_id, c.ciclo
      FROM core.usage_cycles c
      LEFT JOIN core.usage_loc l ON l.tenant_id = c.tenant_id AND l.ciclo = c.ciclo
     GROUP BY c.tenant_id, c.ciclo, c.loc_consumidas
    HAVING c.loc_consumidas <> coalesce(sum(greatest(l.netas,0)), 0)
) q;
RETURN NEXT;

-- V11 · Líneas: coherencia production_lines ⇄ jobs.
chequeo := 'V11_lineas_coherentes';
descripcion := 'Líneas ocupadas sin job activo o jobs con línea no reflejada';
SELECT count(*) INTO problemas FROM (
    SELECT pl.tenant_id, pl.numero
      FROM core.production_lines pl
      LEFT JOIN core.jobs j ON j.id = pl.job_id
             AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
     WHERE (pl.estado = 'ocupada') <> (j.id IS NOT NULL)
    UNION ALL
    SELECT j.tenant_id, j.linea
      FROM core.jobs j
      LEFT JOIN core.production_lines pl
             ON pl.tenant_id = j.tenant_id AND pl.numero = j.linea AND pl.job_id = j.id
     WHERE j.linea IS NOT NULL
       AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
       AND pl.job_id IS NULL
) q;
RETURN NEXT;

-- V12 · Líneas concurrentes dentro del plan (QU-2).
chequeo := 'V12_lineas_dentro_del_plan';
descripcion := 'Tenants con más jobs en línea que líneas de su plan';
SELECT count(*) INTO problemas FROM (
    SELECT j.tenant_id
      FROM core.jobs j
      JOIN core.subscriptions s ON s.tenant_id = j.tenant_id AND s.finaliza_el IS NULL
      JOIN core.plans p ON p.codigo = s.plan_codigo
     WHERE j.linea IS NOT NULL
       AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado')
     GROUP BY j.tenant_id, p.lineas
    HAVING p.lineas IS NOT NULL AND count(*) > p.lineas
) q;
RETURN NEXT;

-- V13 · Duplicados: cadena sin ciclos de un nivel y sin duplicado_de colgante.
chequeo := 'V13_duplicados_validos';
descripcion := 'Tickets duplicados que apuntan a otro duplicado (cadenas)';
SELECT count(*) INTO problemas
  FROM core.tickets t
  JOIN core.tickets d ON d.id = t.duplicado_de
 WHERE d.duplicado_de IS NOT NULL;
RETURN NEXT;

-- V14 · Embeddings huérfanos o faltantes (vec ⇄ core).
chequeo := 'V14_embeddings_tickets';
descripcion := 'Tickets activos (>1 h) sin embedding de deduplicación';
SELECT count(*) INTO problemas
  FROM core.tickets t
 WHERE t.etapa NOT IN ('cancelado','cerrado_duplicado')
   AND t.creado_en < now() - interval '1 hour'
   AND NOT EXISTS (SELECT 1 FROM vec.ticket_embeddings e WHERE e.ticket_id = t.id);
RETURN NEXT;

-- V15 · Dimensión de embeddings homogénea (1024).
chequeo := 'V15_dimension_embeddings';
descripcion := 'Embeddings con dimensión distinta de 1024';
SELECT (SELECT count(*) FROM vec.ticket_embeddings     WHERE vector_dims(embedding) <> 1024)
     + (SELECT count(*) FROM vec.doc_chunks            WHERE vector_dims(embedding) <> 1024)
     + (SELECT count(*) FROM vec.definition_embeddings WHERE vector_dims(embedding) <> 1024)
  INTO problemas;
RETURN NEXT;

-- V16 · Cadena de auditoría íntegra (recorre todos los tenants visibles).
chequeo := 'V16_cadena_auditoria';
descripcion := 'Tenants con cadena de hashes rota (ver audit.verify_chain)';
SELECT count(*) INTO problemas FROM (
    SELECT h.tenant_id
      FROM audit.chain_heads h,
           LATERAL audit.verify_chain(h.tenant_id) v
     WHERE NOT v.integra
) q;
RETURN NEXT;

-- V17 · Cabeza de cadena consistente con el último evento.
chequeo := 'V17_cabeza_cadena';
descripcion := 'chain_heads cuyo último sello no coincide con el último evento';
SELECT count(*) INTO problemas FROM (
    SELECT h.tenant_id
      FROM audit.chain_heads h
      LEFT JOIN LATERAL (
          SELECT sello FROM audit.audit_events e
           WHERE e.tenant_id = h.tenant_id
           ORDER BY e.id DESC LIMIT 1) ult ON true
     WHERE h.eventos > 0 AND ult.sello IS DISTINCT FROM h.ultimo_sello
) q;
RETURN NEXT;

-- V18 · Webhooks: fallidos estancados (>24 h sin reproceso).
chequeo := 'V18_webhooks_estancados';
descripcion := 'Entradas de webhook_inbox fallidas hace más de 24 h';
SELECT count(*) INTO problemas
  FROM core.webhook_inbox
 WHERE estado = 'fallido' AND recibido_en < now() - interval '24 hours';
RETURN NEXT;

-- V19 · Adjuntos huérfanos vencidos (>48 h sin asociar): candidatos a purga.
chequeo := 'V19_adjuntos_huerfanos';
descripcion := 'Adjuntos sin ticket asociado hace más de 48 h';
SELECT count(*) INTO problemas
  FROM core.ticket_attachments
 WHERE ticket_id IS NULL AND creado_en < now() - interval '48 hours';
RETURN NEXT;

-- V20 · Gates aprobados apuntando a specs de otro ticket.
chequeo := 'V20_gate_spec_coherente';
descripcion := 'Firmas de Gate 1 cuya spec no pertenece al mismo ticket';
SELECT count(*) INTO problemas
  FROM core.gate_signatures g
  JOIN core.specs s ON s.id = g.spec_id
 WHERE g.spec_id IS NOT NULL AND s.ticket_id <> g.ticket_id;
RETURN NEXT;


-- V21 · Coherencia de tenant padre/hijo (KC-DB-03-3, DB-F01). Las relaciones se
--       generan desde pg_constraint: ver core.incoherencias_tenant().
chequeo := 'V21_coherencia_tenant';
SELECT count(*),
       'Filas cuyo tenant no coincide con el de su padre'
       || coalesce(' en: ' || string_agg(DISTINCT relacion || ' (' || tabla_hija || ')', ', '), '')
  INTO problemas, descripcion
  FROM core.incoherencias_tenant();
RETURN NEXT;

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría; V10/V12 la validación comercial; V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0).';
