-- KC-BE-05-5: retirar índice redundante de adjuntos.
--
-- Confirmación y consulta de estado utilizan la clave primaria id.
-- La limpieza de huérfanos ya dispone de attachments_huerfanos_ix.
-- El índice agregado en 040 interfiere con el plan previsto para ese barrido.

DROP INDEX IF EXISTS core.ix_ticket_attachments_estado_pendiente;