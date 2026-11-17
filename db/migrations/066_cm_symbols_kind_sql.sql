-- ============================================================================
-- KALLICODE · 066_cm_symbols_kind_sql.sql
-- KC-CM-11-3 — Vistas y procedimientos como símbolos del grafo.
--
-- El analizador incorpora la gramática de SQL y extrae vistas, procedimientos y funciones de los archivos .sql
-- (hasta ahora se ingerían sin producir nodos). cm.symbols.kind solo admitía function, method, class, interface,
-- enum y type, así que una vista o un procedimiento violaría symbols_kind_chk y tumbaría la carga de la fase de
-- símbolos. Las funciones SQL ya caben como 'function'.
--
-- Cambio ADITIVO: el conjunto nuevo es un superconjunto del anterior, ninguna fila existente puede violarlo. Se crea
-- NOT VALID y se valida después: la validación toma SHARE UPDATE EXCLUSIVE (no bloquea lecturas ni escrituras), en
-- lugar de recorrer la tabla bajo ACCESS EXCLUSIVE.
--
-- Aditiva y re-ejecutable. Sin índices nuevos. Sin BEGIN/COMMIT: el runner abre la transacción.
-- ============================================================================

SET LOCAL lock_timeout = '5s';
SET LOCAL ROLE kallicode_admin;

ALTER TABLE cm.symbols DROP CONSTRAINT IF EXISTS symbols_kind_chk;
ALTER TABLE cm.symbols
    ADD CONSTRAINT symbols_kind_chk
    CHECK (kind IN ('function','method','class','interface','enum','type','view','procedure')) NOT VALID;
ALTER TABLE cm.symbols VALIDATE CONSTRAINT symbols_kind_chk;
