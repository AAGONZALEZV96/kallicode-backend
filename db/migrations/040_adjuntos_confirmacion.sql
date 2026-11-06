-- KC-BE-05-5: confirmación y análisis de adjuntos.
-- Compatible con esquema base actualizado y con el estado local anterior.

ALTER TABLE core.ticket_attachments
    ADD COLUMN IF NOT EXISTS estado text NOT NULL DEFAULT 'reservado',
    ADD COLUMN IF NOT EXISTS confirmado_en timestamptz,
    ADD COLUMN IF NOT EXISTS motivo_rechazo text;

ALTER TABLE core.ticket_attachments
    DROP CONSTRAINT IF EXISTS att_estado_chk;

UPDATE core.ticket_attachments
   SET estado = 'reservado'
 WHERE estado = 'pendiente'
    OR (estado = 'listo' AND escaneo <> 'limpio');

ALTER TABLE core.ticket_attachments
    ALTER COLUMN estado SET DEFAULT 'reservado';

ALTER TABLE core.ticket_attachments
    ADD CONSTRAINT att_estado_chk
    CHECK (
        estado IN (
            'reservado',
            'pendiente_escaneo',
            'listo',
            'rechazado'
        )
    );

ALTER TABLE core.ticket_attachments
    DROP CONSTRAINT IF EXISTS att_tamano_chk;

ALTER TABLE core.ticket_attachments
    ADD CONSTRAINT att_tamano_chk
    CHECK (tamano_bytes >= 0);

CREATE INDEX IF NOT EXISTS ix_ticket_attachments_estado_pendiente
    ON core.ticket_attachments (tenant_id, estado, creado_en)
    WHERE ticket_id IS NULL;

COMMENT ON COLUMN core.ticket_attachments.confirmado_en IS
'Instante de confirmación de las propiedades reales del objeto almacenado.';

COMMENT ON COLUMN core.ticket_attachments.motivo_rechazo IS
'Código estable del motivo por el cual el adjunto fue rechazado.';