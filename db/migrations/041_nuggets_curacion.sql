-- ============================================================================
-- KALLICODE · 041_nuggets_curacion.sql
-- KC-CM-08-2 — Propuesta de cm.nuggets con curación humana obligatoria.
--
-- cm.nuggets (002) son las reglas cortas que las plantillas del pipeline
-- consumen vía {{nuggets}}. Hasta ahora nada impedía escribir un nugget ya
-- 'aprobado', su evidencia_ref era texto libre y la regla no tenía límite.
-- El extractor de KC-CM-08-2 las propone desde la documentación ingerida
-- (vec.doc_chunks, KC-CM-08-1); esta migración hace que la base garantice:
--
--   1. Evidencia resoluble: evidencia_ref es el id del chunk de documentación
--      que la respalda, con FK compuesta a vec.doc_chunks. Si el chunk
--      desaparece (el documento se reprocesa más corto o falla), el nugget cae
--      en cascada: una regla cuya evidencia ya no existe no se puede resolver.
--      La sección de origen y la cita literal se guardan en el nugget.
--   2. Límite de longitud: cm.nugget_regla_max_chars() = 280, para que los
--      nuggets aprobados quepan en el presupuesto de contexto de {{nuggets}}.
--      El extractor (kallicode_codemapping.nuggets.REGLA_MAX_CHARS) usa el
--      mismo valor; un test lo compara.
--   3. Curación humana obligatoria:
--      - todo nugget nace 'propuesto' (trigger BEFORE INSERT) y su alta abre
--        una revisión 'pendiente' en cm.curation_queue (trigger AFTER INSERT);
--      - solo pasa a 'aprobado'/'rechazado' si cm.curation_queue tiene ESA
--        resolución registrada por un usuario del tenant (mismo estado, mismo
--        revisado_por, mismo instante) — trigger BEFORE UPDATE;
--      - una vez resuelto es inmutable;
--      - cm.resolver_nugget() registra la resolución y la aplica en una sola
--        transacción: es el camino soportado (portal de curación).
--
-- "pendiente" del ticket = estado 'propuesto' del nugget (CHECK de 002, sin
-- tocar) + revisión 'pendiente' en cm.curation_queue.
--
-- No es destructiva: solo ADD COLUMN / ADD CONSTRAINT / CREATE. Si hubiera
-- nuggets previos con evidencia inexistente o aprobados sin resolución, la FK
-- o el CHECK fallan y la migración entera se revierte sin borrar nada.
-- Sin BEGIN/COMMIT: el runner abre la transacción (db/migrations/README.md).
-- ============================================================================

SET LOCAL lock_timeout = '5s';

-- ---------------------------------------------------------------------------
-- 1. Columnas: tipo, sección de origen, cita y resolución humana
-- ---------------------------------------------------------------------------
ALTER TABLE cm.nuggets
    ADD COLUMN tipo              text NOT NULL DEFAULT 'regla_negocio',
    ADD COLUMN seccion           text NOT NULL DEFAULT '',
    ADD COLUMN cita              text NOT NULL DEFAULT '',
    ADD COLUMN propuesto_en      timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN resuelto_por      text,
    ADD COLUMN resuelto_en       timestamptz,
    ADD COLUMN motivo_resolucion text;

-- El tipo no tiene un valor por defecto con sentido: solo se dio para poder
-- agregar la columna NOT NULL; las filas nuevas deben traerlo.
ALTER TABLE cm.nuggets ALTER COLUMN tipo DROP DEFAULT;

CREATE FUNCTION cm.nugget_regla_max_chars() RETURNS integer
    LANGUAGE sql IMMUTABLE AS $$ SELECT 280 $$;

