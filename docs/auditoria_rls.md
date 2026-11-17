# Auditoría de RLS, privilegios y roles (KC-DB-12-3)

> **Documento generado** por `python db/tools/auditoria_rls.py` desde `core.informe_aislamiento()` y
> `core.informe_politicas_rls()` (migración `069_auditoria_rls.sql`). No se edita a mano: una prueba lo compara
> con el catálogo. Pruebas: `packages/core/tests/integracion/test_auditoria_rls.py` y
> `test_penetracion_tenants.py`.

## 1. Resultado

* **85 tablas** en `core`, `audit`, `vec` y `cm` (sin contar particiones hijas): **67 aisladas** (RLS + FORCE + política con cláusula TO), **18 excepciones justificadas** (sin RLS, con su motivo) y **0 que incumplen**.
* **Todas las 65 tablas con `tenant_id`** están en RLS + FORCE: sí.
* **72 políticas**: 0 que incumplen (sin cláusula `TO` o sin filtrar por tenant sin justificar).
* **BYPASSRLS fuera de `kallicode_admin`** (descontado el superusuario de la instalación): ninguno.
* **Particiones hijas accesibles** para la aplicación o el worker: 0.

## 2. Qué se encontró y se corrigió

La auditoría sobre el esquema real (66 migraciones) encontró cuatro cosas; la migración `069_auditoria_rls.sql` las corrige:

1. **Fuga entre tenants por una partición hija.** `vec.definition_embeddings_default` daba `SELECT`, `INSERT`, `UPDATE` y `DELETE` a
   `kallicode_app` y `kallicode_worker`. Una partición **no hereda las políticas RLS** del padre: con permiso sobre ella, la aplicación leía
   las filas de todos los tenants saltándose el aislamiento. La causa fue `GRANT ... ON ALL TABLES IN SCHEMA vec` de la migración 037,
   ejecutado después de crear la partición; el event trigger `kc_particiones_sin_acceso` solo actúa en el momento del DDL.
   Se revoca el acceso a **todas** las particiones y una prueba lo vigila.
2. **`core.retention_purgas` tenía `tenant_id` y no tenía RLS.** Ahora tiene RLS + FORCE + política por tenant (la aplicación no tiene
   privilegios sobre ella: es defensa en profundidad).
3. **66 políticas sin cláusula `TO`** (valían para `PUBLIC`): `tenant_isolation` en 65 tablas y `plan_prices_solo_lectura`. Ahora todas van
   `TO kallicode_app, kallicode_worker` (`kallicode_dev` hereda de `kallicode_app`).
4. **Escrituras de la aplicación sobre catálogos globales** (`core.user_roles`, `core.pipeline_stages`, `vec.acta_conversion_capa`): venían del
   `DEFAULT PRIVILEGES`, el código no las usa y permitían a un tenant alterar un catálogo compartido. Se revocan.

## 3. Roles

| Rol | Superusuario | BYPASSRLS | CREATEROLE | CREATEDB | Login |
|---|---|---|---|---|---|
| `kallicode_admin` | no | sí | no | no | sí |
| `kallicode_app` | no | no | no | no | sí |
| `kallicode_worker` | no | no | no | no | sí |

Solo `kallicode_admin` (el rol de las migraciones) tiene `BYPASSRLS`. Ni la aplicación ni el worker pueden crear objetos en ningún esquema, ni
tienen `TRUNCATE`, `TRIGGER` ni `REFERENCES` sobre ninguna tabla, ni son dueños de ninguna.

## 4. Tablas

`probatoria` = ni la aplicación ni el worker pueden modificarla ni borrarla (KC-DB-05-2). Los privilegios son los de `kallicode_app` y
`kallicode_worker` sobre la tabla (`-` = ninguno).

