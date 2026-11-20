-- ============================================================================
-- KALLICODE · 070_smoke_release.sql
-- KC-DB-12-5 (3 SP) — Aceptación de GA: smoke de release.   CLASE A (función nueva, sin tocar privilegios existentes).
--
-- El smoke post-deploy de KC-DB-12-1 (db/tools/smoke_post_deploy.sql) solo comprueba la versión del esquema y la última
-- migración, y corre con el rol de la aplicación. El criterio de aceptación de GA pide más: versión del esquema,
-- los 26 chequeos de integridad en cero, la cadena de auditoría íntegra y cero tablas sin política. Esta función las reúne
-- en UNA llamada, con un resultado por comprobación:
--
--     SELECT * FROM core.smoke_release('v2.0.0', '070_smoke_release.sql', 68);
--
--   version_esquema                    etiqueta y última migración de core.version_esquema()
--   migraciones_aplicadas              el ledger tiene las migraciones que trae el artefacto (si se indica el número)
--   ledger_en_orden                    ninguna migración se aplicó antes que otra de nombre anterior
--   integridad_26_chequeos             core.validar_integridad(): el número de chequeos esperado y todos en cero
--   cadena_auditoria                   audit.verify_chain() íntegra para TODOS los tenants con cadena
--   tablas_sin_politica_retencion      core.contar_tablas_sin_retencion() = 0 (V26)
--   tablas_sin_politica_recuperacion   core.tablas_sin_politica_recuperacion() vacía (KC-DB-11-4)
--   aislamiento_sin_incumplimientos    core.informe_aislamiento() y core.informe_politicas_rls() sin INCUMPLE (KC-DB-12-3)
--   particiones_default_vacias         la alerta DB-A06 de core.alertas_db() apagada (KC-DB-11-5)
--
-- SOLO kallicode_admin puede ejecutarla: verificar la cadena de TODOS los tenants exige ver todos los tenants (BYPASSRLS).
-- La parte que sí debe poder hacer la aplicación (leer su versión) sigue en db/tools/smoke_post_deploy.sql.
-- db/tools/smoke_release.sql la ejecuta y sale con código distinto de 0 si alguna comprobación falla.
--
-- Aditiva y re-ejecutable. Sin BEGIN/COMMIT: el runner abre la transacción. Una función nueva no cambia las tablas, así que
-- no hace falta migrar datos; sí hay que regenerar db/esquema_v2.snapshot.json y docs/modelo_datos_v2.md (política de cambios).
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

CREATE OR REPLACE FUNCTION core.smoke_release(
    p_version_esperada       text,
    p_ultima_migracion       text,
    p_migraciones_esperadas  integer DEFAULT NULL,
    p_chequeos_esperados     integer DEFAULT 26
) RETURNS TABLE (chequeo text, ok boolean, detalle text)
LANGUAGE plpgsql STABLE
-- public al final: core.validar_integridad() usa vector_dims() de pgvector, que vive en public.
SET search_path = pg_catalog, public, pg_temp AS $f$
DECLARE
    v         record;
    v_n       integer;
    v_mal     bigint;
    v_nombres text;
    v_total   integer;
    v_rotas   integer;
    v_tenants integer;
    v_t       text;
