-- ============================================================================
-- KALLICODE · 051_renovacion_ciclo.sql
-- KC-DB-09-1 — Renovación por aniversario operativa y desbloqueo de cuota (DB-F08).
--
-- Completa el ciclo comercial de D13. Database aporta las funciones y las
-- garantías; el worker (apps/workers/billing_cycle.py) solo las invoca:
--
--   core.aniversario_en(dia, mes)          aniversario del día `dia` en el mes de
--                                          `mes`, recortado al último día del mes.
--   core.proximo_aniversario(inicia, ref)  primer aniversario de `inicia` > `ref`.
--   core.desbloquear_cola_cuota(tenant)    saca de la cola de cuota tantos tickets
--                                          como líneas libres tenga el plan, por
--                                          prioridad y antigüedad.
--   core.renovar_ciclo(tenant, hoy)        renovación transaccional e idempotente.
--   V23_renovacion_vencida                 chequeo nuevo de validar_integridad().
--
-- Decisiones:
--   * El próximo aniversario se deriva SIEMPRE del día de inicia_el, nunca de
--     renueva_el + 1 mes: con la suma un contrato del 31 derivaba
--     31-ene → 28-feb → 28-mar. Ahora da 31-ene → 28-feb → 31-mar.
--   * La clave de usage_cycles.ciclo sigue siendo 'YYYY-MM' (la del CHECK de
--     usage_loc): el mes en que EMPIEZA el ciclo de aniversario. Como cada mes
--     tiene exactamente un aniversario, la clave es única por ciclo.
--   * Los ciclos anteriores no se tocan (INSERT ... ON CONFLICT DO NOTHING): el
--     saldo histórico se conserva.
--   * Idempotencia y concurrencia: la suscripción vigente se bloquea con
--     FOR UPDATE. Una segunda llamada (o una concurrente, al obtener el candado)
--     ve renueva_el > hoy y no escribe nada: un solo ciclo y un solo evento.
--   * SECURITY INVOKER: las funciones respetan RLS; el worker las llama dentro
--     de sesion_tenant(). Con otro tenant fijado no ven la suscripción y no
--     renuevan nada.
--   * V22 ya lo ocupa 028_cuota_delta.sql (V22_consumo_no_negativo): el chequeo
--     nuevo es V23. 05_validacion.sql no se edita (ya aplicado).
--
-- No es destructiva: solo crea funciones y redefine validar_integridad().
-- ============================================================================

SET lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Aniversarios con normalización de meses cortos.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.aniversario_en(p_dia integer, p_mes date)
RETURNS date
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT make_date(
        extract(year  FROM p_mes)::int,
        extract(month FROM p_mes)::int,
        least(p_dia,
              extract(day FROM (date_trunc('month', p_mes) + interval '1 month - 1 day'))::int))
$$;

COMMENT ON FUNCTION core.aniversario_en(integer, date) IS
'KC-DB-09-1: aniversario del día p_dia dentro del mes de p_mes; si el mes es más corto, el último día del mes (31 → 28 en febrero no bisiesto).';

CREATE OR REPLACE FUNCTION core.proximo_aniversario(p_inicia_el date, p_despues_de date)
RETURNS date
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    v_dia  integer := extract(day FROM p_inicia_el)::int;
    v_cand date;
BEGIN
    v_cand := core.aniversario_en(v_dia, p_despues_de);
    IF v_cand <= p_despues_de THEN
        v_cand := core.aniversario_en(v_dia, (date_trunc('month', p_despues_de)
                                              + interval '1 month')::date);
    END IF;
    RETURN v_cand;
END $$;

COMMENT ON FUNCTION core.proximo_aniversario(date, date) IS
'KC-DB-09-1: primer aniversario mensual de p_inicia_el estrictamente posterior a p_despues_de. Se deriva del día de inicia_el, no del aniversario anterior, para que los meses cortos no arrastren el día.';

-- ---------------------------------------------------------------------------
-- 2. Desbloqueo de la cola de cuota limitado por las líneas del plan.
--
--    Capacidad = plans.lineas − jobs activos del tenant que no están pausados
--    por cuota (NULL = plan sin límite de líneas). Orden: prioridad del
--    pipeline (P1 primero; si el ticket aún no la tiene, la declarada:
--    alta = P2, media = P3, baja = P4), después antigüedad del ticket.
--
--    Un ticket con job pausado se desbloquea moviendo el job a
--    pendiente_asignacion (el trigger T9 lleva el ticket a triage); un ticket
--    sin job se mueve a triage directamente.
--
--    No mira el umbral de cuota: quien la llama decide si la cuota lo permite
--    (renovar_ciclo la invoca con el ciclo recién abierto en 'normal').
--    Toma el mismo candado que renovar_ciclo (la suscripción vigente), así que
--    dos desbloqueos concurrentes del mismo tenant no superan la capacidad.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.desbloquear_cola_cuota(p_tenant_id text)
RETURNS TABLE (tickets_desbloqueados text[], jobs_desbloqueados integer)
LANGUAGE plpgsql AS $$
DECLARE
    v_lineas   integer;
    v_ocupadas integer;
    v_libres   integer;
    v_cand     record;
