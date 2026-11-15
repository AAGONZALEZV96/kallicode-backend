-- ============================================================================
-- KALLICODE · 057_informe_retencion.sql
-- KC-DB-10-4 (5 SP) — Informe de cumplimiento de retención por tabla.
--
-- La retención comprometida (evidencia un año según D14, webhooks 90 días, auditoría permanente) no tenía forma
-- de acreditarse. Esta migración publica, por tabla, la política declarada, el dato más antiguo, la última purga,
-- las particiones exportadas a WORM y la desviación:
--
--   1. core.retencion_dedicada      tablas que se purgan FUERA del motor de core.retention_policies, con su plazo y
--                                   su ejecutor: idempotency_keys (TTL de 48 h, KC-DB-10-3), refresh_tokens y adjuntos
--                                   huérfanos (housekeeping). No se declaran como 'drop' en retention_policies porque
--                                   el motor (054) las purgaría por segunda vez con otra lógica.
--   2. core.tablas_retencion()      el universo del informe: toda tabla (no partición) de core, audit, vec y cm.
--   3. Declaración 'conservar'      para cada tabla de ese universo sin política, con su motivo. Recoge la situación
--                                   de hecho (nada las purga): fijar un plazo es una decisión de negocio posterior.
--   4. V26 ampliado                 core.contar_tablas_sin_retencion() cuenta TODAS las tablas del universo sin
--                                   política de plataforma activa ni purga dedicada (antes, solo las particionadas).
--                                   core.validar_integridad() se redefine solo para actualizar la descripción de V26
--                                   (V01-V26 idénticos a 055).
--   5. core.reporte_cumplimiento_retencion(p_ahora, p_tolerancia)   el informe (solo lectura).
--   6. core.alertas_retencion(p_ahora, p_tolerancia)                las filas cuyo dato más antiguo supera la
--                                   retención declarada en más de p_tolerancia (7 días por defecto).
--
-- Antigüedad de una tabla particionada por rango: se mide por el LÍMITE SUPERIOR de la partición que contiene el
-- dato más antiguo, no por la fila. El motor solo descarta particiones enteras (límite superior <= ahora - plazo),
-- así que una fila puede vivir hasta un mes más que el plazo sin que haya incumplimiento; medir por la fila daría
-- una alerta falsa casi todo el mes.
--
-- Nota: el ticket nombra db/tools/informe_retencion.sql (el script que invoca esta función),
-- docs/cumplimiento-retencion.md y tests/test_informe_retencion.py (vive en packages/core/tests/integracion/).
-- Aditiva y re-ejecutable. Sin índices nuevos. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- ---------------------------------------------------------------------------
-- 1. Purgas dedicadas (fuera del motor de core.retention_policies)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.retencion_dedicada (
    tabla           text PRIMARY KEY,           -- esquema.tabla
    plazo           interval NOT NULL,          -- antigüedad máxima según columna_tiempo
    columna_tiempo  text NOT NULL,              -- columna con la que se mide la antigüedad
    columna_nula    text,                       -- si se indica, la purga solo alcanza filas con esta columna a NULL
    ejecutor        text NOT NULL,              -- quién purga (función, trabajo de pg_cron o worker)
    tarea_log       text,                       -- tarea en core.purgas_log (NULL = el ejecutor no registra)
    motivo          text NOT NULL,
    CONSTRAINT rded_tabla_chk        CHECK (tabla ~ '^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$'),
    CONSTRAINT rded_columna_chk      CHECK (columna_tiempo ~ '^[a-z_][a-z0-9_]*$'),
    CONSTRAINT rded_columna_nula_chk CHECK (columna_nula IS NULL OR columna_nula ~ '^[a-z_][a-z0-9_]*$'),
    CONSTRAINT rded_plazo_chk        CHECK (plazo >= interval '1 hour'),
    CONSTRAINT rded_ejecutor_chk     CHECK (length(btrim(ejecutor)) > 0 AND char_length(ejecutor) <= 500),
    CONSTRAINT rded_tarea_chk        CHECK (tarea_log IS NULL OR char_length(tarea_log) BETWEEN 1 AND 200),
    CONSTRAINT rded_motivo_chk       CHECK (length(btrim(motivo)) > 0 AND char_length(motivo) <= 20000)
);
COMMENT ON TABLE core.retencion_dedicada IS
'KC-DB-10-4: tablas purgadas fuera de core.purgar_retencion (TTL o housekeeping), con su plazo, su ejecutor y la tarea de core.purgas_log que deja constancia. Cuentan como política declarada para V26 y las mide core.reporte_cumplimiento_retencion().';
REVOKE ALL ON core.retencion_dedicada FROM PUBLIC, kallicode_app, kallicode_worker;

