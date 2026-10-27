-- Reportes de facturación: CTEs simples, múltiples, recursivas y anidadas.
-- Un ';' dentro de un comentario no debe cortar sentencias: SELECT 1;

WITH ventas_mes AS (
    SELECT date_trunc('month', emitida_en) AS mes, sum(monto) AS total
      FROM facturas
     WHERE estado = 'ISSUED'
     GROUP BY 1
)
SELECT mes, total,
       total - lag(total) OVER (ORDER BY mes) AS variacion
  FROM ventas_mes
 ORDER BY mes;

WITH clientes_activos AS (
    SELECT id, nombre FROM clientes WHERE activo
),
deuda AS (
    SELECT cliente_id, sum(monto) AS saldo
      FROM facturas
     WHERE pagada_en IS NULL
     GROUP BY cliente_id
)
SELECT c.nombre, coalesce(d.saldo, 0) AS saldo
  FROM clientes_activos c
  LEFT JOIN deuda d ON d.cliente_id = c.id;

WITH RECURSIVE jerarquia AS (
    SELECT id, padre_id, nombre, 1 AS nivel
      FROM centros_costo
     WHERE padre_id IS NULL
    UNION ALL
    SELECT h.id, h.padre_id, h.nombre, j.nivel + 1
      FROM centros_costo h
      JOIN jerarquia j ON h.padre_id = j.id
)
SELECT * FROM jerarquia ORDER BY nivel, nombre;

SELECT resumen.anio, resumen.total
  FROM (
        WITH base AS (SELECT extract(year FROM emitida_en) AS anio, monto FROM facturas)
        SELECT anio, sum(monto) AS total FROM base GROUP BY anio
       ) AS resumen;

CREATE OR REPLACE FUNCTION reporte.total_cliente(p_cliente text)
RETURNS numeric
LANGUAGE plpgsql
AS $$
DECLARE
    v_total numeric;
BEGIN
    WITH f AS (SELECT monto FROM facturas WHERE cliente_id = p_cliente)
    SELECT coalesce(sum(monto), 0) INTO v_total FROM f;
    RETURN v_total;
END;
$$;
