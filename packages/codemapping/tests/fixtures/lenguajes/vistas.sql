CREATE VIEW reporte.facturas_pagadas AS
SELECT f.id, upper(f.cliente) AS cliente, total_con_iva(f.neto) AS total
FROM facturas f
WHERE f.estado = 'pagada';

CREATE MATERIALIZED VIEW reporte.resumen_mensual AS
SELECT date_trunc('month', creada) AS mes, sum(total) AS total
FROM facturas
GROUP BY 1;

CREATE OR REPLACE FUNCTION reporte.total_con_iva(p_neto numeric)
RETURNS numeric
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN round(p_neto * 1.19, 2);
END;
$$;

CREATE PROCEDURE reporte.cerrar_mes(IN p_mes date)
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE facturas SET cerrada = true WHERE date_trunc('month', creada) = p_mes;
END;
$$;

CREATE TABLE reporte.auditoria (id bigint PRIMARY KEY);
