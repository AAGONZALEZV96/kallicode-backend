# Runbook de Operaciones: Monitoreo, Reindexado y Detach de Particiones (KC-DB-07-6)

## 1. Contexto y Arquitectura
En KalliCode, las tablas de alto volumen y crecimiento cronológico (`audit.audit_events`, `core.usage_tokens`, `core.job_transitions`, etc.) operan particionadas por rango temporal (`creado_en`) administradas con `pg_partman`.

Este runbook define los procedimientos normativos para el equipo de infraestructura y bases de datos.

---

## 2. Monitoreo y Salud de Particiones

### A. Verificar configuración de pg_partman
Para verificar qué tablas están bajo gestión automática y sus intervalos de rotación:

```sql
SELECT 
    parent_table,
    control,
    partition_type,
    partition_interval,
    premake,
    retention,
    infinite_time
FROM partman.part_config;
```

### B. Detección de particiones faltantes (Premake Check)
El job de mantenimiento debe garantizar siempre al menos `premake` particiones futuras creadas. Para consultar las particiones existentes y su rango de fechas:

```sql
SELECT 
    nmsp_parent.nspname AS parent_schema,
    parent.relname AS parent_table,
    nmsp_child.nspname AS child_schema,
    child.relname AS child_partition,
    pg_get_expr(child.relpartbound, child.oid) AS partition_bounds
FROM pg_inherits
JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
JOIN pg_class child ON pg_inherits.inhrelid = child.oid
JOIN pg_namespace nmsp_parent ON nmsp_parent.oid = parent.relnamespace
JOIN pg_namespace nmsp_child ON nmsp_child.oid = child.relnamespace
WHERE parent.relkind = 'p'
ORDER BY parent_table, child_partition;
```

---

## 3. Estrategia de Reindexado (Sin Bloqueos)

### Normativa
- **NUNCA** ejecutar `REINDEX TABLE` sobre la tabla padre particionada en horas pico; esto adquiere un bloqueo exclusivo (`AccessExclusiveLock`) que congela escrituras.
- El reindexado debe ejecutarse **partición por partición**, utilizando `CONCURRENTLY` exclusivamente sobre particiones cerradas/históricas (meses anteriores).

### Procedimiento
1. Identificar la partición hija a reindexar (ejemplo: `audit.audit_events_p2026_09`).
2. Ejecutar la reindexación concurrente:
```sql
REINDEX TABLE CONCURRENTLY audit.audit_events_p2026_09;
```
3. Verificar la validez de los índices reindexados:
```sql
SELECT 
    c.relname AS index_name,
    i.indisvalid AS is_valid
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
WHERE t.relname = 'audit_events_p2026_09';
```

---

## 4. Protocolo de Detach Seguro (Desacople de Retención)

### Normativa
- El desacople de particiones que superan el ciclo de retención definido en `core.retention_policies` debe ejecutarse con `DETACH PARTITION ... CONCURRENTLY`.
- Esto requiere dos transacciones independientes internas en PostgreSQL y no bloquea las consultas concurrentes sobre las particiones activas.

### Procedimiento de Desacople
1. Consultar la política de retención activa para la tabla:
```sql
SELECT retention_days, action
FROM core.retention_policies
WHERE target_table = 'audit.audit_events' AND is_active = true;
```

2. Ejecutar el detach concurrente de la partición vencida:
```sql
ALTER TABLE audit.audit_events 
    DETACH PARTITION audit.audit_events_p2026_01 CONCURRENTLY;
```

3. **Acción post-detach:**
   - Si la acción es `archive`: La partición desacoplada se convierte en una tabla ordinaria independiente (`audit.audit_events_p2026_01`), permitiendo exportarla a almacenamiento en frío (`core.audit_exports` o blobstore) antes de su eliminación física.
   - Si la acción es `drop`:
     ```sql
     DROP TABLE IF EXISTS audit.audit_events_p2026_01;
     ```