BEGIN
    -- 1. versión del esquema
    IF to_regprocedure('core.version_esquema()') IS NULL THEN
        chequeo := 'version_esquema'; ok := false; detalle := 'core.version_esquema() no existe (falta la migración 067)'; RETURN NEXT;
    ELSE
        SELECT * INTO v FROM core.version_esquema();
        chequeo := 'version_esquema';
        ok := v.etiqueta IS NOT DISTINCT FROM p_version_esperada AND v.ultima_migracion IS NOT DISTINCT FROM p_ultima_migracion;
        detalle := format('etiqueta %s (esperada %s), última migración %s (esperada %s), %s posterior(es) a la etiqueta',
                          coalesce(v.etiqueta, '<ninguna>'), p_version_esperada, coalesce(v.ultima_migracion, '<ninguna>'),
                          p_ultima_migracion, v.migraciones_posteriores);
        RETURN NEXT;
    END IF;

    -- 2. número de migraciones aplicadas
    SELECT count(*) INTO v_n FROM core.schema_migrations;
    chequeo := 'migraciones_aplicadas';
    ok := p_migraciones_esperadas IS NULL OR v_n = p_migraciones_esperadas;
    detalle := format('%s en el ledger%s', v_n, CASE WHEN p_migraciones_esperadas IS NULL THEN ' (sin número esperado)'
                                                     ELSE format(' (el artefacto trae %s)', p_migraciones_esperadas) END);
    RETURN NEXT;

    -- 3. orden de aplicación: ninguna migración se aplicó antes que otra de nombre anterior
    SELECT count(*) INTO v_mal FROM (
        SELECT m.aplicado_en,
               max(m.aplicado_en) OVER (ORDER BY m.archivo ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previo
          FROM core.schema_migrations m) x
     WHERE x.aplicado_en < x.previo;
    chequeo := 'ledger_en_orden'; ok := v_mal = 0;
    detalle := format('%s migración(es) aplicada(s) antes que una de nombre anterior', v_mal);
    RETURN NEXT;

    -- 4. los chequeos de integridad
    SELECT count(*)::integer, coalesce(sum(i.problemas), 0),
           string_agg(i.chequeo || '=' || i.problemas, ', ') FILTER (WHERE i.problemas > 0)
      INTO v_total, v_mal, v_nombres
      FROM core.validar_integridad() i;
    chequeo := 'integridad_' || p_chequeos_esperados || '_chequeos';
    ok := v_total = p_chequeos_esperados AND v_mal = 0;
    detalle := format('%s chequeos (esperados %s), %s hallazgo(s)%s', v_total, p_chequeos_esperados, v_mal,
                      CASE WHEN v_nombres IS NULL THEN '' ELSE ': ' || v_nombres END);
    RETURN NEXT;

    -- 5. la cadena de auditoría de todos los tenants
    v_tenants := 0; v_rotas := 0; v_nombres := NULL;
    FOR v_t IN SELECT h.tenant_id FROM audit.chain_heads h ORDER BY h.tenant_id LOOP
        v_tenants := v_tenants + 1;
        IF NOT EXISTS (SELECT 1 FROM audit.verify_chain(v_t) c WHERE c.integra) THEN
            v_rotas := v_rotas + 1;
            v_nombres := coalesce(v_nombres || ', ', '') || v_t;
        END IF;
    END LOOP;
    chequeo := 'cadena_auditoria'; ok := v_rotas = 0;
    detalle := format('%s tenant(s) con cadena verificados, %s rota(s)%s', v_tenants, v_rotas,
                      CASE WHEN v_nombres IS NULL THEN '' ELSE ': ' || v_nombres END);
    RETURN NEXT;

    -- 6. tablas sin política
    chequeo := 'tablas_sin_politica_retencion'; ok := core.contar_tablas_sin_retencion() = 0;
    detalle := format('%s tabla(s) sin política de retención (V26)', core.contar_tablas_sin_retencion()); RETURN NEXT;

    SELECT count(*) INTO v_n FROM core.tablas_sin_politica_recuperacion();
    chequeo := 'tablas_sin_politica_recuperacion'; ok := v_n = 0;
    detalle := format('%s tabla(s) de cm y vec sin política de recuperación', v_n); RETURN NEXT;

    SELECT (SELECT count(*) FROM core.informe_aislamiento() a WHERE a.estado LIKE 'INCUMPLE%')
         + (SELECT count(*) FROM core.informe_politicas_rls() p WHERE p.estado LIKE 'INCUMPLE%')
      INTO v_n;
    chequeo := 'aislamiento_sin_incumplimientos'; ok := v_n = 0;
    detalle := format('%s tabla(s) o política(s) que incumplen el aislamiento (RLS, FORCE, cláusula TO, particiones)', v_n);
    RETURN NEXT;

    -- 7. ninguna fila perdida en una partición DEFAULT
    chequeo := 'particiones_default_vacias';
    ok := coalesce((SELECT NOT a.activa FROM core.alertas_db() a WHERE a.alerta = 'DB-A06'), false);
    detalle := coalesce((SELECT a.detalle FROM core.alertas_db() a WHERE a.alerta = 'DB-A06'), 'core.alertas_db() no disponible');
    RETURN NEXT;
END
$f$;
COMMENT ON FUNCTION core.smoke_release(text, text, integer, integer) IS
'KC-DB-12-5: smoke de release (aceptación de GA): versión del esquema, migraciones aplicadas y en orden, los chequeos de integridad en cero, la cadena de auditoría íntegra de todos los tenants, cero tablas sin política (retención, recuperación y aislamiento) y particiones DEFAULT vacías. Una fila por comprobación. Solo kallicode_admin.';
REVOKE ALL ON FUNCTION core.smoke_release(text, text, integer, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.smoke_release(text, text, integer, integer) TO kallicode_admin;