ALTER TABLE cm.nuggets
    ADD CONSTRAINT nuggets_tipo_chk
        CHECK (tipo IN ('regla_negocio', 'restriccion_tecnica')),
    ADD CONSTRAINT nuggets_regla_len_chk
        CHECK (btrim(regla) <> '' AND char_length(regla) <= cm.nugget_regla_max_chars()),
    ADD CONSTRAINT nuggets_cita_len_chk CHECK (char_length(cita) <= 300),
    -- Resuelto <=> con quién y cuándo. Propuesto <=> sin ninguno de los dos.
    ADD CONSTRAINT nuggets_resolucion_chk
        CHECK ((estado = 'propuesto') = (resuelto_por IS NULL AND resuelto_en IS NULL)),
    ADD CONSTRAINT nuggets_resolucion_completa_chk
        CHECK ((resuelto_por IS NULL) = (resuelto_en IS NULL)),
    -- vec.doc_chunks está particionada por tenant: la FK incluye tenant_id
    -- (también así un nugget no puede citar un chunk de otro tenant).
    ADD CONSTRAINT nuggets_evidencia_ref_fkey
        FOREIGN KEY (tenant_id, evidencia_ref)
        REFERENCES vec.doc_chunks (tenant_id, id) ON DELETE CASCADE,
    ADD CONSTRAINT nuggets_resuelto_por_fkey
        FOREIGN KEY (tenant_id, resuelto_por)
        REFERENCES core.users (tenant_id, id);

-- Cubre la FK nuggets_evidencia_ref_fkey (borrar un chunk arrastra sus nuggets
-- sin recorrer la tabla) y responde "¿qué reglas salen de este chunk?".
-- Documentado en docs/plan-indices.md.
CREATE INDEX nuggets_evidencia_ix ON cm.nuggets (tenant_id, evidencia_ref);

-- Una revisión de nugget resuelta siempre dice quién y cuándo.
ALTER TABLE cm.curation_queue
    ADD CONSTRAINT curation_nugget_revision_chk
        CHECK (tipo <> 'nugget'
               OR (estado = 'pendiente') = (revisado_por IS NULL AND revisado_en IS NULL));

-- ---------------------------------------------------------------------------
-- 2. Curación obligatoria (triggers)
-- ---------------------------------------------------------------------------
CREATE FUNCTION cm.nuggets_curacion_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.estado <> 'propuesto' OR NEW.resuelto_por IS NOT NULL
           OR NEW.resuelto_en IS NOT NULL THEN
            RAISE EXCEPTION 'cm.nuggets: un nugget nace propuesto; % exige una resolución humana registrada',
                NEW.estado
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.estado <> 'propuesto' THEN
        RAISE EXCEPTION 'cm.nuggets: el nugget % ya fue resuelto (%) y no se modifica', OLD.id, OLD.estado
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.evidencia_ref IS DISTINCT FROM OLD.evidencia_ref THEN
        RAISE EXCEPTION 'cm.nuggets: la identidad y la evidencia de un nugget no cambian (%)', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.estado <> 'propuesto' AND NOT EXISTS (
        SELECT 1
          FROM cm.curation_queue q
         WHERE q.tenant_id = NEW.tenant_id
           AND q.tipo = 'nugget'
           AND q.payload @> jsonb_build_object('nugget_id', NEW.id)
           AND q.estado = NEW.estado
           AND q.revisado_por = NEW.resuelto_por
           AND q.revisado_en = NEW.resuelto_en
    ) THEN
        RAISE EXCEPTION 'cm.nuggets: % sin resolución humana registrada en cm.curation_queue (%)',
            NEW.estado, NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER nuggets_curacion
    BEFORE INSERT OR UPDATE ON cm.nuggets
    FOR EACH ROW EXECUTE FUNCTION cm.nuggets_curacion_trg();

-- Alta de un nugget => revisión pendiente en la cola de curación. Las
-- revisiones ya resueltas de una propuesta anterior del mismo nugget se
-- conservan como historial: el id de la revisión es propio, no el del nugget.
CREATE FUNCTION cm.nuggets_encolar_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cm.curation_queue (id, tenant_id, tipo, payload)
    VALUES ('cq_' || replace(gen_random_uuid()::text, '-', ''), NEW.tenant_id, 'nugget',
            jsonb_build_object(
                'nugget_id', NEW.id,
                'regla', NEW.regla,
                'tipo', NEW.tipo,
                'evidencia_ref', NEW.evidencia_ref,
                'seccion', NEW.seccion,
                'cita', NEW.cita,
                'scope_refs', NEW.scope_refs));
    RETURN NULL;
