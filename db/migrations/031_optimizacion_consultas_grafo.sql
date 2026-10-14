-- kc:sin-transaccion
-- Migración 031: Optimización de consultas recurrentes sobre el grafo (KC-DB-06-3)
-- Índices tenant-first y cubrientes para cm.repo_snapshots, cm.symbols y cm.files.

-- 1. Búsqueda de snapshots por commit y estado con orden temporal
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_repo_snapshots_tenant_commit_estado
    ON cm.repo_snapshots (tenant_id, commit_sha, estado, iniciado_en DESC);

-- 2. Búsqueda directa del último snapshot completo (Index-Only Scan)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_repo_snapshots_ultimo_completo
    ON cm.repo_snapshots (tenant_id, finalizado_en DESC NULLS LAST, iniciado_en DESC)
    INCLUDE (commit_sha)
    WHERE estado = 'completo';

-- 3. JOIN y filtrado recurrente de símbolos por archivo
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_symbols_tenant_file_id
    ON cm.symbols (tenant_id, file_id)
    INCLUDE (id, kind, name);

-- 4. Reconciliación rápida de hashes de archivos sin tocar heap
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_files_tenant_path_sha
    ON cm.files (tenant_id, path)
    INCLUDE (sha256, id);