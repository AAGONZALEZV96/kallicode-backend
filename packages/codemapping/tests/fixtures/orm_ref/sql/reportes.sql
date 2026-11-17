-- Ventas por cliente con CTE encadenadas: "ventas" y "top" no son tablas.
WITH ventas AS (
    SELECT o.customer_id, sum(oi.quantity * oi.unit_price) AS total
      FROM public.orders o
      JOIN order_items oi ON oi.order_id = o.id
     GROUP BY o.customer_id
), top AS (
    SELECT v.* FROM ventas v ORDER BY v.total DESC LIMIT 10
)
SELECT c.name, t.total
  FROM top t
  JOIN app.customers c ON c.id = t.customer_id;

CREATE VIEW inventory.v_stock AS
SELECT w.name, sum(sm.delta) AS stock
  FROM inventory.warehouses w
  LEFT JOIN inventory.stock_movements sm ON sm.warehouse_id = w.id
 GROUP BY w.name;