END $$;

CREATE TRIGGER nuggets_encolar
    AFTER INSERT ON cm.nuggets
    FOR EACH ROW EXECUTE FUNCTION cm.nuggets_encolar_trg();

-- Nugget borrado (p. ej. en cascada desde su chunk) => su revisión pendiente
-- ya no tiene qué resolver. Las resueltas quedan como historial.
CREATE FUNCTION cm.nuggets_retirar_revision_trg() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM cm.curation_queue
     WHERE tenant_id = OLD.tenant_id
       AND tipo = 'nugget'
       AND estado = 'pendiente'
       AND payload @> jsonb_build_object('nugget_id', OLD.id);
    RETURN NULL;
END $$;

CREATE TRIGGER nuggets_retirar_revision
    AFTER DELETE ON cm.nuggets
    FOR EACH ROW EXECUTE FUNCTION cm.nuggets_retirar_revision_trg();

-- ---------------------------------------------------------------------------
-- 3. Resolución humana: registra la decisión y la aplica en una transacción
-- ---------------------------------------------------------------------------
-- SECURITY INVOKER (por defecto): corre con RLS del tenant de la sesión.
-- p_regla permite al curador ajustar la redacción al aprobar (mismo límite).
CREATE FUNCTION cm.resolver_nugget(
    p_nugget_id  text,
    p_usuario_id text,
    p_decision   text,
    p_motivo     text DEFAULT NULL,
    p_regla      text DEFAULT NULL
) RETURNS cm.nuggets
    LANGUAGE plpgsql AS $$
DECLARE
    v_tenant  text := core.current_tenant();
    v_ahora   timestamptz := clock_timestamp();
    v_cola    text;
    v_nugget  cm.nuggets;
BEGIN
    IF p_decision IS NULL OR p_decision NOT IN ('aprobado', 'rechazado') THEN
        RAISE EXCEPTION 'cm.resolver_nugget: decisión inválida %', p_decision
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF p_usuario_id IS NULL OR btrim(p_usuario_id) = '' THEN
        RAISE EXCEPTION 'cm.resolver_nugget: la resolución exige el usuario que la toma'
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    SELECT q.id INTO v_cola
      FROM cm.curation_queue q
     WHERE q.tenant_id = v_tenant
       AND q.tipo = 'nugget'
       AND q.estado = 'pendiente'
       AND q.payload @> jsonb_build_object('nugget_id', p_nugget_id)
     ORDER BY q.creado_en DESC
     LIMIT 1
     FOR UPDATE;
    IF v_cola IS NULL THEN
        RAISE EXCEPTION 'cm.resolver_nugget: el nugget % no tiene una revisión pendiente', p_nugget_id
            USING ERRCODE = 'no_data_found';
    END IF;

    UPDATE cm.curation_queue
       SET estado = p_decision,
           revisado_por = p_usuario_id,
           revisado_en = v_ahora,
           payload = payload || jsonb_build_object(
               'motivo', p_motivo,
               'regla_final', COALESCE(p_regla, payload ->> 'regla'))
     WHERE tenant_id = v_tenant AND id = v_cola;

    UPDATE cm.nuggets
       SET estado = p_decision,
           resuelto_por = p_usuario_id,
           resuelto_en = v_ahora,
           motivo_resolucion = p_motivo,
           regla = COALESCE(p_regla, regla)
     WHERE tenant_id = v_tenant AND id = p_nugget_id
    RETURNING * INTO v_nugget;
    IF v_nugget.id IS NULL THEN
        RAISE EXCEPTION 'cm.resolver_nugget: el nugget % no existe', p_nugget_id
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN v_nugget;
END $$;

GRANT EXECUTE ON FUNCTION cm.nugget_regla_max_chars() TO kallicode_app;
GRANT EXECUTE ON FUNCTION cm.resolver_nugget(text, text, text, text, text) TO kallicode_app;
