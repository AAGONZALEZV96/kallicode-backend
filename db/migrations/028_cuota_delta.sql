-- ============================================================================
-- KALLICODE · 028_cuota_delta.sql
-- KC-DB-05-3 (DB-F07, ALTO) — Trigger de cuota que respeta los ajustes
-- negativos y reacciona a UPDATE y DELETE.
--
-- core.accumulate_loc acumulaba greatest(NEW.netas, 0): un usage_loc de tipo
-- 'ajuste' con netas = -500 (un crédito comercial explícitamente permitido por
-- loc_netas_chk, que solo exige la aritmética cuando tipo = 'pr') se aceptaba en
-- la tabla y se descartaba en el agregado sin error ni aviso. Además
-- trg_usage_loc_acumula era AFTER INSERT únicamente: un UPDATE o un DELETE
-- desincronizaban usage_cycles para siempre, y V10 replicaba el mismo greatest(),
-- así que tampoco lo detectaba.
--
-- Corrección:
--   * El trigger pasa a AFTER INSERT OR UPDATE OF (tenant_id, ciclo, netas) OR
--     DELETE y aplica el delta de cada fila SIN greatest(): INSERT suma +netas,
--     DELETE resta netas y UPDATE aplica la diferencia (o mueve la fila de ciclo).
--   * El piso en cero se aplica solo al total acumulado del ciclo. Para que ese
--     piso no haga perder crédito (100 consumidas, ajuste -300 y luego +50 debe
--     dejar 0, no 50), usage_cycles guarda además la suma real de netas, que puede
--     ser negativa (loc_netas); loc_consumidas = greatest(loc_netas, 0).
--   * V10 compara contra la suma real de usage_loc y se agrega V22: ningún ciclo
--     puede tener loc_consumidas negativo.
--
-- Nota: el ticket nombra esta migración 015, pero 015 ya existe.
-- Nota: desde KC-DB-05-2 usage_loc es append-only; las reacciones a UPDATE y
--       DELETE solo se ejercitan en mantenimiento privilegiado (el trigger de
--       inmutabilidad desactivado a propósito) y en la cascada SET NULL del
--       ticket, que no cambia netas, tenant ni ciclo y por eso no dispara nada.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. usage_cycles guarda la suma real de netas del ciclo (puede ser negativa).
-- ---------------------------------------------------------------------------
ALTER TABLE core.usage_cycles ADD COLUMN loc_netas integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN core.usage_cycles.loc_netas IS
'KC-DB-05-3: suma real de usage_loc.netas del ciclo (un crédito mayor que lo consumido la deja negativa). loc_consumidas = greatest(loc_netas, 0).';

-- ---------------------------------------------------------------------------
-- 2. Umbral del ciclo según el límite del plan vigente (lógica de T6 sin cambios).
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.umbral_cuota(p_tenant text, p_consumidas integer) RETURNS text
LANGUAGE plpgsql STABLE AS $$
DECLARE
    lim integer;
BEGIN
    SELECT p.loc_mes INTO lim
      FROM core.subscriptions s JOIN core.plans p ON p.codigo = s.plan_codigo
     WHERE s.tenant_id = p_tenant AND s.finaliza_el IS NULL;

    IF lim IS NULL THEN
        RETURN 'normal';                                 -- Enterprise: sin límite
    ELSIF p_consumidas >= lim THEN
        RETURN 'agotado';
    ELSIF p_consumidas >= lim * 0.8 THEN
        RETURN 'aviso_80';
    END IF;
    RETURN 'normal';
END $$;