BEGIN
    tickets_desbloqueados := ARRAY[]::text[];
    jobs_desbloqueados    := 0;

    SELECT p.lineas INTO v_lineas
      FROM core.subscriptions s
      JOIN core.plans p ON p.codigo = s.plan_codigo
     WHERE s.tenant_id = p_tenant_id
       AND s.finaliza_el IS NULL
       AND s.deleted_at IS NULL
       FOR UPDATE OF s;
    IF NOT FOUND THEN
        RETURN NEXT;                                     -- sin suscripción vigente
        RETURN;
    END IF;

    IF v_lineas IS NOT NULL THEN
        SELECT count(*) INTO v_ocupadas
          FROM core.jobs j
         WHERE j.tenant_id = p_tenant_id
           AND j.deleted_at IS NULL
           AND j.estado NOT IN ('produccion','cancelado','cerrado_duplicado','pausado_por_cuota');
        v_libres := greatest(v_lineas - v_ocupadas, 0);
    END IF;                                              -- v_libres NULL = sin límite

    FOR v_cand IN
        SELECT t.id AS ticket_id, coalesce(t.numero, t.id) AS numero, j.id AS job_id
          FROM core.tickets t
          LEFT JOIN core.jobs j
                 ON j.ticket_id = t.id
                AND j.estado = 'pausado_por_cuota'
                AND j.deleted_at IS NULL
         WHERE t.tenant_id = p_tenant_id
           AND t.etapa = 'en_cola_por_cuota'
           AND t.deleted_at IS NULL
         ORDER BY coalesce(
                      CASE coalesce(j.prioridad, t.prioridad_pipeline)
                          WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4
                      END,
                      CASE t.prioridad WHEN 'alta' THEN 2 WHEN 'media' THEN 3 ELSE 4 END),
                  t.creado_en, t.id
         LIMIT v_libres
           FOR UPDATE OF t
    LOOP
        IF v_cand.job_id IS NOT NULL THEN
            UPDATE core.jobs
               SET estado = 'pendiente_asignacion', actualizado_en = now()
             WHERE id = v_cand.job_id;
            jobs_desbloqueados := jobs_desbloqueados + 1;
        ELSE
            UPDATE core.tickets
               SET etapa = 'triage', actualizado_en = now()
             WHERE id = v_cand.ticket_id;
        END IF;
        tickets_desbloqueados := tickets_desbloqueados || v_cand.numero;
    END LOOP;

    RETURN NEXT;
END $$;

COMMENT ON FUNCTION core.desbloquear_cola_cuota(text) IS
'KC-DB-09-1: saca de en_cola_por_cuota / pausado_por_cuota tantos tickets como líneas libres tenga el plan, por prioridad (P1..P4) y antigüedad. Devuelve los números de ticket desbloqueados y cuántos jobs se reanudaron. No comprueba la cuota: lo hace quien la invoca.';

-- ---------------------------------------------------------------------------
-- 3. Renovación transaccional del ciclo por aniversario.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.renovar_ciclo(p_tenant_id text, p_hoy date DEFAULT current_date)
RETURNS TABLE (renovado              boolean,
               ciclo_nuevo           text,
               proxima_renovacion    date,
               tickets_desbloqueados text[],
               jobs_desbloqueados    integer)
LANGUAGE plpgsql AS $$
DECLARE
    v_sub_id       text;
    v_inicia_el    date;
    v_renueva_ant  date;
    v_inicio_ciclo date;
    v_actor        text := coalesce(nullif(current_setting('app.actor', true), ''), 'sistema');
