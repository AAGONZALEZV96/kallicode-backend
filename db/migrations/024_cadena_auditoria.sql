-- ============================================================================
-- KALLICODE · 024_cadena_auditoria.sql
-- KC-DB-05-1 (DB-F04, BLOQUEANTE) — audit.verify_chain verifica el
-- encadenamiento y audit.chain_heads queda protegida.
--
-- Dos defectos de la cadena de auditoría (db/03_triggers.sql, db/04_rls.sql):
--
--   1. verify_chain recalculaba el sello de cada fila y lo comparaba con el
--      almacenado, pero nunca comprobaba que sello_previo coincidiera con el
--      sello del evento anterior: cada evento se validaba contra sí mismo, así
--      que borrar o truncar eventos intermedios dejaba la cadena «íntegra».
--   2. 04_rls.sql concedía UPDATE sobre audit.chain_heads a kallicode_app y
--      ningún trigger la protegía: el rol de la aplicación podía ajustar
--      ultimo_sello y eventos para que V16/V17 cuadraran tras borrar eventos.
--
-- Corrección:
--   * verify_chain exige el encadenamiento (sello_previo = sello del eslabón
--     anterior; génesis = repeat('0', 64)) y devuelve eventos esperados frente
--     a observados.
--   * kallicode_app pierde INSERT/UPDATE/DELETE/TRUNCATE sobre chain_heads: solo
--     la actualiza audit.seal_event (SECURITY DEFINER). Un trigger rechaza
--     cualquier retroceso o reescritura de la cabeza.
--   * V16 y V17 se reescriben sobre la función corregida y detectan huecos.
--
-- Nota: el ticket nombra esta migración 013, pero 013 ya existe.
--
-- Nota de diseño: el encadenamiento se verifica como propiedad del conjunto de
-- eventos (cada sello_previo es el génesis o el sello de otro evento del
-- tenant; ningún sello_previo se repite) y no recorriendo por id. El id se
-- asigna ANTES de que seal_event tome el candado de la cabeza, así que con
-- escrituras concurrentes del mismo tenant el orden por id puede diferir del
-- orden real de la cadena y una verificación por id daría falsas alarmas.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Índice para resolver sello_previo -> evento anterior sin recorrer la tabla.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS audit_events_sello_ix ON audit.audit_events (tenant_id, sello);

-- ---------------------------------------------------------------------------
-- 2. verify_chain: sello propio + encadenamiento + recuento.
--    integra = false si algún evento (a) no cuadra con su sello, (b) apunta a
--    un eslabón que ya no existe, (c) comparte eslabón anterior con otro
--    (bifurcación) o (d) el recuento observado difiere del de chain_heads.
--    primer_evento_roto es el id menor entre los (a)-(c); un recorte de cola no
--    tiene evento «roto» y se delata solo por eventos_esperados <> observados.
--    Con ventana (p_desde/p_hasta) no hay recuento esperado: es NULL.
-- ---------------------------------------------------------------------------
DROP FUNCTION audit.verify_chain(text, timestamptz, timestamptz);

CREATE FUNCTION audit.verify_chain(p_tenant text,
                                   p_desde timestamptz DEFAULT NULL,
                                   p_hasta timestamptz DEFAULT NULL)
RETURNS TABLE (integra boolean,
               eventos_verificados bigint,
               primer_evento_roto bigint,
               eventos_esperados bigint,
               eventos_observados bigint)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    genesis constant text := repeat('0', 64);
    sin_ventana boolean := (p_desde IS NULL AND p_hasta IS NULL);
    n bigint;
    roto bigint;
