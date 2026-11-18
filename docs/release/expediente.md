# Expediente de release GA

Índice de las evidencias que acompañan a la release GA. La aceptación del expediente es de KC-DB-12-5
(ensayo de migración en staging y smoke de release). Cada ticket que aporta una evidencia la enlaza aquí.

## CodeMapping

| Evidencia | Ticket | Documento |
|---|---|---|
| Carga y resiliencia de la capa de consulta | KC-CM-11-5 / KC-CM-11-6 | [docs/rendimiento/carga-cm.md](../rendimiento/carga-cm.md) |
| Calidad semántica del grafo: precisión y exhaustividad sobre 50 preguntas | KC-CM-12-4 | [docs/calidad/informe-calidad-cm.md](../calidad/informe-calidad-cm.md) |
| Contrato de consultas congelado (`cm-grafo-v1` / `cm-consulta-v1`), nota de release y presupuestos p95 | KC-CM-12-5 | [docs/release/contrato_cm_queries_v1.md](contrato_cm_queries_v1.md) |
## Base de datos y seguridad

| Evidencia | Ticket | Documento |
|---|---|---|
| Esquema v2 congelado y política de cambios post-GA (versión, instantánea del catálogo, smoke post-deploy) | KC-DB-12-1 | [docs/politica-cambios-esquema.md](../politica-cambios-esquema.md) |
| Modelo de datos generado desde `pg_catalog` (COMMENT en todo el esquema) | KC-DB-12-2 | [docs/modelo_datos_v2.md](../modelo_datos_v2.md) |
| Auditoría final de RLS, privilegios y roles, y prueba de penetración de datos (seis vías) | KC-DB-12-3 | [docs/auditoria_rls.md](../auditoria_rls.md) |
| Recuperación a un punto en el tiempo: RPO/RTO y acta del ensayo | KC-DB-11-1 | [docs/recuperacion_pitr.md](../recuperacion_pitr.md) · [acta](../recuperacion_pitr_acta.md) |
| Runbook de incidentes de base de datos y acta de los seis ensayos | KC-DB-11-5 | [docs/runbook_incidentes_db.md](../runbook_incidentes_db.md) · [acta](../runbook_incidentes_db_acta.md) |
| Procedimiento de despliegue del esquema, smoke de release y acta del ensayo de migración | KC-DB-12-5 | [docs/aceptacion_ga_esquema.md](../aceptacion_ga_esquema.md) · [acta](../aceptacion_ga_acta.md) |

## Estado de la aceptación (KC-DB-12-5)

La evidencia técnica está reunida; **la aceptación no está firmada**. Cada firma es de una persona y este expediente no las da por hechas.

| Aceptación | Firma | Estado |
|---|---|---|
| Esquema desplegado y verificado (DBA) | Anderson González | **pendiente** |
| Product Owner | _pendiente_ | **pendiente** |
| Architecture Board (incluye aprobar los objetivos RPO/RTO, hoy «propuesto», y la política de cambios, hoy «propuesta») | _pendiente_ | **pendiente** |
| Tech Lead de Testing | _pendiente_ | **pendiente** |

Puntos que el ensayo deja abiertos y que no son de este expediente: el ensayo se hizo sobre un **staging simulado en Docker**, no sobre el staging real;
el presupuesto de migraciones es una **propuesta**; y siguen abiertos el HNSW a 10⁶ filas en un servidor dimensionado (KC-DB-11-4) y la conexión de las alertas
(`core.alertas_db()`) a un canal de guardia (KC-DB-11-5).
