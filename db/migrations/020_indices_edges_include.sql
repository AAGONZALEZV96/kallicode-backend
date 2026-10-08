-- kc:sin-transaccion
CREATE INDEX CONCURRENTLY IF NOT EXISTS edges_out_ix ON cm.edges (tenant_id, src_id, tipo, dst_id) INCLUDE (src_kind, dst_kind, props);
CREATE INDEX CONCURRENTLY IF NOT EXISTS edges_in_ix ON cm.edges (tenant_id, dst_id, tipo, src_id) INCLUDE (src_kind, dst_kind, props);