INSERT INTO core.retencion_dedicada (tabla, plazo, columna_tiempo, columna_nula, ejecutor, tarea_log, motivo) VALUES
    ('core.idempotency_keys', interval '48 hours', 'creado_en', NULL,
     'core.purgar_idempotency_keys(): pg_cron idempotencia_purga (cada hora) y housekeeping como respaldo',
     'idempotency_keys',
     'TTL de las claves de idempotencia (KC-DB-10-3): pasado el plazo una repetición de la petición ya no se deduplica.'),
    ('core.refresh_tokens', interval '7 days', 'expira_en', NULL,
     'housekeeping (apps/workers/src/kallicode_workers/housekeeping.py)',
     NULL,
     'Sesiones expiradas hace más de 7 días (§9.3). La IP y el navegador se anonimizan antes, a los 30 días de emitidas (core.minimizar_pii, KC-DB-10-3).'),
    ('core.ticket_attachments', interval '48 hours', 'creado_en', 'ticket_id',
     'housekeeping (apps/workers/src/kallicode_workers/housekeeping.py)',
     NULL,
     'Adjuntos huérfanos (sin ticket) de más de 48 h (§9.3, KC-BE-05-5). Los asociados a un ticket se conservan con él y se eliminan con su tenant.')
ON CONFLICT (tabla) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. Universo del informe y de V26: toda tabla (no partición) de core, audit, vec y cm
--    Se excluyen las particiones (las gobierna su tabla madre) y las tablas de extensiones.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.tablas_retencion()
RETURNS TABLE (tabla text, relid oid, particionada boolean)
LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT n.nspname || '.' || c.relname, c.oid, c.relkind = 'p'
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind IN ('r', 'p')
       AND NOT c.relispartition
       AND n.nspname IN ('core', 'audit', 'vec', 'cm')
       AND NOT EXISTS (SELECT 1 FROM pg_depend d
                        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
$f$;
COMMENT ON FUNCTION core.tablas_retencion() IS
'KC-DB-10-4: tablas de core, audit, vec y cm sujetas a política de retención (sin particiones ni tablas de extensiones). Universo de V26 y de core.reporte_cumplimiento_retencion().';
REVOKE ALL ON FUNCTION core.tablas_retencion() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.tablas_retencion() TO kallicode_admin;

-- ---------------------------------------------------------------------------
-- 3. Declarar 'conservar' para las tablas sin política, con su motivo
--    Se respeta cualquier política de plataforma existente (activa o desactivada a propósito) y las purgas
--    dedicadas. Una tabla cuyo nombre no cumple retention_policies_entidad_chk se queda fuera y V26 la señala.
-- ---------------------------------------------------------------------------
INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, motivo)
SELECT NULL, t.tabla, NULL, 'conservar',
       coalesce(m.motivo,
                CASE WHEN EXISTS (SELECT 1 FROM pg_attribute a
                                   WHERE a.attrelid = t.relid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
                     THEN 'Dato operativo del tenant: se conserva mientras el tenant existe y se elimina con él '
                          '(core.suprimir_tenant, 048). Sin plazo propio: fijarlo es una decisión de negocio '
                          '(declarado en KC-DB-10-4).'
                     ELSE 'Catálogo o registro de plataforma, sin datos de un tenant: se conserva mientras esté en '
                          'uso (declarado en KC-DB-10-4).'
                END)
  FROM core.tablas_retencion() t
  LEFT JOIN (VALUES
      ('core.schema_migrations',
       'Ledger de migraciones aplicadas con su sha256: el runner lo verifica en cada despliegue; no se purga.'),
      ('core.retention_policies',
       'Declaración de las políticas de retención: es la evidencia de gobierno que muestra este informe.'),
      ('core.retention_purgas',
       'Registro de lo que hizo cada purga (KC-DB-10-1): evidencia de la «última purga» del informe de cumplimiento.'),
      ('core.purgas_log',
       'Registro de las ejecuciones de TTL y minimización de PII (KC-DB-10-3): evidencia del informe de cumplimiento.'),
      ('core.pii_ventanas',
       'Configuración de las ventanas de minimización de PII (KC-DB-10-3).'),
      ('core.retencion_dedicada',
       'Declaración de las purgas dedicadas (KC-DB-10-4): evidencia de gobierno.'),
      ('audit.worm_exports',
       'Comprobantes de export WORM (append-only, KC-DB-10-2): sin ellos no se puede verificar una partición de auditoría descartada.'),
      ('audit.chain_anchors',
       'Anclas de la cadena de auditoría (KC-DB-10-2): sin ellas la cadena de un tenant no se verifica tras un descarte.'),
      ('audit.chain_heads',
       'Cabeza de la cadena de hashes de cada tenant: se elimina solo con su tenant (core.suprimir_tenant).'),
      ('core.gate_signatures',
       'Firmas de gates: evidencia probatoria append-only (025). D14 exige al menos un año; se conserva sin plazo máximo y se elimina con su tenant.'),
      ('core.llm_steps',
       'Pasos LLM: evidencia probatoria append-only (025). D14 exige al menos un año; se conserva sin plazo máximo y se elimina con su tenant.'),
      ('core.qa_runs',
       'Ejecuciones de QA: evidencia probatoria append-only (025). D14 exige al menos un año; se conserva sin plazo máximo y se elimina con su tenant.'),
      ('core.qa_results',
       'Resultados de QA: evidencia probatoria append-only (025). D14 exige al menos un año; se conserva sin plazo máximo y se elimina con su tenant.'),
      ('core.usage_loc',
       'Consumo de LOC: evidencia de facturación append-only (025); se conserva y se elimina con su tenant.')
  ) AS m(tabla, motivo) ON m.tabla = t.tabla
 WHERE t.tabla ~ '^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$'
   AND NOT EXISTS (SELECT 1 FROM core.retention_policies p
                    WHERE p.tenant_id IS NULL AND p.entity_name = t.tabla)
   AND NOT EXISTS (SELECT 1 FROM core.retencion_dedicada d WHERE d.tabla = t.tabla)
ON CONFLICT (entity_name) WHERE tenant_id IS NULL DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. V26 ampliado a todas las tablas del universo
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.contar_tablas_sin_retencion() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT count(*)
      FROM core.tablas_retencion() t
     WHERE NOT EXISTS (SELECT 1 FROM core.retention_policies p
                        WHERE p.tenant_id IS NULL AND p.is_active AND p.entity_name = t.tabla)
       AND NOT EXISTS (SELECT 1 FROM core.retencion_dedicada d WHERE d.tabla = t.tabla)
$f$;
COMMENT ON FUNCTION core.contar_tablas_sin_retencion() IS
'KC-DB-09-6 / KC-DB-10-1 / KC-DB-10-4: número de tablas de core, audit, vec y cm (core.tablas_retencion) sin política de plataforma activa en core.retention_policies (conservar si no se purgan) ni purga dedicada en core.retencion_dedicada. La usa V26 de core.validar_integridad().';
REVOKE ALL ON FUNCTION core.contar_tablas_sin_retencion() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.contar_tablas_sin_retencion() TO kallicode_app, kallicode_worker, kallicode_admin;

-- ---------------------------------------------------------------------------
-- 5. core.validar_integridad(): V01-V26 idénticos a 055; solo cambia la descripción de V26
-- ---------------------------------------------------------------------------
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

-- V10 · Agregado de cuota: usage_cycles debe cuadrar con la suma real de usage_loc.
--       La suma real puede ser negativa (créditos mayores que lo consumido);
--       el piso en cero se aplica solo al total del ciclo (KC-DB-05-3, DB-F07).
chequeo := 'V10_cuota_cuadra';
descripcion := 'Ciclos cuyo agregado no cuadra con la suma de usage_loc (netas reales, piso en cero solo en el total)';
SELECT count(*) INTO problemas FROM (
    SELECT c.tenant_id, c.ciclo
      FROM core.usage_cycles c
      LEFT JOIN core.usage_loc l ON l.tenant_id = c.tenant_id AND l.ciclo = c.ciclo
     GROUP BY c.tenant_id, c.ciclo, c.loc_netas, c.loc_consumidas
    HAVING c.loc_netas <> coalesce(sum(l.netas), 0)
        OR c.loc_consumidas <> greatest(coalesce(sum(l.netas), 0), 0)
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

-- V16 · Cadena de auditoría íntegra (recorre todos los tenants visibles):
--       sello propio, encadenamiento (sello_previo = sello del eslabón anterior)
--       y recuento contra chain_heads (KC-DB-05-1, DB-F04).
chequeo := 'V16_cadena_auditoria';
descripcion := 'Tenants con cadena de auditoría rota: sello alterado, eslabón perdido o eventos faltantes (ver audit.verify_chain)';
SELECT count(*) INTO problemas FROM (
    SELECT h.tenant_id
      FROM audit.chain_heads h,
           LATERAL audit.verify_chain(h.tenant_id) v
     WHERE NOT v.integra
) q;
RETURN NEXT;

-- V17 · Cabeza de cadena consistente con los eventos: mismo último sello y
--       mismo número de eventos (un hueco por borrado delata la diferencia),
--       y ningún tenant con eventos sin cabeza (KC-DB-05-1, DB-F04).
chequeo := 'V17_cabeza_cadena';
descripcion := 'chain_heads que no cuadra con los eventos: último sello distinto, eventos de menos o de más, o eventos sin cabeza';
SELECT count(*) INTO problemas FROM (
    SELECT h.tenant_id
      FROM audit.chain_heads h
      LEFT JOIN LATERAL (
          SELECT count(*) AS n,
                 (array_agg(e.sello ORDER BY e.id DESC))[1] AS ultimo
            FROM audit.audit_events e
           WHERE e.tenant_id = h.tenant_id) ev ON true
      LEFT JOIN audit.chain_anchors an ON an.tenant_id = h.tenant_id
     WHERE ev.n <> h.eventos - coalesce(an.eventos_descartados, 0)
        OR ev.ultimo IS DISTINCT FROM
           (CASE WHEN h.eventos - coalesce(an.eventos_descartados, 0) > 0 THEN h.ultimo_sello END)
    UNION
    SELECT e.tenant_id
      FROM audit.audit_events e
     WHERE NOT EXISTS (SELECT 1 FROM audit.chain_heads h WHERE h.tenant_id = e.tenant_id)
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

-- V22 · Ningún ciclo de cuota puede tener loc_consumidas negativo (KC-DB-05-3, DB-F07).
chequeo := 'V22_consumo_no_negativo';
descripcion := 'Ciclos de cuota con loc_consumidas negativo';
SELECT count(*) INTO problemas FROM core.usage_cycles WHERE loc_consumidas < 0;
RETURN NEXT;

-- V23 · Renovación vencida: suscripciones activas cuyo renueva_el pasó (antes de hoy)
--       sin que core.renovar_ciclo abriera el ciclo nuevo (KC-DB-09-1, DB-F08).
--       Se compara con < current_date para no alertar por las renovaciones del día
--       que el worker horario aún no ha procesado.
chequeo := 'V23_renovacion_vencida';
descripcion := 'Suscripciones activas con renueva_el vencido sin ciclo nuevo (ver core.renovar_ciclo)';
SELECT count(*) INTO problemas
  FROM core.subscriptions s
 WHERE s.finaliza_el IS NULL
   AND s.deleted_at IS NULL
   AND s.estado IN ('activa','periodo_gracia')
   AND s.renueva_el < current_date;
RETURN NEXT;

-- V24 · Hallazgos de seguridad bloqueantes sin adjudicar (estado 'abierto': ni corregidos ni descartados con
--       justificación) desde hace más de 48 h (KC-DB-09-6). V09 ya impide pasar a producción con bloqueantes;
--       éste detecta los que llevan días esperando a alguien.
chequeo := 'V24_bloqueantes_sin_adjudicar';
descripcion := 'Hallazgos de seguridad bloqueantes abiertos hace más de 48 h sin corregir ni descartar';
SELECT count(*) INTO problemas
  FROM core.security_findings f
 WHERE f.bloquea AND f.estado = 'abierto'
   AND f.creado_en < now() - interval '48 hours';
RETURN NEXT;

-- V25 · Organizaciones borradas lógicamente (deleted_at, o eliminado_en legado) con actividad POSTERIOR al
--       borrado: tickets, trabajos o consumo de LOC creados después. Una organización borrada no debe operar;
--       si hay filas nuevas, el borrado no se aplicó en algún camino de escritura. La auditoría no cuenta
--       (core.suprimir_tenant registra su propio acto después de marcar el borrado). Cuenta organizaciones.
chequeo := 'V25_org_borrada_con_actividad';
descripcion := 'Organizaciones borradas lógicamente con tickets, trabajos o consumo creados después del borrado';
SELECT count(*) INTO problemas
  FROM core.organizations o
 WHERE coalesce(o.deleted_at, o.eliminado_en) IS NOT NULL
   AND (EXISTS (SELECT 1 FROM core.tickets t
                 WHERE t.tenant_id = o.id AND t.creado_en > coalesce(o.deleted_at, o.eliminado_en))
     OR EXISTS (SELECT 1 FROM core.jobs j
                 WHERE j.tenant_id = o.id AND j.creado_en > coalesce(o.deleted_at, o.eliminado_en))
     OR EXISTS (SELECT 1 FROM core.usage_loc u
                 WHERE u.tenant_id = o.id AND u.creado_en > coalesce(o.deleted_at, o.eliminado_en)));
RETURN NEXT;

-- V26 · Tablas sin política de retención declarada (KC-DB-09-6; desde KC-DB-10-4, todas las tablas de core, audit,
--       vec y cm, no solo las particionadas): ni una política de plataforma activa en core.retention_policies
--       (que incluye «conservar» con su motivo) ni una purga dedicada en core.retencion_dedicada. Una tabla que
--       crece sin que nadie haya decidido cuánto conservarla es deuda.
chequeo := 'V26_tabla_sin_retencion';
descripcion := 'Tablas de core, audit, vec y cm sin política de retención declarada (core.retention_policies activa o core.retencion_dedicada)';
SELECT core.contar_tablas_sin_retencion() INTO problemas;
RETURN NEXT;

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría (sello, encadenamiento y recuento, KC-DB-05-1; descuentan los eventos descartados tras un export WORM, anclados en audit.chain_anchors, KC-DB-10-2); V10/V12 la validación comercial (V10 contra la suma real de usage_loc, V22 sin consumo negativo, KC-DB-05-3); V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0); V23 las renovaciones por aniversario vencidas (KC-DB-09-1); V24 los hallazgos bloqueantes abiertos más de 48 h; V25 las organizaciones borradas con actividad posterior; V26 las tablas sin retención declarada (KC-DB-09-6; todas las tablas de core, audit, vec y cm desde KC-DB-10-4, que publica el informe core.reporte_cumplimiento_retencion). 26 chequeos en total.';

-- ---------------------------------------------------------------------------
-- 6. El informe de cumplimiento
--    Una fila por tabla del universo, por entidad declarada en core.retention_policies o core.retencion_dedicada
--    (aunque la tabla no exista: estado 'inexistente') y por política activa de tenant. Solo lectura.
--    SECURITY DEFINER (kallicode_admin, BYPASSRLS): mide todos los tenants y lee los registros de purga, que la
--    aplicación no ve. Los identificadores del SQL dinámico salen del catálogo (regclass, pg_attribute), nunca de texto
--    libre: columna_tiempo se usa solo si existe en la tabla y es de tipo fecha.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.reporte_cumplimiento_retencion(
    p_ahora      timestamptz DEFAULT now(),
    p_tolerancia interval    DEFAULT interval '7 days'
) RETURNS TABLE (
    tabla               text,
    tenant              text,          -- NULL = política de plataforma
    particionada        boolean,
    origen              text,          -- retention_policies | purga_dedicada | sin_politica
    accion              text,          -- drop | detach | archive | conservar | purga_dedicada
    retencion           interval,      -- plazo declarado (NULL en conservar)
    columna_tiempo      text,
    dato_mas_antiguo    timestamptz,
    referencia          timestamptz,   -- fecha con la que se mide: límite superior de su partición o la propia fila
    antiguedad          interval,
    exceso              interval,      -- antiguedad - retencion (positivo = fuera de plazo)
    politicas_tenant    integer,
    ultima_purga        timestamptz,
    filas_ultima_purga  bigint,
    ultima_omision      text,          -- motivo, si lo último que hizo el motor fue omitir un descarte
    particiones         integer,
    particiones_worm    integer,       -- audit.audit_events: particiones con comprobante WORM
    ultimo_export_worm  timestamptz,
    estado              text,          -- cumple | en_tolerancia | excede | conservar | sin_politica | no_medible | inexistente | ignorada
    desviacion          boolean,
    alerta              boolean,       -- dato más antiguo fuera de plazo en más de p_tolerancia
    justificacion       text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
SET timezone = 'UTC' AS $f$
#variable_conflict use_column
DECLARE
    r        record;
    tp       record;
    reg      regclass;
    hay_pol  boolean;
    hay_ded  boolean;
    pol      record;
    ded      record;
    col      text;
    nula     text;
    es_rango boolean;
    v_min    timestamptz;
    v_ref    timestamptz;
    b        text[];
    ult      record;
BEGIN
    IF p_tolerancia IS NULL OR p_tolerancia < interval '0' THEN
        RAISE EXCEPTION 'core.reporte_cumplimiento_retencion: tolerancia inválida (%)', p_tolerancia
            USING ERRCODE = '22023';
    END IF;

    FOR r IN
        SELECT x.t AS nombre FROM (
            SELECT tr.tabla AS t FROM core.tablas_retencion() tr
            UNION SELECT p.entity_name FROM core.retention_policies p
            UNION SELECT d.tabla FROM core.retencion_dedicada d) x
         ORDER BY 1
    LOOP
        tabla := r.nombre; tenant := NULL; particionada := NULL; origen := NULL; accion := NULL;
        retencion := NULL; columna_tiempo := NULL; dato_mas_antiguo := NULL; referencia := NULL;
        antiguedad := NULL; exceso := NULL; politicas_tenant := 0; ultima_purga := NULL;
        filas_ultima_purga := NULL; ultima_omision := NULL; particiones := NULL; particiones_worm := NULL;
        ultimo_export_worm := NULL; justificacion := NULL;
        col := NULL; nula := NULL; v_min := NULL; v_ref := NULL;

        SELECT * INTO pol FROM core.retention_policies p WHERE p.tenant_id IS NULL AND p.entity_name = r.nombre;
        hay_pol := FOUND;
        SELECT * INTO ded FROM core.retencion_dedicada d WHERE d.tabla = r.nombre;
        hay_ded := FOUND;
        SELECT count(*)::int INTO politicas_tenant
          FROM core.retention_policies p WHERE p.tenant_id IS NOT NULL AND p.is_active AND p.entity_name = r.nombre;

        reg := to_regclass(r.nombre);
        IF reg IS NULL THEN
            origen := CASE WHEN hay_pol THEN 'retention_policies' WHEN hay_ded THEN 'purga_dedicada' END;
            accion := CASE WHEN hay_pol THEN pol.action WHEN hay_ded THEN 'purga_dedicada' END;
            estado := 'inexistente'; desviacion := true; alerta := false;
            justificacion := 'la política declara una tabla que no existe';
            RETURN NEXT;
            CONTINUE;
        END IF;

        particionada := (SELECT c.relkind = 'p' FROM pg_class c WHERE c.oid = reg);
        es_rango := EXISTS (SELECT 1 FROM pg_partitioned_table pt WHERE pt.partrelid = reg AND pt.partstrat = 'r');
        IF particionada THEN
            particiones := (SELECT count(*)::int FROM pg_inherits i WHERE i.inhparent = reg);
        END IF;

        -- política aplicable: la de plataforma activa; si no, la purga dedicada
        IF hay_pol AND pol.is_active THEN
            origen := 'retention_policies';
            accion := pol.action;
            retencion := CASE WHEN pol.retention_days IS NOT NULL THEN make_interval(days => pol.retention_days) END;
            col := pol.columna_tiempo;
            justificacion := pol.motivo;
        ELSIF hay_ded THEN
            origen := 'purga_dedicada';
            accion := 'purga_dedicada';
            retencion := ded.plazo;
            col := ded.columna_tiempo;
            nula := ded.columna_nula;
            justificacion := ded.motivo || ' Ejecutor: ' || ded.ejecutor || '.';
        ELSE
            origen := 'sin_politica';
            justificacion := CASE WHEN hay_pol
                                  THEN 'la política de plataforma está desactivada: no cuenta como declarada'
                                  ELSE 'sin política en core.retention_policies ni purga dedicada en core.retencion_dedicada' END;
        END IF;
        IF es_rango THEN
            col := (regexp_match(pg_get_partkeydef(reg), '^RANGE \(([a-z_][a-z0-9_]*)\)$'))[1];
        END IF;
        -- la columna solo vale si existe y es de tipo fecha
        IF col IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = reg AND a.attname = col AND NOT a.attisdropped
                  AND a.atttypid IN ('date'::regtype, 'timestamp'::regtype, 'timestamptz'::regtype)) THEN
            col := NULL;
        END IF;
        IF nula IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM pg_attribute a WHERE a.attrelid = reg AND a.attname = nula AND NOT a.attisdropped) THEN
            col := NULL;   -- el filtro de la purga dedicada no se puede aplicar: no se mide
        END IF;
        columna_tiempo := col;

        -- dato más antiguo y fecha de referencia
        IF col IS NOT NULL THEN
            BEGIN
                EXECUTE format('SELECT min(%I)::timestamptz FROM %s%s', col, reg,
                               CASE WHEN nula IS NOT NULL THEN format(' WHERE %I IS NULL', nula) ELSE '' END)
                   INTO v_min;
            EXCEPTION WHEN insufficient_privilege THEN
                -- una tabla que kallicode_admin no puede leer (no la creó una migración): no se mide
                col := NULL;
                columna_tiempo := NULL;
                justificacion := coalesce(justificacion || ' · ', '') || 'kallicode_admin no puede leer la tabla';
            END;
        END IF;
        IF col IS NOT NULL THEN
            dato_mas_antiguo := v_min;
            v_ref := v_min;
            IF es_rango AND v_min IS NOT NULL THEN
                FOR b IN
                    SELECT regexp_match(pg_get_expr(c.relpartbound, c.oid), 'FROM \(''([^'']+)''\) TO \(''([^'']+)''\)')
                      FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
                     WHERE i.inhparent = reg
                LOOP
                    IF b IS NOT NULL AND b[1]::timestamptz <= v_min AND v_min < b[2]::timestamptz THEN
                        v_ref := b[2]::timestamptz;
                        EXIT;
                    END IF;
                END LOOP;
            END IF;
            referencia := v_ref;
            antiguedad := CASE WHEN v_ref IS NOT NULL THEN greatest(p_ahora - v_ref, interval '0') END;
            exceso := CASE WHEN antiguedad IS NOT NULL AND retencion IS NOT NULL THEN antiguedad - retencion END;
        END IF;

        -- última purga y, si lo último fue omitir un descarte, por qué
        IF origen = 'purga_dedicada' THEN
            IF ded.tarea_log IS NOT NULL THEN
                SELECT l.ejecutada_en, l.filas INTO ultima_purga, filas_ultima_purga
                  FROM core.purgas_log l WHERE l.tarea = ded.tarea_log ORDER BY l.id DESC LIMIT 1;
            END IF;
        ELSE
            SELECT rp.ejecutado_en, rp.filas INTO ultima_purga, filas_ultima_purga
              FROM core.retention_purgas rp
             WHERE rp.entidad = r.nombre AND rp.tenant_id IS NULL
               AND rp.accion IN ('eliminada', 'desacoplada', 'archivada', 'borradas')
             ORDER BY rp.id DESC LIMIT 1;
            SELECT rp.accion, rp.motivo INTO ult
              FROM core.retention_purgas rp WHERE rp.entidad = r.nombre AND rp.tenant_id IS NULL
             ORDER BY rp.id DESC LIMIT 1;
            IF FOUND AND ult.accion = 'omitida' THEN
                ultima_omision := ult.motivo;
            END IF;
        END IF;

        -- particiones de auditoría con comprobante WORM
        IF r.nombre = 'audit.audit_events' AND to_regclass('audit.worm_exports') IS NOT NULL THEN
            SELECT (count(*) FILTER (WHERE v.ultimo_export_id IS NOT NULL))::int INTO particiones_worm
              FROM audit.v_particiones_worm v;
            SELECT max(w.verificado_en) INTO ultimo_export_worm FROM audit.worm_exports w;
        END IF;

        -- estado
        IF origen = 'sin_politica' THEN
            estado := 'sin_politica';
        ELSIF accion = 'conservar' THEN
            estado := 'conservar';
        ELSIF col IS NULL THEN
            estado := 'no_medible';
            justificacion := coalesce(justificacion || ' · ', '')
                             || 'no hay una columna de fecha válida con la que medir la antigüedad';
        ELSIF antiguedad IS NULL OR antiguedad <= retencion THEN
            estado := 'cumple';
        ELSIF antiguedad <= retencion + p_tolerancia THEN
            estado := 'en_tolerancia';
        ELSE
            estado := 'excede';
        END IF;
        justificacion := coalesce(justificacion, 'política declarada en core.retention_policies sin motivo');
        desviacion := estado IN ('excede', 'sin_politica', 'no_medible');
        alerta := estado = 'excede';
        RETURN NEXT;

        -- políticas por tenant de esta entidad: solo acortan el plazo de la de plataforma
        FOR tp IN
            SELECT p.tenant_id, p.retention_days, p.motivo
              FROM core.retention_policies p
             WHERE p.tenant_id IS NOT NULL AND p.is_active AND p.entity_name = r.nombre
             ORDER BY p.tenant_id
        LOOP
            tenant := tp.tenant_id; origen := 'retention_policies'; accion := 'drop';
            retencion := make_interval(days => tp.retention_days);
            dato_mas_antiguo := NULL; referencia := NULL; antiguedad := NULL; exceso := NULL;
            ultima_purga := NULL; filas_ultima_purga := NULL; ultima_omision := NULL;
            particiones_worm := NULL; ultimo_export_worm := NULL; politicas_tenant := NULL;
            justificacion := tp.motivo;
            IF NOT (hay_pol AND pol.is_active AND pol.action <> 'conservar') THEN
                estado := 'ignorada';
                justificacion := 'la plataforma no purga esta entidad: la política del tenant no se aplica';
            ELSIF tp.retention_days >= pol.retention_days THEN
                estado := 'ignorada';
                justificacion := 'una política por tenant no puede alargar el plazo de la de plataforma';
            ELSIF col IS NULL OR NOT EXISTS (SELECT 1 FROM pg_attribute a
                                              WHERE a.attrelid = reg AND a.attname = 'tenant_id' AND NOT a.attisdropped) THEN
                estado := 'no_medible';
                justificacion := 'la entidad no tiene columna de fecha válida o tenant_id';
            ELSE
                EXECUTE format('SELECT min(%I)::timestamptz FROM %s WHERE tenant_id = $1', col, reg)
                   INTO v_min USING tp.tenant_id;
                dato_mas_antiguo := v_min; referencia := v_min;   -- el borrado por tenant es por fila
                antiguedad := CASE WHEN v_min IS NOT NULL THEN greatest(p_ahora - v_min, interval '0') END;
                exceso := CASE WHEN antiguedad IS NOT NULL THEN antiguedad - retencion END;
                SELECT rp.ejecutado_en, rp.filas INTO ultima_purga, filas_ultima_purga
                  FROM core.retention_purgas rp
                 WHERE rp.entidad = r.nombre AND rp.tenant_id = tp.tenant_id AND rp.accion = 'borradas'
                 ORDER BY rp.id DESC LIMIT 1;
                estado := CASE WHEN antiguedad IS NULL OR antiguedad <= retencion THEN 'cumple'
                               WHEN antiguedad <= retencion + p_tolerancia THEN 'en_tolerancia'
                               ELSE 'excede' END;
            END IF;
            desviacion := estado IN ('excede', 'no_medible');
            alerta := estado = 'excede';
            RETURN NEXT;
        END LOOP;
    END LOOP;
END
$f$;
COMMENT ON FUNCTION core.reporte_cumplimiento_retencion(timestamptz, interval) IS
'KC-DB-10-4: informe de cumplimiento de retención. Una fila por tabla de core/audit/vec/cm, por entidad declarada en core.retention_policies o core.retencion_dedicada y por política activa de tenant: política, dato más antiguo, antigüedad (en particionadas por rango, desde el límite superior de su partición), última purga, particiones con comprobante WORM, estado y justificación. alerta = el dato más antiguo supera la retención en más de p_tolerancia (7 días). Solo lectura.';
REVOKE ALL ON FUNCTION core.reporte_cumplimiento_retencion(timestamptz, interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.reporte_cumplimiento_retencion(timestamptz, interval) TO kallicode_admin;

-- ---------------------------------------------------------------------------
-- 7. Alertas: el dato más antiguo supera la retención declarada en más de la tolerancia
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.alertas_retencion(
    p_ahora      timestamptz DEFAULT now(),
    p_tolerancia interval    DEFAULT interval '7 days'
) RETURNS TABLE (tabla text, tenant text, retencion interval, referencia timestamptz, exceso interval)
LANGUAGE sql STABLE
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT r.tabla, r.tenant, r.retencion, r.referencia, r.exceso
      FROM core.reporte_cumplimiento_retencion(p_ahora, p_tolerancia) r
     WHERE r.alerta
     ORDER BY r.exceso DESC, r.tabla, r.tenant
$f$;
COMMENT ON FUNCTION core.alertas_retencion(timestamptz, interval) IS
'KC-DB-10-4: filas de core.reporte_cumplimiento_retencion() con alerta (dato más antiguo fuera de plazo en más de p_tolerancia). Las consultan housekeeping y el CI: vacía = sin alertas.';
REVOKE ALL ON FUNCTION core.alertas_retencion(timestamptz, interval) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.alertas_retencion(timestamptz, interval) TO kallicode_admin;