| Tabla | tenant_id | RLS | FORCE | Políticas | Sin TO | Privilegios app | Privilegios worker | Probatoria | Estado |
|---|---|---|---|---|---|---|---|---|---|
| `audit.audit_events` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `audit.chain_anchors` | sí | sí | sí | 1 | 0 | SELECT | - | no | aislada |
| `audit.chain_heads` | sí | sí | sí | 1 | 0 | SELECT | - | no | aislada |
| `audit.tenant_deletion_receipts` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `audit.worm_exports` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `cm.concepts` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.curation_queue` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.db_columns` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.db_tables` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.definitions` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.doc_chunk_links` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `cm.edges` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.edge_types` | no | no | no | 0 | 0 | SELECT | SELECT | no | excepcion_justificada |
| `cm.files` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.incident_nodes` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `cm.incidents` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.nodes` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.node_types` | no | no | no | 0 | 0 | SELECT | SELECT | no | excepcion_justificada |
| `cm.nuggets` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.reachability` | sí | sí | sí | 1 | 0 | SELECT,INSERT,DELETE | - | no | aislada |
| `cm.reachability_estado` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.realimentacion_tickets` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE | - | no | aislada |
| `cm.repo_snapshots` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.snapshot_vigente` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.symbols` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.test_flow_covers` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `cm.test_flows` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.audit_exports` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.branch_map` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.connections` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.criteria_test_traceability` | sí | sí | sí | 1 | 0 | SELECT,INSERT | SELECT,INSERT | no | aislada |
| `core.escalations` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.excepciones_aislamiento` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.gate_signatures` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.idempotency_keys` | sí | sí | sí | 2 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `core.invitations` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.jobs` | sí | sí | sí | 2 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT | no | aislada |
| `core.job_transitions` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.llm_steps` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.model_assignments` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.model_providers` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.notifications` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.objetivos_recuperacion` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.onboarding_documents` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.organizations` | no | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.password_reset_tokens` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.pii_ventanas` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.pipeline_stages` | no | no | no | 0 | 0 | SELECT | SELECT | no | excepcion_justificada |
| `core.plan_prices` | no | sí | sí | 1 | 0 | SELECT | SELECT | no | aislada |
| `core.plans` | no | no | no | 0 | 0 | SELECT | - | no | excepcion_justificada |
| `core.politica_recuperacion` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.production_lines` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.purgas_log` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.qa_results` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.qa_runs` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.refresh_tokens` | sí | sí | sí | 2 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,DELETE | no | aislada |
| `core.retencion_dedicada` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.retention_policies` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.retention_purgas` | sí | sí | sí | 1 | 0 | - | - | no | aislada |
| `core.schema_migrations` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `core.security_findings` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.spec_alternatives` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.spec_criteria` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.specs` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.subscriptions` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.test_quarantine` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `core.ticket_attachments` | sí | sí | sí | 2 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,DELETE | no | aislada |
| `core.ticket_counters` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.tickets` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.ticket_watchers` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.upgrade_requests` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.usage_cycles` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.usage_loc` | sí | sí | sí | 1 | 0 | SELECT,INSERT | - | sí | aislada |
| `core.usage_tokens` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT | no | aislada |
| `core.user_roles` | no | no | no | 0 | 0 | SELECT | SELECT | no | excepcion_justificada |
| `core.users` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | - | no | aislada |
| `core.webhook_inbox` | sí | sí | sí | 2 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,UPDATE,DELETE | no | aislada |
| `vec.acta_conversion_capa` | no | no | no | 0 | 0 | SELECT | SELECT | no | excepcion_justificada |
| `vec.definition_embeddings` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `vec.doc_chunks` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `vec.hnsw_catalogo` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `vec.hnsw_metricas` | no | no | no | 0 | 0 | - | - | no | excepcion_justificada |
| `vec.incident_embeddings` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |
| `vec.parametros_busqueda` | no | no | no | 0 | 0 | SELECT | SELECT | no | excepcion_justificada |
| `vec.ticket_embeddings` | sí | sí | sí | 1 | 0 | SELECT,INSERT,UPDATE,DELETE | SELECT,INSERT,UPDATE,DELETE | no | aislada |

## 5. Políticas

| Tabla | Política | Comando | Roles (cláusula TO) | Filtra por tenant | Estado |
|---|---|---|---|---|---|
| `audit.audit_events` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `audit.chain_anchors` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `audit.chain_heads` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.concepts` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.curation_queue` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.db_columns` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.db_tables` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.definitions` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.doc_chunk_links` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.edges` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.files` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.incident_nodes` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.incidents` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.nodes` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.nuggets` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.reachability` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.reachability_estado` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.realimentacion_tickets` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.repo_snapshots` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.snapshot_vigente` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.symbols` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.test_flow_covers` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `cm.test_flows` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.audit_exports` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.branch_map` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.connections` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.criteria_test_traceability` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.escalations` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.gate_signatures` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.idempotency_keys` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.idempotency_keys` | `worker_colas` | ALL | kallicode_worker | no | excepcion_justificada |
| `core.invitations` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.jobs` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.jobs` | `worker_colas` | ALL | kallicode_worker | no | excepcion_justificada |
| `core.job_transitions` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.llm_steps` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.model_assignments` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.model_providers` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.notifications` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.onboarding_documents` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.organizations` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.password_reset_tokens` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.plan_prices` | `plan_prices_solo_lectura` | SELECT | kallicode_app, kallicode_worker | no | excepcion_justificada |
| `core.production_lines` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.qa_results` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.qa_runs` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.refresh_tokens` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.refresh_tokens` | `worker_colas` | ALL | kallicode_worker | no | excepcion_justificada |
| `core.retention_policies` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.retention_purgas` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.security_findings` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.spec_alternatives` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.spec_criteria` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.specs` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.subscriptions` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.test_quarantine` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.ticket_attachments` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.ticket_attachments` | `worker_colas` | ALL | kallicode_worker | no | excepcion_justificada |
| `core.ticket_counters` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.tickets` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.ticket_watchers` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.upgrade_requests` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.usage_cycles` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.usage_loc` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.usage_tokens` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.users` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.webhook_inbox` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `core.webhook_inbox` | `worker_colas` | ALL | kallicode_worker | no | excepcion_justificada |
| `vec.definition_embeddings` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `vec.doc_chunks` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `vec.incident_embeddings` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |
| `vec.ticket_embeddings` | `tenant_isolation` | ALL | kallicode_app, kallicode_worker | sí | filtra por tenant |

## 6. Excepciones justificadas

### Tablas sin RLS

| Tabla | Por qué es seguro |
|---|---|
| `audit.tenant_deletion_receipts` | Actas de eliminación de un tenant, con el tenant ya pseudonimizado (hash): sobreviven al borrado del tenant y no llevan su identificador. Solo kallicode_admin accede. |
| `audit.worm_exports` | Comprobantes de export WORM de la auditoría (append-only). Cada fila resume varios tenants de una partición, así que no pertenece a uno solo. Solo kallicode_admin accede. |
| `cm.edge_types` | Catálogo global de tipos de arista del grafo, sembrado por migraciones. Lectura para la aplicación y el worker; sin datos de ningún tenant. |
| `cm.node_types` | Catálogo global de tipos de nodo del grafo, sembrado por migraciones. Lectura para la aplicación y el worker; sin datos de ningún tenant. |
| `core.excepciones_aislamiento` | Esta misma tabla de justificaciones: es de gobierno de la plataforma, sin datos de tenants. Solo kallicode_admin accede. |
| `core.objetivos_recuperacion` | Objetivos RPO/RTO de la plataforma y su aprobación (KC-DB-11-1): gobierno, sin datos de tenants. Solo kallicode_admin accede. |
| `core.pii_ventanas` | Ventanas de minimización de PII por regla (KC-DB-10-3): configuración de la plataforma, sin datos de tenants. Solo kallicode_admin accede. |
| `core.pipeline_stages` | Catálogo global de etapas del pipeline (migración 049). Lectura para la aplicación y el worker; las escrituras se revocaron en KC-DB-12-3. |
| `core.plans` | Catálogo global de planes comerciales. Solo lectura para la aplicación; sin datos de tenants. |
| `core.politica_recuperacion` | Política de recuperación por tabla (KC-DB-11-4): gobierno de la plataforma, sin datos de tenants. Solo kallicode_admin accede. |
| `core.purgas_log` | Registro de ejecuciones de las purgas por TTL y de la minimización de PII (KC-DB-10-3): solo tarea y número de filas, sin identificadores de tenant. Solo kallicode_admin accede. |
| `core.retencion_dedicada` | Tablas purgadas fuera del motor de retención con su plazo (KC-DB-10-4): configuración de la plataforma, sin datos de tenants. Solo kallicode_admin accede. |
| `core.schema_migrations` | Ledger de migraciones del esquema (sha256, fecha, etiqueta de versión): solo kallicode_admin lo escribe; la aplicación y el worker solo pueden leerlo vía core.version_esquema(). |
| `core.user_roles` | Catálogo global de roles de usuario (migración 049). Lectura para la aplicación y el worker; las escrituras se revocaron en KC-DB-12-3. |
| `vec.acta_conversion_capa` | Acta histórica de la conversión de capas de embeddings (KC-DB-04-4): datos de migración, sin identificadores de tenant. Lectura; las escrituras se revocaron en KC-DB-12-3. |
| `vec.hnsw_catalogo` | Definición de los índices HNSW (KC-DB-08-2): configuración de operación, sin datos de tenants. Solo kallicode_admin accede. |
| `vec.hnsw_metricas` | Mediciones de tamaño y duración de los índices HNSW: operación, sin datos de tenants. Solo kallicode_admin accede. |
| `vec.parametros_busqueda` | Parámetros de la búsqueda vectorial por caso de uso (ef_search, etc.): configuración de la plataforma. Lectura para la aplicación y el worker. |

### Políticas que no filtran por tenant

| Política | Por qué es seguro |
|---|---|
| `core.idempotency_keys#worker_colas` | La purga de claves de idempotencia y el worker de ingesta operan sobre todos los tenants (migraciones 010 y 054): solo kallicode_worker. |
| `core.jobs#worker_colas` | El planificador del worker lee los jobs pendientes de todos los tenants (migración 010): solo kallicode_worker y solo SELECT. |
| `core.plan_prices#plan_prices_solo_lectura` | Lista de precios de la plataforma: el mismo dato para todos los tenants, solo lectura (FOR SELECT) y sin columna de tenant. |
| `core.refresh_tokens#worker_colas` | La purga de tokens vencidos del worker barre todos los tenants (migración 010): solo kallicode_worker y solo SELECT y DELETE. |
| `core.ticket_attachments#worker_colas` | La purga de adjuntos huérfanos del worker barre todos los tenants (migración 010): solo kallicode_worker y solo SELECT y DELETE. |
| `core.webhook_inbox#worker_colas` | El worker barre la cola de webhooks de TODOS los tenants (migración 010): solo kallicode_worker, por política y no por BYPASSRLS; las operaciones las limita el GRANT (SELECT, UPDATE, DELETE). |