-- ---------------------------------------------------------------------------
-- 3. Aplica un delta al ciclo en una sola sentencia (el UPSERT bloquea la fila:
--    dos cambios simultáneos del mismo ciclo se serializan). p_crear = false
--    para las salidas (DELETE, o el ciclo viejo de un UPDATE): si el ciclo no
--    existe no hay nada que ajustar y no se crea una fila nueva.
-- ---------------------------------------------------------------------------
CREATE FUNCTION core.aplicar_delta_loc(p_tenant text, p_ciclo text, p_delta integer,
                                       p_crear boolean DEFAULT true) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    IF p_crear THEN
        INSERT INTO core.usage_cycles (tenant_id, ciclo, loc_netas, loc_consumidas, umbral)
        VALUES (p_tenant, p_ciclo, p_delta, greatest(p_delta, 0),
                core.umbral_cuota(p_tenant, greatest(p_delta, 0)))
        ON CONFLICT (tenant_id, ciclo) DO UPDATE
           SET loc_netas      = core.usage_cycles.loc_netas + p_delta,
               loc_consumidas = greatest(core.usage_cycles.loc_netas + p_delta, 0),
               umbral         = core.umbral_cuota(
                                    p_tenant, greatest(core.usage_cycles.loc_netas + p_delta, 0)),
               actualizado_en = now();
    ELSE
        UPDATE core.usage_cycles
           SET loc_netas      = loc_netas + p_delta,
               loc_consumidas = greatest(loc_netas + p_delta, 0),
               umbral         = core.umbral_cuota(p_tenant, greatest(loc_netas + p_delta, 0)),
               actualizado_en = now()
         WHERE tenant_id = p_tenant AND ciclo = p_ciclo;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 4. El trigger de acumulación: INSERT, UPDATE y DELETE, sin greatest() por fila.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.accumulate_loc() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM core.aplicar_delta_loc(NEW.tenant_id, NEW.ciclo, NEW.netas);
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM core.aplicar_delta_loc(OLD.tenant_id, OLD.ciclo, -OLD.netas, false);
    ELSIF NEW.tenant_id = OLD.tenant_id AND NEW.ciclo = OLD.ciclo THEN
        IF NEW.netas <> OLD.netas THEN
            PERFORM core.aplicar_delta_loc(NEW.tenant_id, NEW.ciclo, NEW.netas - OLD.netas);
        END IF;
    ELSE
        -- La fila cambió de ciclo (o de tenant): sale del viejo y entra al nuevo.
        PERFORM core.aplicar_delta_loc(OLD.tenant_id, OLD.ciclo, -OLD.netas, false);
        PERFORM core.aplicar_delta_loc(NEW.tenant_id, NEW.ciclo, NEW.netas);
    END IF;
    RETURN NULL;                                         -- trigger AFTER: se ignora
END $$;

DROP TRIGGER trg_usage_loc_acumula ON core.usage_loc;
CREATE TRIGGER trg_usage_loc_acumula
    AFTER INSERT OR UPDATE OF tenant_id, ciclo, netas OR DELETE ON core.usage_loc
    FOR EACH ROW EXECUTE FUNCTION core.accumulate_loc();

-- ---------------------------------------------------------------------------
-- 5. Reparar lo ya acumulado: los ciclos que descartaron ajustes negativos (o que
--    cambiaron por UPDATE/DELETE) se recalculan desde usage_loc. Corre como
--    kallicode_admin (BYPASSRLS), sobre todos los tenants.
-- ---------------------------------------------------------------------------
UPDATE core.usage_cycles c
   SET loc_netas      = s.neto,
       loc_consumidas = greatest(s.neto, 0),
       umbral         = core.umbral_cuota(c.tenant_id, greatest(s.neto, 0)),
       actualizado_en = now()
  FROM (SELECT c2.tenant_id, c2.ciclo, coalesce(sum(l.netas), 0)::integer AS neto
          FROM core.usage_cycles c2
          LEFT JOIN core.usage_loc l ON l.tenant_id = c2.tenant_id AND l.ciclo = c2.ciclo
         GROUP BY c2.tenant_id, c2.ciclo) s
 WHERE c.tenant_id = s.tenant_id AND c.ciclo = s.ciclo
   AND (c.loc_netas <> s.neto OR c.loc_consumidas <> greatest(s.neto, 0));

-- ---------------------------------------------------------------------------
-- 6. validar_integridad(): V01-V21 idénticos a 024_cadena_auditoria.sql salvo V10,
--    que compara contra la suma real de usage_loc, y el chequeo nuevo V22.
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

END $$;

COMMENT ON FUNCTION core.validar_integridad() IS
'Batería de chequeos de integridad de negocio. Programar ejecución nocturna y
alertar si cualquier fila devuelve problemas > 0. Los chequeos V16/V17 verifican
la cadena de hashes de auditoría (sello, encadenamiento y recuento, KC-DB-05-1); V10/V12 la validación comercial (V10 contra la suma real de usage_loc, V22 sin consumo negativo, KC-DB-05-3); V21 la
coherencia de tenant entre padre e hijo (KC-DB-03-2 depende de que esté en 0).';

