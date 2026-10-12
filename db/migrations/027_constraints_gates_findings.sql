-- Migración 027: Constraints en gate_signatures y security_findings (KC-DB-05-5)
-- Refuerza la integridad en firmas de compuertas y hallazgos de seguridad.

SET lock_timeout = '5s';
SET statement_timeout = '30s';



-- 1. Restricciones en core.gate_signatures
-- Si accion = 'cambios_pedidos', devolver_a y comentario son obligatorios.
-- Si accion = 'aprobado', devolver_a debe ser nulo.
ALTER TABLE core.gate_signatures
    ADD CONSTRAINT gs_accion_coherencia_chk
    CHECK (
        (accion = 'aprobado' AND devolver_a IS NULL)
        OR
        (accion = 'cambios_pedidos' AND devolver_a IS NOT NULL AND comentario IS NOT NULL AND length(trim(comentario)) > 0)
    );

-- Sello no puede ser una cadena vacía o solo espacios
ALTER TABLE core.gate_signatures
    ADD CONSTRAINT gs_sello_formato_chk
    CHECK (length(trim(sello)) > 0);

-- Comentario no puede ser una cadena de solo espacios
ALTER TABLE core.gate_signatures
    ADD CONSTRAINT gs_comentario_no_vacio_chk
    CHECK (comentario IS NULL OR length(trim(comentario)) > 0);


-- 2. Restricciones en core.security_findings
-- Línea de código positiva si existe
ALTER TABLE core.security_findings
    ADD CONSTRAINT sf_linea_pos_chk
    CHECK (linea IS NULL OR linea > 0);

-- Título no vacío
ALTER TABLE core.security_findings
    ADD CONSTRAINT sf_titulo_no_vacio_chk
    CHECK (length(trim(titulo)) > 0);

