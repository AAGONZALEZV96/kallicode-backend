-- ============================================================================
-- KALLICODE · 013_v21_fks_compuestas.sql
-- KC-DB-03-2 (DB-F01) — V21 revisa también las claves ajenas compuestas.
--
-- La versión de 011 solo revisaba las FK escalares. KC-DB-03-2 las convierte
-- TODAS en compuestas (tenant_id, x) -> padre(tenant_id, id): sin este cambio
-- V21 se quedaría revisando 0 relaciones. Ahora revisa toda FK entre dos
-- tablas con tenant_id, uniendo hija y padre por las columnas de la FK salvo
-- la pareja tenant_id = tenant_id, que es justo lo que se compara.
--
-- Una FK compuesta VALIDADA garantiza 0 filas; V21 sigue siendo útil en la
-- ventana NOT VALID -> VALIDATE (las filas viejas aún no se revisaron) y si
-- alguien desactiva o recrea una FK. validar_integridad() no cambia: V21 ya
-- delega en esta función.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '1min';

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
               -- Unión hija/padre por las columnas de la FK, sin la pareja de tenant:
               (SELECT string_agg(format('p.%I = h.%I', pa.attname, ha.attname), ' AND ')
                  FROM unnest(c.conkey, c.confkey) AS k(hijo_n, padre_n)
                  JOIN pg_attribute ha ON ha.attrelid = c.conrelid  AND ha.attnum = k.hijo_n
                  JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.padre_n
                 WHERE NOT (ha.attname = 'tenant_id' AND pa.attname = 'tenant_id')
               ) AS union_sql,
               -- Clave primaria de la hija como jsonb, para identificar la fila:
               (SELECT string_agg(format('%L, h.%I', a.attname, a.attname), ', ')
                  FROM pg_index i
                  JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                 WHERE i.indrelid = c.conrelid AND i.indisprimary
               ) AS pk_sql
          FROM pg_constraint c
         WHERE c.contype = 'f'
           -- la hija y el padre tienen tenant_id (escalar o compuesta, da igual)
           AND EXISTS (SELECT 1 FROM pg_attribute
                        WHERE attrelid = c.conrelid AND attname = 'tenant_id' AND NOT attisdropped)
           AND EXISTS (SELECT 1 FROM pg_attribute
                        WHERE attrelid = c.confrelid AND attname = 'tenant_id' AND NOT attisdropped)
         ORDER BY c.conrelid::regclass::text, c.conname
    LOOP
        CONTINUE WHEN r.union_sql IS NULL;  -- FK solo por tenant_id: nada que comparar
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
'KC-DB-03-3/03-2: filas hijas cuyo tenant_id no coincide con el de su padre, en toda FK
(escalar o compuesta) entre dos tablas con tenant_id, generadas desde pg_constraint.
Base de V21 y consulta de reparación.';