BEGIN
    renovado              := false;
    tickets_desbloqueados := ARRAY[]::text[];
    jobs_desbloqueados    := 0;

    -- Candado de la suscripción vigente: serializa renovaciones concurrentes.
    SELECT s.id, s.inicia_el, s.renueva_el
      INTO v_sub_id, v_inicia_el, v_renueva_ant
      FROM core.subscriptions s
     WHERE s.tenant_id = p_tenant_id
       AND s.finaliza_el IS NULL
       AND s.deleted_at IS NULL
       AND s.estado IN ('activa','periodo_gracia')
       FOR UPDATE;
    IF NOT FOUND THEN
        RETURN NEXT;                                     -- nada que renovar
        RETURN;
    END IF;

    IF v_renueva_ant > p_hoy THEN                        -- ya renovado: idempotente
        proxima_renovacion := v_renueva_ant;
        ciclo_nuevo := to_char(core.aniversario_en(
                           extract(day FROM v_inicia_el)::int,
                           (date_trunc('month', v_renueva_ant) - interval '1 month')::date),
                           'YYYY-MM');
        RETURN NEXT;
        RETURN;
    END IF;

    -- Si llevaba varios meses vencida, se salta directamente al ciclo vigente.
    proxima_renovacion := core.proximo_aniversario(v_inicia_el, p_hoy);
    v_inicio_ciclo := core.aniversario_en(
                          extract(day FROM v_inicia_el)::int,
                          (date_trunc('month', proxima_renovacion) - interval '1 month')::date);
    ciclo_nuevo := to_char(v_inicio_ciclo, 'YYYY-MM');

    UPDATE core.subscriptions SET renueva_el = proxima_renovacion WHERE id = v_sub_id;

    -- Ciclo nuevo con umbral normal; los ciclos anteriores quedan intactos.
    INSERT INTO core.usage_cycles (tenant_id, ciclo, umbral)
    VALUES (p_tenant_id, ciclo_nuevo, 'normal')
    ON CONFLICT (tenant_id, ciclo) DO NOTHING;

    SELECT d.tickets_desbloqueados, d.jobs_desbloqueados
      INTO tickets_desbloqueados, jobs_desbloqueados
      FROM core.desbloquear_cola_cuota(p_tenant_id) d;

    -- sello_previo / sello los fija el trigger de sellado (audit.seal_event).
    INSERT INTO audit.audit_events (tenant_id, actor_tipo, actor_id, evento, resumen, datos,
                                    sello_previo, sello)
    VALUES (p_tenant_id, 'sistema', v_actor, 'ciclo_renovado',
            format('Ciclo %s renovado; %s tickets desbloqueados',
                   ciclo_nuevo, cardinality(tickets_desbloqueados)),
            jsonb_build_object(
                'ciclo',                ciclo_nuevo,
                'inicio_ciclo',         v_inicio_ciclo,
                'renueva_el_anterior',  v_renueva_ant,
                'renueva_el',           proxima_renovacion,
                'tickets_desbloqueados', to_jsonb(tickets_desbloqueados),
                'jobs_desbloqueados',   jobs_desbloqueados),
            '', '');

    renovado := true;
    RETURN NEXT;
END $$;

COMMENT ON FUNCTION core.renovar_ciclo(text, date) IS
'KC-DB-09-1 (DB-F08): si renueva_el <= p_hoy, avanza renueva_el al próximo aniversario de inicia_el, abre el ciclo nuevo en usage_cycles (umbral normal, sin tocar el historial), desbloquea la cola de cuota dentro de las líneas del plan y audita ciclo_renovado, todo en la transacción del llamador. Idempotente: con renueva_el > p_hoy no escribe nada (renovado = false).';

GRANT EXECUTE ON FUNCTION core.aniversario_en(integer, date)      TO kallicode_app, kallicode_worker;
GRANT EXECUTE ON FUNCTION core.proximo_aniversario(date, date)    TO kallicode_app, kallicode_worker;
-- Las que escriben, solo a kallicode_app: el worker las invoca desde
-- sesion_tenant() (rol de aplicación con RLS); kallicode_worker no tiene UPDATE
-- sobre subscriptions ni tickets (010_rol_worker.sql) y no debe ganarlo aquí.
GRANT EXECUTE ON FUNCTION core.desbloquear_cola_cuota(text)       TO kallicode_app;
GRANT EXECUTE ON FUNCTION core.renovar_ciclo(text, date)          TO kallicode_app;

-- ---------------------------------------------------------------------------
-- 4. validar_integridad(): V01-V22 idénticos a 028_cuota_delta.sql y el
--    chequeo nuevo V23.
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

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría (sello, encadenamiento y recuento, KC-DB-05-1); V10/V12 la validación comercial (V10 contra la suma real de usage_loc, V22 sin consumo negativo, KC-DB-05-3); V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0); V23 las renovaciones por aniversario vencidas (KC-DB-09-1).';
