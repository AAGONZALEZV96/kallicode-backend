-- Ninguna de estas sentencias debe producir aristas USES_TABLE.

-- Una CTE sombrea a la tabla real "products" dentro de su sentencia.
WITH products AS (SELECT 1 AS id)
SELECT * FROM products;

-- Tabla que no existe en el esquema del cliente.
SELECT * FROM tabla_que_no_existe;

-- Función en FROM y FROM dentro de EXTRACT.
SELECT extract(year FROM g.d) FROM generate_series(now(), now(), '1 day') AS g(d);

-- "customers" existe en app y en billing (ninguna en public): ambigua sin esquema.
SELECT * FROM customers;