## 7. Límites de esta auditoría

* **Las dos políticas de `pg_cron`** (`cron.job` y `cron.job_run_details`, filtran por `CURRENT_USER`) pertenecen a la extensión y están
  fuera de `core`, `audit`, `vec` y `cm`: no se tocan ni se incluyen aquí.
* **El aislamiento protege contra errores de consulta, no contra un cliente con SQL arbitrario.** El tenant de la sesión lo fija la propia
  aplicación (`app.tenant_id`); quien pudiera ejecutar SQL libre con el rol de la aplicación podría fijarlo a otro valor. La defensa es no
  exponer SQL libre (inyección) y `kallicode_admin`/`postgres` fuera del alcance de la aplicación.
* **El superusuario `postgres` de la instalación** tiene `BYPASSRLS` por ser superusuario: no se usa desde la aplicación.
* **Una tabla nueva en `core` o `vec` recibe `SELECT/INSERT/UPDATE/DELETE` para la aplicación por `DEFAULT PRIVILEGES`.** Por eso una tabla
  nueva sin RLS y sin excepción aparece aquí como `INCUMPLE` y la prueba falla hasta que se decida.
* Es una auditoría automática sobre un esquema de demostración, **no una revisión externa**; su aprobación por el responsable de
  seguridad no está dada por este documento.
