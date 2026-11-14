-- ============================================================================
-- KALLICODE · 051_checks_dominio.sql
-- KC-DB-09-2 (DB-F25, MEDIO) — CHECKs de dominio ausentes y cotas de longitud de texto.
--
-- Faltaban restricciones elementales que permitían datos imposibles:
--   * positividad: core.specs.version, core.qa_runs.corrida, core.production_lines.numero (admitían 0 y
--     negativos) y core.ticket_counters.ultimo (no negativo: el contador arranca en 1000);
--   * fechas de suscripción: sub_fechas_chk solo comparaba renueva_el con inicia_el, de modo que finaliza_el
--     podía ser anterior al inicio;
--   * tickets: etapa 'cancelado' sin quién canceló, y etapas terminales (produccion, cancelado,
--     cerrado_duplicado) sin cerrado_en. La lista de etapas terminales es core.pipeline_stages.es_terminal;
--     un CHECK no puede consultar otra tabla, por eso se repite aquí y lo vigila
--     packages/core/tests/test_checks_dominio.py;
--   * ninguna columna de texto libre tenía cota: títulos de hasta 200 caracteres y descripciones, comentarios
--     y y motivos de hasta 20.000.
--
-- Nombres (convención del repositorio: <prefijo de tabla>_<columna>_<tipo>_chk):
--   *_pos_chk   positividad          *_len_chk   longitud máxima de texto
--   tk_cancel_por_chk, tk_cerrado_chk, sub_finaliza_chk  coherencia entre columnas
--
-- Datos existentes. Cada restricción se crea NOT VALID (rige para toda escritura nueva desde ya, sin
-- recorrer ni bloquear la tabla). Después la migración cuenta, por restricción, las filas que NO la
-- cumplirían y las LISTA (aviso con hasta 20 identificadores por restricción); si no hay ninguna, la
-- valida (VALIDATE CONSTRAINT). Si hay alguna, la deja sin validar y avisa con un WARNING: se corrigen
-- esas filas y se valida a mano (la vista core.v_checks_dominio_sin_validar dice cuáles faltan). Así la
-- migración nunca falla por datos antiguos ni deja pasar datos nuevos inválidos.
--
-- Nota: el ticket nombra db/migrations/034_checks_dominio.sql y tests/test_checks_dominio.py; la 034 está
-- ocupada, la siguiente libre es la 051, y la prueba vive en packages/core/tests/.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

DO $$
DECLARE
    c record;
    n bigint;
    ids text;
    terminales constant text := $t$('produccion', 'cancelado', 'cerrado_duplicado')$t$;
BEGIN
    FOR c IN
        SELECT * FROM (VALUES
            -- tabla, restricción, condición CHECK, expresión que identifica la fila
            ('core.specs',            'spec_version_pos_chk',   'version > 0',                       'id'),
            ('core.qa_runs',          'qa_corrida_pos_chk',     'corrida > 0',                       'id'),
            ('core.production_lines', 'lines_numero_pos_chk',   'numero > 0',                        $i$tenant_id || '/' || numero$i$),
            ('core.ticket_counters',  'tc_ultimo_pos_chk',      'ultimo >= 0',                       'tenant_id'),
            ('core.subscriptions',    'sub_finaliza_chk',       'finaliza_el IS NULL OR finaliza_el >= inicia_el', 'id'),
            ('core.tickets',          'tk_cancel_por_chk',      $x$etapa <> 'cancelado' OR cancelado_por IS NOT NULL$x$, 'id'),
            ('core.tickets',          'tk_cerrado_chk',         'etapa NOT IN ' || terminales || ' OR cerrado_en IS NOT NULL', 'id'),
            ('core.tickets',          'tk_titulo_len_chk',      'char_length(titulo) <= 200',        'id'),
            ('core.tickets',          'tk_descripcion_len_chk', 'char_length(descripcion) <= 20000', 'id'),
            ('core.tickets',          'tk_motivo_cancel_len_chk', 'char_length(motivo_cancelacion) <= 20000', 'id'),
            ('core.spec_alternatives', 'alt_titulo_len_chk',    'char_length(titulo) <= 200',        'id'),
            ('core.spec_alternatives', 'alt_descripcion_len_chk', 'char_length(descripcion) <= 20000', 'id'),
            ('core.security_findings', 'sf_titulo_len_chk',     'char_length(titulo) <= 200',        'id'),
            ('core.notifications',    'nt_titulo_len_chk',      'char_length(titulo) <= 200',        'id'),
            ('core.gate_signatures',  'gs_comentario_len_chk',  'char_length(comentario) <= 20000',  'id'),
            ('core.upgrade_requests', 'upg_comentario_len_chk', 'char_length(comentario) <= 20000',  'id'),
            ('core.test_quarantine',  'tq_motivo_len_chk',      'char_length(motivo) <= 20000',      'id')
        ) AS v(tabla, nombre, condicion, ident)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conrelid = c.tabla::regclass AND conname = c.nombre) THEN
            EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I CHECK (%s) NOT VALID', c.tabla, c.nombre, c.condicion);
        END IF;

        -- Filas que no la cumplirían (un CHECK solo rechaza cuando la condición es FALSE, no NULL).
        EXECUTE format('SELECT count(*) FROM %s WHERE (%s) IS FALSE', c.tabla, c.condicion) INTO n;
        IF n > 0 THEN
            EXECUTE format('SELECT string_agg(i, '', '') FROM (SELECT (%s)::text AS i FROM %s WHERE (%s) IS FALSE LIMIT 20) q',
                           c.ident, c.tabla, c.condicion) INTO ids;
        END IF;

        IF n = 0 THEN
            EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', c.tabla, c.nombre);
        ELSE
            RAISE WARNING '051: % (%): % fila(s) no la cumplen y quedan sin validar. Primeras (hasta 20): %. Corríjalas y ejecute ALTER TABLE % VALIDATE CONSTRAINT %.',
                c.nombre, c.tabla, n, ids, c.tabla, c.nombre;
        END IF;
    END LOOP;
END $$;

-- Restricciones de dominio que quedaron sin validar (por filas antiguas): se corrigen y se validan a mano.
CREATE OR REPLACE VIEW core.v_checks_dominio_sin_validar AS
SELECT c.conrelid::regclass::text AS tabla,
       c.conname                  AS restriccion,
       pg_get_constraintdef(c.oid) AS definicion
  FROM pg_constraint c
 WHERE c.contype = 'c'
   AND NOT c.convalidated
   AND c.conname ~ '(_pos_chk|_len_chk|^tk_cancel_por_chk|^tk_cerrado_chk|^sub_finaliza_chk)$'
   AND c.conislocal;
COMMENT ON VIEW core.v_checks_dominio_sin_validar IS
'KC-DB-09-2: CHECKs de dominio (positividad, cotas de longitud, coherencia de fechas y etapas) creados NOT VALID cuyas filas antiguas no los cumplían. Vacía = todos validados. Para cerrar: corregir las filas y ALTER TABLE ... VALIDATE CONSTRAINT.';
REVOKE ALL ON core.v_checks_dominio_sin_validar FROM PUBLIC, kallicode_app, kallicode_worker;
