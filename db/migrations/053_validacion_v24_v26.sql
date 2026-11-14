-- ============================================================================
-- KALLICODE · 053_validacion_v24_v26.sql
-- KC-DB-09-6 (3 SP) — Chequeos V22-V26 de las invariantes nuevas en core.validar_integridad().
--
-- Cada corrección de los sprints 5 a 9 introdujo una invariante que hay que vigilar en producción y no solo
-- probar una vez. De las cinco condiciones del ticket, dos ya las añadieron sus tickets y se conservan tal cual
-- (la función pasa de 23 a 26 chequeos):
--   V22_consumo_no_negativo        ciclos con consumo negativo          (028_cuota_delta, KC-DB-05-3)
--   V23_renovacion_vencida         ciclos vencidos sin renovar          (051_renovacion_ciclo, KC-DB-09-1)
-- y esta migración añade las otras tres:
--   V24_bloqueantes_sin_adjudicar  hallazgos bloqueantes abiertos hace más de 48 h
--   V25_org_borrada_con_actividad  organizaciones borradas lógicamente con actividad posterior
--   V26_tabla_sin_retencion        tablas particionadas sin política de retención declarada
--
-- V26 necesita saber qué es «declarada»: hay dos fuentes. core.retencion_particiones (036) fija una ventana en la
-- que se eliminan particiones; core.retencion_declaraciones (nueva, aquí) declara las demás: 'conservar' (no se
-- borra, con su justificación) o 'por_tenant' (se elimina con su tenant). Se siembran las particionadas que
-- no tienen ventana. NO inventan una política: recogen la vigente (auditoría sin borrado, 033; consumo y
-- transiciones sin retención hasta que se fije en core.retention_policies, 035; colecciones vectoriales que se
-- suprimen con su tenant, 048). Pasar una a 'ventana' es una decisión de negocio posterior.
--
-- Nota: el ticket nombra db/05_validacion.sql, db/migrations/038_validacion_v22_v26.sql y
-- tests/test_validacion_ampliada.py. 05_validacion.sql (V01-V20, esquema base) no se edita; la 038 está ocupada,
-- la siguiente libre es la 053; la prueba vive en packages/core/tests/integracion/.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

-- 1. Declaraciones de retención de las tablas que no tienen ventana.
CREATE TABLE IF NOT EXISTS core.retencion_declaraciones (
    tabla          text PRIMARY KEY,                  -- esquema.tabla (particionada)
    politica       text NOT NULL,
    justificacion  text NOT NULL,
    declarada_en   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rd_politica_chk          CHECK (politica IN ('conservar', 'por_tenant')),
    CONSTRAINT rd_justificacion_chk     CHECK (length(btrim(justificacion)) > 0),
    CONSTRAINT rd_justificacion_len_chk CHECK (char_length(justificacion) <= 20000)
);
COMMENT ON TABLE core.retencion_declaraciones IS
'KC-DB-09-6: tablas particionadas sin ventana de eliminación en core.retencion_particiones, con su política declarada (conservar | por_tenant) y por qué. Las lee V26 de core.validar_integridad().';
REVOKE ALL ON core.retencion_declaraciones FROM PUBLIC, kallicode_app, kallicode_worker;

INSERT INTO core.retencion_declaraciones (tabla, politica, justificacion) VALUES
    ('audit.audit_events',        'conservar',  'La auditoría no se borra (033): cadena de hashes inmutable; las particiones antiguas se conservan.'),
    ('core.usage_tokens',         'conservar',  'Consumo de tokens sin ventana hasta que se fije en core.retention_policies (035); mientras tanto se conserva.'),
    ('core.job_transitions',      'conservar',  'Historial de transiciones de trabajos sin ventana hasta que se fije en core.retention_policies; mientras tanto se conserva.'),
    ('vec.definition_embeddings', 'por_tenant', 'Particionada por tenant (037): se elimina con su tenant mediante core.suprimir_tenant (048).'),
    ('vec.doc_chunks',            'por_tenant', 'Particionada por tenant (037): se elimina con su tenant mediante core.suprimir_tenant (048).')
ON CONFLICT (tabla) DO NOTHING;

-- Cuenta las tablas particionadas sin política declarada. SECURITY DEFINER porque core.validar_integridad() la
-- ejecuta también la aplicación (kallicode_app), que no puede leer las tablas de retención; solo devuelve un número.
CREATE OR REPLACE FUNCTION core.contar_tablas_sin_retencion() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $f$
    SELECT count(*)
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind = 'p'
       AND n.nspname IN ('core', 'audit', 'vec', 'cm')
       AND NOT EXISTS (SELECT 1 FROM core.retencion_particiones rp
                        WHERE rp.tabla = n.nspname || '.' || c.relname)
       AND NOT EXISTS (SELECT 1 FROM core.retencion_declaraciones d
                        WHERE d.tabla = n.nspname || '.' || c.relname)
$f$;
COMMENT ON FUNCTION core.contar_tablas_sin_retencion() IS
'KC-DB-09-6: número de tablas particionadas (core, audit, vec, cm) sin ventana en core.retencion_particiones ni declaración en core.retencion_declaraciones. La usa V26 de core.validar_integridad().';
REVOKE ALL ON FUNCTION core.contar_tablas_sin_retencion() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.contar_tablas_sin_retencion() TO kallicode_app, kallicode_worker, kallicode_admin;

-- 2. core.validar_integridad(): V01-V23 idénticos a 051_renovacion_ciclo.sql, más V24-V26.
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
     WHERE ev.n <> h.eventos
        OR ev.ultimo IS DISTINCT FROM (CASE WHEN h.eventos > 0 THEN h.ultimo_sello END)
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

-- V26 · Tablas particionadas sin política de retención declarada (KC-DB-09-6): ni una ventana en
--       core.retencion_particiones ni una declaración en core.retencion_declaraciones (que incluye «conservar»
--       con su justificación). Una tabla que crece sin que nadie haya decidido cuánto conservarla es deuda.
chequeo := 'V26_tabla_sin_retencion';
descripcion := 'Tablas particionadas sin política de retención declarada (core.retencion_particiones o core.retencion_declaraciones)';
SELECT core.contar_tablas_sin_retencion() INTO problemas;
RETURN NEXT;

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría (sello, encadenamiento y recuento, KC-DB-05-1); V10/V12 la validación comercial (V10 contra la suma real de usage_loc, V22 sin consumo negativo, KC-DB-05-3); V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0); V23 las renovaciones por aniversario vencidas (KC-DB-09-1); V24 los hallazgos bloqueantes abiertos más de 48 h; V25 las organizaciones borradas con actividad posterior; V26 las tablas particionadas sin retención declarada (KC-DB-09-6). 26 chequeos en total.';
