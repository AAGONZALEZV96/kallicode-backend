-- KC-BE-05-5: confirmación y tamaño real de adjuntos.

ALTER TABLE core.ticket_attachments
    ADD COLUMN confirmado boolean NOT NULL DEFAULT false;

ALTER TABLE core.ticket_attachments
    DROP CONSTRAINT att_tamano_chk;

ALTER TABLE core.ticket_attachments
    ADD CONSTRAINT att_tamano_chk
    CHECK (tamano_bytes >= 0);

ALTER TABLE core.ticket_attachments
    ADD CONSTRAINT att_confirmado_chk
    CHECK (NOT confirmado OR escaneo = 'limpio');