BEGIN
    WITH ev AS (
        SELECT e.id,
               encode(sha256(convert_to(
                   e.sello_previo || e.tenant_id || e.evento || e.resumen
                   || coalesce(e.datos::text, '') || coalesce(e.ticket_id, '')
                   || coalesce(e.actor_id, '')
                   || to_char(e.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                   'UTF8')), 'hex') <> e.sello AS sello_malo,
               (e.sello_previo <> genesis AND NOT EXISTS (
                   SELECT 1 FROM audit.audit_events p
                    WHERE p.tenant_id = e.tenant_id AND p.sello = e.sello_previo
               )) AS eslabon_perdido,
               (count(*) OVER (PARTITION BY e.sello_previo) > 1) AS bifurcado
          FROM audit.audit_events e
         WHERE e.tenant_id = p_tenant
           AND (p_desde IS NULL OR e.creado_en >= p_desde)
           AND (p_hasta IS NULL OR e.creado_en <= p_hasta)
    )
    SELECT count(*), min(id) FILTER (WHERE sello_malo OR eslabon_perdido OR bifurcado)
      INTO n, roto
      FROM ev;

    eventos_verificados := n;
    eventos_observados  := n;
    primer_evento_roto  := roto;
    eventos_esperados   := NULL;
    IF sin_ventana THEN
        SELECT h.eventos INTO eventos_esperados
          FROM audit.chain_heads h WHERE h.tenant_id = p_tenant;
    END IF;

    integra := roto IS NULL
               AND (eventos_esperados IS NULL OR eventos_esperados = n);
    RETURN NEXT;
END $$;

COMMENT ON FUNCTION audit.verify_chain(text, timestamptz, timestamptz) IS
'KC-DB-05-1: verifica sello propio, encadenamiento (sello_previo = sello del eslabon anterior, genesis repeat(0,64)) y recuento contra chain_heads. Usada por validar_integridad (V16/V17) y GET /audit/integrity.';

-- ---------------------------------------------------------------------------
-- 3. seal_event: único camino de escritura de chain_heads.
--    SECURITY DEFINER (dueño: kallicode_admin) para poder quitarle a la
--    aplicación todo permiso de escritura sobre la cabeza. Una sola
--    actualización por evento (eventos + 1 y ultimo_sello a la vez), tras tomar
--    el candado de la fila: la cadena no se bifurca.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.seal_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE prev text;
BEGIN
    -- La función corre con privilegios de su dueño: no puede sellar eventos de
    -- otro tenant aunque el INSERT lo pida (la política RLS lo rechazaría, pero
    -- solo después de haber tocado la cabeza ajena).
    IF coalesce(core.current_tenant(), '') <> ''
       AND NEW.tenant_id <> core.current_tenant() THEN
        RAISE EXCEPTION 'audit.seal_event: el evento es de otro tenant (%)', NEW.tenant_id
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    INSERT INTO audit.chain_heads (tenant_id) VALUES (NEW.tenant_id)
        ON CONFLICT (tenant_id) DO NOTHING;
    -- Bloquea la cabeza de cadena del tenant hasta el COMMIT:
    SELECT ultimo_sello INTO prev
      FROM audit.chain_heads WHERE tenant_id = NEW.tenant_id FOR UPDATE;

    NEW.sello_previo := prev;
    NEW.creado_en    := now();
    NEW.sello := encode(sha256(convert_to(
        prev
        || NEW.tenant_id
        || NEW.evento
        || NEW.resumen
        || coalesce(NEW.datos::text, '')
        || coalesce(NEW.ticket_id, '')
        || coalesce(NEW.actor_id, '')
        || to_char(NEW.creado_en, 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'UTF8')), 'hex');

    UPDATE audit.chain_heads
       SET eventos = eventos + 1, ultimo_sello = NEW.sello
     WHERE tenant_id = NEW.tenant_id;
    RETURN NEW;
END $$;

-- ---------------------------------------------------------------------------
-- 4. La aplicación pierde la escritura sobre chain_heads (conserva SELECT:
--    verify_chain y V16/V17 la leen).
-- ---------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit.chain_heads FROM kallicode_app;

-- ---------------------------------------------------------------------------
-- 5. Inmutabilidad de la cabeza: solo se admite avanzar de uno en uno. Ni la
--    aplicación ni un rol privilegiado pueden hacerla retroceder o reescribirla
--    sin desactivar este trigger a propósito. Borrar una cabeza solo se permite
--    si el tenant ya no tiene eventos (p. ej. limpieza de pruebas).
-- ---------------------------------------------------------------------------
CREATE FUNCTION audit.chain_heads_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.tenant_id <> OLD.tenant_id OR NEW.eventos <> OLD.eventos + 1 THEN
            RAISE EXCEPTION
                'audit.chain_heads: la cabeza solo avanza de uno en uno (eventos % -> %)',
                OLD.eventos, NEW.eventos USING ERRCODE = 'raise_exception';
        END IF;
        RETURN NEW;
    END IF;
    -- DELETE
    IF EXISTS (SELECT 1 FROM audit.audit_events WHERE tenant_id = OLD.tenant_id) THEN
        RAISE EXCEPTION 'audit.chain_heads: el tenant % aun tiene eventos; no se puede borrar su cabeza',
            OLD.tenant_id USING ERRCODE = 'raise_exception';
    END IF;
    RETURN OLD;
END $$;

CREATE TRIGGER trg_chain_heads_guard
    BEFORE UPDATE OR DELETE ON audit.chain_heads
    FOR EACH ROW EXECUTE FUNCTION audit.chain_heads_guard();

CREATE FUNCTION audit.chain_heads_no_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit.chain_heads: TRUNCATE prohibido' USING ERRCODE = 'raise_exception';
END $$;

CREATE TRIGGER trg_chain_heads_no_truncate
    BEFORE TRUNCATE ON audit.chain_heads
    FOR EACH STATEMENT EXECUTE FUNCTION audit.chain_heads_no_truncate();

-- ---------------------------------------------------------------------------
-- 6. validar_integridad(): V01-V21 idénticos a 011_v21_coherencia_tenant.sql
--    salvo V16 y V17 (PostgreSQL no permite cambiar un chequeo: se reemplaza
--    la función).
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

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría (sello, encadenamiento y recuento, KC-DB-05-1); V10/V12 la validación comercial; V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0).';

