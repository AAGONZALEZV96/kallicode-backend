# Plan de índices

KC-DB-06-1 · inventario de consultas críticas y plan de índices justificado

> **Estado de la evidencia: inventario PROVISIONAL.** El inventario de consultas por endpoint lo
> entrega Backend en KC-BE-06-1, que todavía no existe. Mientras tanto, el inventario de este
> documento (Anexo A) se extrajo leyendo el SQL de la API, los servicios, los workers y CodeMapping
> con `db/tools/inventario_consultas.py`. Cuando Backend publique el suyo, se reemplaza el Anexo A y
> se revisan las asociaciones índice-consulta de la sección 4.

Alcance: este documento **no cambia nada en la base**. Decide qué índice sirve a qué consulta, cuál
no lo usa nadie y qué falta. Ejecutar los cambios corresponde a los tres tickets siguientes.

## 1. Resumen

- La base tiene **113 índices explícitos** (los creados con `CREATE INDEX`) en los esquemas `core`, `vec`,
  `audit` y `cm`. Otros 80 respaldan una `PRIMARY KEY` o `UNIQUE` y no se pueden retirar por separado: se
  justifican por su restricción y no entran en este plan. (Los 50 índices de `db/02_indexes.sql` son solo una
  parte: las migraciones posteriores añadieron el resto.)
- **16 candidatos a retiro**: 3 redundantes (otro índice o restricción ya los
  cubre), 12 sin ninguna consulta ni restricción que los use y
  1 índice vectorial sin consumidor. Están listados en la sección 5.
- **1 índice recomendado que falta**: `vec.doc_chunks (tenant_id, documento_id)` (sección 6).
- Las 15 consultas más costosas de la medición de ejemplo están en la sección 7, con su plan y un
  presupuesto p95 propuesto.

| Categoría | Índices | Significado |
|---|---|---|
| `consulta` | 33 | Lo usa al menos una consulta del inventario. |
| `soporte-fk` | 44 | Cubre una clave foránea compuesta: sin él, borrar o validar el padre recorre la tabla hija. |
| `unicidad` | 11 | Hace cumplir una regla de unicidad del negocio. |
| `funcion-sql` | 6 | Lo usa una función SQL o un proceso interno, no un endpoint. |
| `vectorial` | 3 | Índice HNSW de similitud sobre embeddings. |
| `reserva` | 1 | Aún sin consumidor, pero lo exige un ticket entregado. |
| `redundante` | 3 | Otro índice o restricción de la misma tabla ya lo cubre. |
| `sin-consulta` | 12 | Ninguna consulta del inventario lo usa y ninguna restricción depende de él. |

## 2. Qué evidencia hay y qué no

Leer esto antes de actuar sobre cualquier candidato:

- **La asociación índice-consulta sale del código, no de mediciones en producción.** Es una aproximación
  estática (Anexo A). No ve el SQL que se arma dinámicamente más allá de los fragmentos literales; los
  filtros dinámicos de `GET /tickets`, `GET /users` y `GET /audit/events` se revisaron a mano.
- **La medición de ejemplo usa una carga de pruebas sobre tablas diminutas.** En ella, 32 de los
  113 índices registraron escaneos y 81 no. Con tablas de pocas filas el planificador
  prefiere recorrerlas enteras, así que **un índice sin escaneos aquí no es evidencia de que sobre**. Por eso el
  criterio de retiro no es «cero escaneos»: es «ninguna consulta del inventario lo usa y ninguna restricción
  depende de él», o «otro índice lo cubre».
- Los candidatos son **propuestas para los tickets siguientes**, que deben confirmarlas con el inventario de
  Backend y con volumen realista antes de borrar nada.
- Los índices de `cm.*` y `vec.*` los consultan CodeMapping y los procesos de grafo, no los endpoints de la API; su
  justificación se apoya en las funciones SQL y en el código de CodeMapping.

## 3. Cómo se mide

- **Informe:** `db/tools/informe_indices.sql`. Lee `pg_stat_user_indexes` y `pg_stat_statements` y entrega:
  el uso de cada índice, los candidatos sin escaneos, las claves foráneas sin índice que las cubra, las 15
  consultas más costosas de la aplicación (solo sentencias de nivel superior de `kallicode_app`), su plan
  genérico capturado **como `kallicode_app`** (incluye las políticas RLS) y un p95 propuesto.
- **p95 propuesto:** `pg_stat_statements` no entrega percentiles. Se usa media + 2 desviaciones, redondeado
  hacia arriba a múltiplos de 5 ms, con un mínimo de 5 ms. Es una **aproximación inicial** que debe validarse
  con carga real.
- **Automático:** el workflow `.github/workflows/indices_nocturno.yml` levanta PostgreSQL con
  `pg_stat_statements` cargada, aplica el esquema, ejecuta la carga (los tests de la API y de integración),
  genera el informe y lo publica como artefacto `informe-indices`.
- **A mano:**
  ```bash
  psql -d <base> -v ON_ERROR_STOP=1 -v tenant_plan=org_demo -f db/tools/informe_indices.sql
  python db/tools/inventario_consultas.py        # inventario provisional en Markdown
  ```
- **Entorno de demostración:** `docker-compose.yml` arranca Postgres con
  `shared_preload_libraries=pg_stat_statements` y `db/00_extensiones.sql` crea la extensión.

## 4. Plan de índices

Estado: `activo` (se mantiene) o `candidato_a_retiro` (propuesto para retirar, con su justificación).
La columna «Consultas» cita los identificadores del Anexo A. **El test
`test_indices_documentados.py` falla si la base tiene un índice que no está en esta tabla** (y si esta
tabla cita uno que no existe): un índice nuevo debe añadirse aquí en la misma migración que lo crea.

<!-- plan-indices:inicio -->
| Índice | Tabla | Claves | Categoría | Justificación | Consultas | Estado |
|---|---|---|---|---|---|---|
| `audit.audit_actor_ix` | `audit_events` | tenant_id,actor_tipo,creado_en DESC | `consulta` | Filtro `actor_tipo` de `GET /audit/events`. | Q-005, Q-006, Q-007 | `activo` |
| `audit.audit_evento_ix` | `audit_events` | tenant_id,evento | `sin-consulta` | Ningún endpoint filtra por `evento`: `GET /audit/events` filtra por ticket, etapa, actor y fechas. Sin escaneos en la medición. | - | `candidato_a_retiro` |
| `audit.audit_events_request_id_ix` | `audit_events` | tenant_id,request_id · parcial | `reserva` | Correlación con el log central por `request_id` (KC-DB-05-6). La API todavía no propaga el `request_id`, así que aún no tiene consumidor. | - | `activo` |
| `audit.audit_events_sello_ix` | `audit_events` | tenant_id,sello | `funcion-sql` | `audit.verify_chain` resuelve el eslabón anterior por `(tenant_id, sello)` (KC-DB-05-1). | - | `activo` |
| `audit.audit_tenant_fecha_ix` | `audit_events` | tenant_id,creado_en DESC | `consulta` | Rango de fechas y conteo del día en `GET /audit/events`. | Q-005, Q-006, Q-007 | `activo` |
| `audit.audit_ticket_ix` | `audit_events` | tenant_id,ticket_id,creado_en | `consulta` | Eventos de un ticket: `GET /tickets/{numero}/activity` y el expediente. | Q-005, Q-006, Q-007, Q-164, Q-253 | `activo` |
| `cm.concepts_snapshot_idx` | `concepts` | tenant_id,snapshot_id | `funcion-sql` | `cm.stats_snapshot` y `cm.retirar_snapshot` filtran por `snapshot_id` (KC-DB-04-3). | - | `activo` |
| `cm.db_tables_snapshot_idx` | `db_tables` | tenant_id,snapshot_id | `funcion-sql` | `cm.stats_snapshot` y `cm.retirar_snapshot` filtran por `snapshot_id` (KC-DB-04-3). | - | `activo` |
| `cm.definitions_snapshot_idx` | `definitions` | tenant_id,snapshot_id | `funcion-sql` | `cm.stats_snapshot` y `cm.retirar_snapshot` filtran por `snapshot_id` (KC-DB-04-3). | - | `activo` |
| `cm.edges_in_ix` | `edges` | tenant_id,dst_id,tipo,src_id +INCLUDE(src_kind,dst_kind,props) | `consulta` | Recorrido del grafo por destino con `INCLUDE` para índice-solo (KC-DB-04-2); el cargador de CodeMapping consulta aristas por `dst_id`. | Q-227, Q-228, Q-229, Q-239 | `activo` |
| `cm.edges_out_ix` | `edges` | tenant_id,src_id,tipo,dst_id +INCLUDE(src_kind,dst_kind,props) | `consulta` | Recorrido del grafo por origen con `INCLUDE` para índice-solo (KC-DB-04-2); el cargador de CodeMapping consulta aristas por `src_id`. | Q-227, Q-228, Q-229, Q-239 | `activo` |
| `cm.edges_snapshot_idx` | `edges` | tenant_id,snapshot_id | `funcion-sql` | `cm.stats_snapshot` y `cm.retirar_snapshot` filtran por `snapshot_id` (KC-DB-04-3). | - | `activo` |
| `cm.files_snapshot_idx` | `files` | tenant_id,snapshot_id | `funcion-sql` | `cm.stats_snapshot` y `cm.retirar_snapshot` filtran por `snapshot_id` (KC-DB-04-3). | - | `activo` |
| `cm.idx_files_tenant_path_sha` | `files` | tenant_id,path +INCLUDE(sha256,id) | `consulta` | Cubre `(tenant_id, path)` con `INCLUDE (sha256, id)` para el refresco incremental. Coincide en claves con `files_tenant_id_path_key`, pero aporta el índice-solo. | Q-228, Q-233, Q-234, Q-237, Q-241 | `activo` |
| `cm.nodes_kind_idx` | `nodes` | tenant_id,kind | `sin-consulta` | Creado en KC-DB-04-4 para consultas por `kind`; ninguna consulta actual filtra `cm.nodes` por `kind`. | - | `candidato_a_retiro` |
| `cm.idx_repo_snapshots_tenant_commit_estado` | `repo_snapshots` | tenant_id,commit_sha,estado,iniciado_en DESC | `consulta` | Búsqueda de un snapshot por commit y estado al reanudar una ingesta. | Q-222, Q-223, Q-224, Q-225, Q-226, Q-232, Q-242, Q-243 (+7) | `activo` |
| `cm.idx_repo_snapshots_ultimo_completo` | `repo_snapshots` | tenant_id,finalizado_en DESC NULLS LAST,iniciado_en DESC +INCLUDE(commit_sha) · parcial | `consulta` | Último snapshot completado de un tenant (`get_last_sha`). | Q-222, Q-223, Q-224, Q-225, Q-226, Q-232, Q-242, Q-243 (+7) | `activo` |
| `cm.idx_symbols_tenant_file_id` | `symbols` | tenant_id,file_id +INCLUDE(id,kind,name) | `consulta` | Símbolos de un archivo con `INCLUDE (id, kind, name)`. | Q-228, Q-230, Q-234, Q-238 | `activo` |
| `core.audit_exports_tenant_ix` | `audit_exports` | tenant_id,creado_en DESC | `sin-consulta` | Ninguna consulta lista las exportaciones por fecha: solo se consulta una por id y se cuentan las en curso por estado. | - | `candidato_a_retiro` |
| `core.idx_audit_exports_tenant_solicitado_por` | `audit_exports` | tenant_id,solicitado_por | `soporte-fk` | Cubre la FK `audit_exports_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_audit_exports_tenant_ticket_id` | `audit_exports` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `audit_exports_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.connections_tenant_ix` | `connections` | tenant_id,categoria | `soporte-fk` | Cubre la FK `connections_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-041, Q-062 | `activo` |
| `core.escalations_abierta_uq` | `escalations` | job_id,COALESCE(paso,'-'::text) · parcial | `unicidad` | Una sola escalación abierta por job y paso. | Q-127, Q-128 | `activo` |
| `core.escalations_abiertas_ix` | `escalations` | tenant_id,severidad,creado_en · parcial | `sin-consulta` | Ningún endpoint lista las escalaciones abiertas por severidad. | - | `candidato_a_retiro` |
| `core.idx_escalations_tenant_job_id` | `escalations` | tenant_id,job_id | `soporte-fk` | Cubre la FK `escalations_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-127 | `activo` |
| `core.gate_aprobado_uq` | `gate_signatures` | ticket_id,gate · parcial | `unicidad` | Un solo gate aprobado por ticket y número de gate; las consultas de gates lo recorren por `ticket_id`. | Q-073, Q-081, Q-084, Q-118 | `activo` |
| `core.gates_actor_ix` | `gate_signatures` | actor_id,creado_en DESC | `sin-consulta` | Ninguna consulta filtra por `actor_id`; la FK ya está cubierta por `idx_gate_signatures_tenant_actor_id`. | - | `candidato_a_retiro` |
| `core.gates_ticket_ix` | `gate_signatures` | ticket_id,gate,creado_en | `consulta` | Sirve a las consultas citadas. | Q-010, Q-073, Q-118 | `activo` |
| `core.idx_gate_signatures_tenant_actor_id` | `gate_signatures` | tenant_id,actor_id | `soporte-fk` | Cubre la FK `gate_signatures_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_gate_signatures_tenant_spec_id` | `gate_signatures` | tenant_id,spec_id | `soporte-fk` | Cubre la FK `gate_signatures_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_gate_signatures_tenant_ticket_id` | `gate_signatures` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `gate_signatures_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-010, Q-073, Q-118 | `activo` |
| `core.idempotency_purga_ix` | `idempotency_keys` | creado_en | `consulta` | Sirve a las consultas citadas. | Q-206 | `activo` |
| `core.idx_invitations_tenant_invitado_por` | `invitations` | tenant_id,invitado_por | `soporte-fk` | Cubre la FK `invitations_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.invitations_pendiente_uq` | `invitations` | tenant_id,email · parcial | `unicidad` | Una sola invitación enviada por tenant y correo. | Q-035, Q-039, Q-183, Q-184 | `activo` |
| `core.invitations_tenant_ix` | `invitations` | tenant_id,estado | `soporte-fk` | Cubre la FK `invitations_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-035, Q-183, Q-256 | `activo` |
| `core.idx_job_transitions_tenant_job_id` | `job_transitions` | tenant_id,job_id | `soporte-fk` | Cubre la FK `job_transitions_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.job_transitions_job_ix` | `job_transitions` | job_id,creado_en | `sin-consulta` | Ninguna consulta lee las transiciones de un job; la FK ya está cubierta por `idx_job_transitions_tenant_job_id`. | - | `candidato_a_retiro` |
| `core.idx_jobs_tenant_ticket_id` | `jobs` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `jobs_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-071, Q-101, Q-107, Q-114, Q-116, Q-120, Q-123, Q-150 (+1) | `activo` |
| `core.jobs_activo_por_ticket_uq` | `jobs` | ticket_id · parcial | `unicidad` | Un solo job activo por ticket; además sirve las búsquedas de job por `ticket_id`. | Q-101, Q-107, Q-114, Q-116, Q-120, Q-123, Q-150 | `activo` |
| `core.jobs_linea_ocupada_uq` | `jobs` | tenant_id,linea · parcial | `unicidad` | Una línea de producción no puede tener dos jobs activos. | - | `activo` |
| `core.jobs_pendientes_ix` | `jobs` | tenant_id,prioridad,creado_en · parcial | `consulta` | Sirve a las consultas citadas. | Q-218 | `activo` |
| `core.jobs_tenant_estado_ix` | `jobs` | tenant_id,estado | `soporte-fk` | Cubre la FK `jobs_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-055, Q-068, Q-202, Q-217, Q-218 | `activo` |
| `core.idx_llm_steps_tenant_job_id` | `llm_steps` | tenant_id,job_id | `soporte-fk` | Cubre la FK `llm_steps_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_llm_steps_tenant_ticket_id` | `llm_steps` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `llm_steps_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-011 | `activo` |
| `core.llm_steps_job_ix` | `llm_steps` | job_id,creado_en | `sin-consulta` | Ninguna consulta lee los pasos de un job; la FK ya está cubierta por `idx_llm_steps_tenant_job_id`. | - | `candidato_a_retiro` |
| `core.llm_steps_ticket_etapa_ix` | `llm_steps` | ticket_id,etapa | `consulta` | Sirve a las consultas citadas. | Q-011 | `activo` |
| `core.idx_model_assignments_tenant_prov_fb` | `model_assignments` | tenant_id,fallback_proveedor | `soporte-fk` | Cubre la FK `model_assignments_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_model_assignments_tenant_prov_ppal` | `model_assignments` | tenant_id,principal_proveedor | `soporte-fk` | Cubre la FK `model_assignments_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_notifications_tenant_ticket_id` | `notifications` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `notifications_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_notifications_tenant_user_id` | `notifications` | tenant_id,user_id | `soporte-fk` | Cubre la FK `notifications_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-132, Q-133, Q-134 | `activo` |
| `core.notifications_noleidas_ix` | `notifications` | user_id · parcial | `consulta` | Sirve a las consultas citadas. | Q-132, Q-133, Q-134 | `activo` |
| `core.notifications_user_ix` | `notifications` | user_id,creada_en DESC | `consulta` | Sirve a las consultas citadas. | Q-132, Q-133, Q-134 | `activo` |
| `core.idx_onboarding_documents_tenant_subido_por` | `onboarding_documents` | tenant_id,subido_por | `soporte-fk` | Cubre la FK `onboarding_documents_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.onboarding_docs_hash_uq` | `onboarding_documents` | tenant_id,hash_contenido · parcial | `unicidad` | Evita subir dos veces el mismo contenido en un tenant (`hash_contenido`). | Q-057 | `activo` |
| `core.idx_password_reset_tokens_tenant_user_id` | `password_reset_tokens` | tenant_id,user_id | `soporte-fk` | Cubre la FK `password_reset_tokens_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_production_lines_tenant_job_id` | `production_lines` | tenant_id,job_id | `soporte-fk` | Cubre la FK `production_lines_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_qa_results_tenant_qa_run_id` | `qa_results` | tenant_id,qa_run_id | `soporte-fk` | Cubre la FK `qa_results_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-079, Q-160 | `activo` |
| `core.idx_qa_runs_tenant_spec_id` | `qa_runs` | tenant_id,spec_id | `soporte-fk` | Cubre la FK `qa_runs_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_qa_runs_tenant_ticket_id` | `qa_runs` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `qa_runs_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-078, Q-091 | `activo` |
| `core.qa_runs_ticket_ix` | `qa_runs` | ticket_id,corrida DESC | `redundante` | Cubierto por la restricción única `qa_corrida_uq (ticket_id, corrida)`: Postgres la recorre al revés para `corrida DESC`. | - | `candidato_a_retiro` |
| `core.idx_refresh_tokens_tenant_user_id` | `refresh_tokens` | tenant_id,user_id | `soporte-fk` | Cubre la FK `refresh_tokens_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-022, Q-026, Q-034, Q-177, Q-189 | `activo` |
| `core.refresh_tokens_expira_ix` | `refresh_tokens` | expira_en | `consulta` | Sirve a las consultas citadas. | Q-205 | `activo` |
| `core.refresh_tokens_user_ix` | `refresh_tokens` | user_id · parcial | `consulta` | Sirve a las consultas citadas. | Q-022, Q-026, Q-034, Q-177, Q-189 | `activo` |
| `core.findings_bloqueantes_ix` | `security_findings` | tenant_id,ticket_id · parcial | `consulta` | Sirve a las consultas citadas. | Q-080, Q-119, Q-161 | `activo` |
| `core.findings_ticket_ix` | `security_findings` | ticket_id | `consulta` | Sirve a las consultas citadas. | Q-080, Q-119, Q-161 | `activo` |
| `core.idx_security_findings_tenant_ticket_id` | `security_findings` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `security_findings_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-080, Q-119, Q-161 | `activo` |
| `core.idx_spec_alternatives_tenant_spec_id` | `spec_alternatives` | tenant_id,spec_id | `soporte-fk` | Cubre la FK `spec_alternatives_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-075, Q-076, Q-157 | `activo` |
| `core.spec_alt_elegida_uq` | `spec_alternatives` | spec_id · parcial | `unicidad` | Regla de unicidad del negocio. | Q-075, Q-076, Q-157 | `activo` |
| `core.spec_alt_recomendada_uq` | `spec_alternatives` | spec_id · parcial | `unicidad` | Regla de unicidad del negocio. | Q-075, Q-076, Q-157 | `activo` |
| `core.idx_spec_criteria_tenant_spec_id` | `spec_criteria` | tenant_id,spec_id | `soporte-fk` | Cubre la FK `spec_criteria_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-111, Q-158 | `activo` |
| `core.spec_criteria_tenant_ix` | `spec_criteria` | tenant_id | `redundante` | Cubierto por `idx_spec_criteria_tenant_spec_id (tenant_id, spec_id)`. | - | `candidato_a_retiro` |
| `core.idx_specs_tenant_ticket_id` | `specs` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `specs_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-074, Q-090, Q-102, Q-103, Q-110 | `activo` |
| `core.specs_ticket_ix` | `specs` | ticket_id,version DESC | `redundante` | Cubierto por la restricción única `spec_version_uq (ticket_id, version)`: Postgres la recorre al revés para `version DESC`. | - | `candidato_a_retiro` |
| `core.subscriptions_vigente_uq` | `subscriptions` | tenant_id · parcial | `unicidad` | Una sola suscripción vigente por tenant; además sirve la búsqueda de la suscripción vigente. | Q-198 | `activo` |
| `core.attachments_huerfanos_ix` | `ticket_attachments` | creado_en · parcial | `consulta` | Purga de adjuntos huérfanos (worker). | Q-207 | `activo` |
| `core.attachments_ticket_ix` | `ticket_attachments` | ticket_id | `consulta` | Sirve a las consultas citadas. | Q-141, Q-155, Q-207 | `activo` |
| `core.idx_ticket_attachments_tenant_subido_por` | `ticket_attachments` | tenant_id,subido_por | `soporte-fk` | Cubre la FK `ticket_attachments_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_ticket_attachments_tenant_ticket_id` | `ticket_attachments` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `ticket_attachments_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-141, Q-155, Q-207 | `activo` |
| `core.idx_ticket_watchers_tenant_ticket_id` | `ticket_watchers` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `ticket_watchers_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-121, Q-149, Q-166, Q-168, Q-169, Q-170 | `activo` |
| `core.idx_ticket_watchers_tenant_user_id` | `ticket_watchers` | tenant_id,user_id | `soporte-fk` | Cubre la FK `ticket_watchers_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-169 | `activo` |
| `core.watchers_user_ix` | `ticket_watchers` | user_id | `consulta` | Sirve a las consultas citadas. | Q-169 | `activo` |
| `core.idx_tickets_tenant_cancelado_por` | `tickets` | tenant_id,cancelado_por | `soporte-fk` | Cubre la FK `tickets_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_tickets_tenant_duplicado_de` | `tickets` | tenant_id,duplicado_de | `soporte-fk` | Cubre la FK `tickets_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_tickets_tenant_reportado_por` | `tickets` | tenant_id,reportado_por | `soporte-fk` | Cubre la FK `tickets_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.tickets_duplicado_ix` | `tickets` | duplicado_de · parcial | `sin-consulta` | Ninguna consulta busca los duplicados de un ticket; la FK ya está cubierta por `idx_tickets_tenant_duplicado_de`. | - | `candidato_a_retiro` |
| `core.tickets_kanban_ix` | `tickets` | tenant_id,etapa,creado_en DESC · parcial | `consulta` | Sirve a las consultas citadas. | Q-066, Q-069, Q-171, Q-201 | `activo` |
| `core.tickets_reporter_ix` | `tickets` | reportado_por | `sin-consulta` | Ninguna consulta filtra por `reportado_por`; la FK ya está cubierta por `idx_tickets_tenant_reportado_por`. | - | `candidato_a_retiro` |
| `core.tickets_tenant_etapa_ix` | `tickets` | tenant_id,etapa,actualizado_en DESC | `consulta` | Filtro `etapa` de `GET /tickets` con orden por `actualizado_en`. | Q-066, Q-067, Q-069, Q-144, Q-145 | `activo` |
| `core.tickets_tenant_gate_ix` | `tickets` | tenant_id,gate_pendiente · parcial | `consulta` | Filtro `gate_pendiente` de `GET /tickets` y del tablero. | Q-066, Q-067, Q-069, Q-144, Q-145 | `activo` |
| `core.tickets_tenant_tipo_ix` | `tickets` | tenant_id,tipo | `consulta` | Filtro `tipo` de `GET /tickets`. | Q-144, Q-145 | `activo` |
| `core.tickets_tenant_upd_ix` | `tickets` | tenant_id,actualizado_en DESC | `consulta` | Orden por `actualizado_en` de `GET /tickets`. | Q-144, Q-145, Q-171, Q-172, Q-173 | `activo` |
| `core.tickets_titulo_trgm_ix` | `tickets` | titulo gin_trgm_ops | `consulta` | Búsqueda `titulo ILIKE '%q%'` de `GET /tickets` (trigramas). | Q-144, Q-145 | `activo` |
| `core.idx_upgrade_requests_tenant_solicitado_por` | `upgrade_requests` | tenant_id,solicitado_por | `soporte-fk` | Cubre la FK `upgrade_requests_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.upgrade_abierta_uq` | `upgrade_requests` | tenant_id · parcial | `unicidad` | Regla de unicidad del negocio. | Q-138 | `activo` |
| `core.idx_usage_loc_tenant_ticket_id` | `usage_loc` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `usage_loc_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-163, Q-172 | `activo` |
| `core.usage_loc_pr_uq` | `usage_loc` | pr_url · parcial | `unicidad` | Un PR no se contabiliza dos veces (`ON CONFLICT (pr_url)`). | Q-124, Q-163, Q-172 | `activo` |
| `core.usage_loc_tenant_ciclo_ix` | `usage_loc` | tenant_id,ciclo | `soporte-fk` | Cubre la FK `usage_loc_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-172 | `activo` |
| `core.usage_loc_ticket_ix` | `usage_loc` | ticket_id | `consulta` | Sirve a las consultas citadas. | Q-163, Q-172 | `activo` |
| `core.idx_usage_tokens_tenant_ticket_id` | `usage_tokens` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `usage_tokens_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-162, Q-172 | `activo` |
| `core.usage_tokens_tenant_ix` | `usage_tokens` | tenant_id,creado_en | `sin-consulta` | Ninguna consulta agrega el uso de tokens por tenant y fecha. | - | `candidato_a_retiro` |
| `core.usage_tokens_ticket_ix` | `usage_tokens` | ticket_id,etapa | `consulta` | Sirve a las consultas citadas. | Q-162, Q-172 | `activo` |
| `core.users_tenant_estado_ix` | `users` | tenant_id,estado | `consulta` | Filtro `estado` de `GET /users` y conteos de usuarios activos. | Q-174, Q-175, Q-176, Q-178, Q-179, Q-180, Q-181, Q-182 (+5) | `activo` |
| `core.users_tenant_rol_ix` | `users` | tenant_id,rol · parcial | `consulta` | Filtro `rol` de `GET /users` y búsqueda de administradores o propietarios. | Q-174, Q-175, Q-176, Q-178, Q-179, Q-180, Q-181, Q-182 (+5) | `activo` |
| `core.idx_webhook_inbox_tenant_connection_id` | `webhook_inbox` | tenant_id,connection_id | `soporte-fk` | Cubre la FK `webhook_inbox_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.idx_webhook_inbox_tenant_ticket_id` | `webhook_inbox` | tenant_id,ticket_id | `soporte-fk` | Cubre la FK `webhook_inbox_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | - | `activo` |
| `core.webhook_inbox_pend_ix` | `webhook_inbox` | estado,recibido_en · parcial | `consulta` | Sirve a las consultas citadas. | Q-208, Q-210 | `activo` |
| `core.webhook_inbox_tenant_ix` | `webhook_inbox` | tenant_id,recibido_en DESC | `sin-consulta` | Ningún endpoint lista la bandeja de webhooks por tenant. | - | `candidato_a_retiro` |
| `vec.definition_emb_tenant_ix` | `definition_embeddings` | tenant_id,capa,modulo | `soporte-fk` | Cubre la FK `definition_embeddings_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-264 | `activo` |
| `vec.definition_embeddings_hnsw_ix` | `definition_embeddings` | embedding vector_cosine_ops | `vectorial` | Búsqueda por similitud coseno (`<=>`) sobre los embeddings. | Q-147 | `activo` |
| `vec.doc_chunks_hnsw_ix` | `doc_chunks` | embedding vector_cosine_ops | `vectorial` | Ningún código actual consulta `vec.doc_chunks`; el índice HNSW de documentos no tiene consumidor. | - | `candidato_a_retiro` |
| `vec.doc_chunks_tenant_ix` | `doc_chunks` | tenant_id | `sin-consulta` | Ningún código actual consulta `vec.doc_chunks`. | - | `candidato_a_retiro` |
| `vec.ticket_embeddings_hnsw_ix` | `ticket_embeddings` | embedding vector_cosine_ops | `vectorial` | Búsqueda por similitud coseno (`<=>`) sobre los embeddings. | Q-146 | `activo` |
| `vec.ticket_embeddings_tenant_ix` | `ticket_embeddings` | tenant_id | `soporte-fk` | Cubre la FK `ticket_embeddings_tenant_id_fkey` (tenant_id): evita recorrer la tabla hija al borrar o validar el padre. | Q-146 | `activo` |
<!-- plan-indices:fin -->

## 5. Candidatos a retiro

- `audit.audit_evento_ix` (`sin-consulta`): Ningún endpoint filtra por `evento`: `GET /audit/events` filtra por ticket, etapa, actor y fechas. Sin escaneos en la medición.
- `cm.nodes_kind_idx` (`sin-consulta`): Creado en KC-DB-04-4 para consultas por `kind`; ninguna consulta actual filtra `cm.nodes` por `kind`.
- `core.audit_exports_tenant_ix` (`sin-consulta`): Ninguna consulta lista las exportaciones por fecha: solo se consulta una por id y se cuentan las en curso por estado.
- `core.escalations_abiertas_ix` (`sin-consulta`): Ningún endpoint lista las escalaciones abiertas por severidad.
- `core.gates_actor_ix` (`sin-consulta`): Ninguna consulta filtra por `actor_id`; la FK ya está cubierta por `idx_gate_signatures_tenant_actor_id`.
- `core.job_transitions_job_ix` (`sin-consulta`): Ninguna consulta lee las transiciones de un job; la FK ya está cubierta por `idx_job_transitions_tenant_job_id`.
- `core.llm_steps_job_ix` (`sin-consulta`): Ninguna consulta lee los pasos de un job; la FK ya está cubierta por `idx_llm_steps_tenant_job_id`.
- `core.qa_runs_ticket_ix` (`redundante`): Cubierto por la restricción única `qa_corrida_uq (ticket_id, corrida)`: Postgres la recorre al revés para `corrida DESC`.
- `core.spec_criteria_tenant_ix` (`redundante`): Cubierto por `idx_spec_criteria_tenant_spec_id (tenant_id, spec_id)`.
- `core.specs_ticket_ix` (`redundante`): Cubierto por la restricción única `spec_version_uq (ticket_id, version)`: Postgres la recorre al revés para `version DESC`.
- `core.tickets_duplicado_ix` (`sin-consulta`): Ninguna consulta busca los duplicados de un ticket; la FK ya está cubierta por `idx_tickets_tenant_duplicado_de`.
- `core.tickets_reporter_ix` (`sin-consulta`): Ninguna consulta filtra por `reportado_por`; la FK ya está cubierta por `idx_tickets_tenant_reportado_por`.
- `core.usage_tokens_tenant_ix` (`sin-consulta`): Ninguna consulta agrega el uso de tokens por tenant y fecha.
- `core.webhook_inbox_tenant_ix` (`sin-consulta`): Ningún endpoint lista la bandeja de webhooks por tenant.
- `vec.doc_chunks_hnsw_ix` (`vectorial`): Ningún código actual consulta `vec.doc_chunks`; el índice HNSW de documentos no tiene consumidor.
- `vec.doc_chunks_tenant_ix` (`sin-consulta`): Ningún código actual consulta `vec.doc_chunks`.

Antes de retirar cualquiera: confirmar con el inventario de Backend (KC-BE-06-1) y medir con volumen
realista. Los redundantes tienen la evidencia más sólida (otro índice con las mismas claves ya existe).

## 6. Índices que faltan

El informe lista las claves foráneas cuyas columnas no son el prefijo de ningún índice de su tabla. En la
base actual son 9; se revisaron una a una:

| Tabla hija | Clave foránea | Veredicto |
|---|---|---|
| `vec.doc_chunks` | `doc_chunks_documento_id_fkey (tenant_id, documento_id)` | **Falta.** Borrar un documento de onboarding arrastra sus fragmentos (`ON DELETE CASCADE`) y hoy recorre toda la tabla. Recomendado `(tenant_id, documento_id)`; podría sustituir a `doc_chunks_tenant_ix`. |
| `vec.ticket_embeddings` | `ticket_embeddings_ticket_id_fkey` | No falta: la `PRIMARY KEY (ticket_id)` ya sirve a la comprobación. Falso positivo del prefijo estricto. |
| `core.subscriptions`, `core.upgrade_requests` | `plan_codigo`, `plan_deseado` → `core.plans` | No falta: `core.plans` es un catálogo global pequeño y de solo lectura para la aplicación. |
| `cm.nodes` | `nodes_kind_fkey (kind)` → `cm.node_types` | No falta: catálogo de seis filas. |
| `cm.snapshot_vigente` | `(tenant_id, snapshot_id)` | No falta: una fila por tenant. |
| `cm.concepts`, `cm.curation_queue` | `curado_por`, `revisado_por` → `core.users` | No falta por ahora: tablas pequeñas y usuarios que casi no se borran. Reevaluar con volumen. |
| `cm.incidents` | `(tenant_id, ticket_id)` | No falta por ahora: tabla sin uso en la API. Reevaluar con volumen. |

## 7. Las 15 consultas más costosas (medición de ejemplo)

Carga: los tests de la API y de integración contra PostgreSQL 16 con `pg_stat_statements`, datos de
demostración. Solo sentencias de nivel superior de `kallicode_app`, ordenadas por tiempo total.

**Lectura honesta:** la lista la dominan inserciones de los tests (auditoría, consumo, snapshots de
CodeMapping), no consultas de endpoints con volumen. Con los datos de demostración todas están muy por
debajo de 5 ms y los planes son recorridos de tablas pequeñas. Sirve para validar el procedimiento, no para
decidir índices: con el inventario de Backend y volumen realista la lista cambiará.

| # | Llamadas | Media (ms) | Desv. (ms) | Máx. (ms) | p95 propuesto (ms) | Consulta |
|---|---|---|---|---|---|---|
| 1 | 183 | 2.973 | 8.967 | 55.76 | 25 | `INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, datos) VALUES (core.current_tenant(), …` |
| 2 | 26 | 2.64 | 2.195 | 8.65 | 10 | `INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, pr_url, anadidas, eliminadas, netas) VALUES (co…` |
| 3 | 20 | 2.726 | 1.489 | 5.78 | 10 | `INSERT INTO audit.audit_events ( tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, d…` |
| 4 | 12 | 3.177 | 1.396 | 6.07 | 10 | `SELECT cm.publicar_snapshot($1, $2, $3)` |
| 5 | 18 | 0.973 | 0.773 | 2.38 | 5 | `INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) VALUES ($1, core.current_tenant(), $…` |
| 6 | 24 | 0.664 | 0.467 | 1.55 | 5 | `INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) VALUES ($1, core.current_tenant(), $2, $3)` |
| 7 | 9 | 1.445 | 0.558 | 2.92 | 5 | `DELETE FROM cm.repo_snapshots` |
| 8 | 6 | 2.111 | 0.508 | 2.79 | 5 | `INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) VALUES ($2, core.current_tenant(), $…` |
| 9 | 6 | 1.857 | 0.294 | 2.26 | 5 | `SELECT integra, eventos_verificados, primer_evento_roto, eventos_esperados, eventos_observados FROM audit.veri…` |
| 10 | 9 | 0.831 | 0.609 | 2.01 | 5 | `DELETE FROM cm.files` |
| 11 | 8 | 0.805 | 0.669 | 2.2 | 5 | `INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, props, snapshot_id) VALUES (core.cu…` |
| 12 | 10 | 0.627 | 0.264 | 1.12 | 5 | `INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) VALUES ($1, core.…` |
| 13 | 9 | 0.675 | 0.214 | 1.11 | 5 | `INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, netas, motivo_ajuste) VALUES (core.current_tenant(), $1, $…` |
| 14 | 7 | 0.8 | 0.293 | 1.31 | 5 | `INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) VALUES ($1, core.…` |
| 15 | 1 | 4.524 | 0.0 | 4.52 | 5 | `SELECT * FROM cm.retirar_snapshot($1)` |

Planes de ejecución genéricos (`EXPLAIN (GENERIC_PLAN, COSTS OFF)`, capturados como `kallicode_app`):

<details><summary>#1 · 183 llamadas · media 2.973 ms</summary>

```sql
INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, datos) VALUES (core.current_tenant(), $3, $4, $1, CAST($2 AS jsonb))
```

```text
Insert on audit_events
  ->  Result
```

</details>
<details><summary>#2 · 26 llamadas · media 2.64 ms</summary>

```sql
INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, pr_url, anadidas, eliminadas, netas) VALUES (core.current_tenant(), $1, $2, $5, $3, $4, $6, $4)
```

```text
Insert on usage_loc
  ->  Result
```

</details>
<details><summary>#3 · 20 llamadas · media 2.726 ms</summary>

```sql
INSERT INTO audit.audit_events ( tenant_id, ticket_id, job_id, etapa, actor_tipo, actor_id, evento, resumen, datos, modelo, step_id, ip, user_agent, request_id, sello_previo, sello ) VALUES ( $1, $2, $3, $4, $5, $6, $7, $8, CAST($9 AS jsonb), $10, $11, CAST($12 AS inet), $13, $14, $15, $16 ) RETURNING sello
```

```text
Insert on audit_events
  ->  Result
```

</details>
<details><summary>#4 · 12 llamadas · media 3.177 ms</summary>

```sql
SELECT cm.publicar_snapshot($1, $2, $3)
```

```text
Result
```

</details>
<details><summary>#5 · 18 llamadas · media 0.973 ms</summary>

```sql
INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) VALUES ($1, core.current_tenant(), $2, $4, $5, $3)
```

```text
Insert on files
  ->  Result
```

</details>
<details><summary>#6 · 24 llamadas · media 0.664 ms</summary>

```sql
INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) VALUES ($1, core.current_tenant(), $2, $3)
```

```text
Insert on repo_snapshots
  ->  Result
```

</details>
<details><summary>#7 · 9 llamadas · media 1.445 ms</summary>

```sql
DELETE FROM cm.repo_snapshots
```

```text
Delete on repo_snapshots
  ->  Seq Scan on repo_snapshots
        Filter: (tenant_id = current_setting('app.tenant_id'::text, true))
```

</details>
<details><summary>#8 · 6 llamadas · media 2.111 ms</summary>

```sql
INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) VALUES ($2, core.current_tenant(), $3, $4, $5, $1)
```

```text
Insert on files
  ->  Result
```

</details>
<details><summary>#9 · 6 llamadas · media 1.857 ms</summary>

```sql
SELECT integra, eventos_verificados, primer_evento_roto, eventos_esperados, eventos_observados FROM audit.verify_chain($1)
```

```text
Function Scan on verify_chain
```

</details>
<details><summary>#10 · 9 llamadas · media 0.831 ms</summary>

```sql
DELETE FROM cm.files
```

```text
Delete on files
  ->  Bitmap Heap Scan on files
        Recheck Cond: (tenant_id = current_setting('app.tenant_id'::text, true))
        ->  Bitmap Index Scan on idx_files_tenant_path_sha
              Index Cond: (tenant_id = current_setting('app.tenant_id'::text, true))
```

</details>
<details><summary>#11 · 8 llamadas · media 0.805 ms</summary>

```sql
INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id, props, snapshot_id) VALUES (core.current_tenant(), $4, $5, $1, $6, $2, $7::jsonb, $3)
```

```text
Insert on edges
  ->  Result
```

</details>
<details><summary>#12 · 10 llamadas · media 0.627 ms</summary>

```sql
INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) VALUES ($1, core.current_tenant(), $2, $4, $3, $5, $6, $7)
```

```text
Insert on symbols
  ->  Result
```

</details>
<details><summary>#13 · 9 llamadas · media 0.675 ms</summary>

```sql
INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, netas, motivo_ajuste) VALUES (core.current_tenant(), $1, $3, $2, $4)
```

```text
Insert on usage_loc
  ->  Result
```

</details>
<details><summary>#14 · 7 llamadas · media 0.8 ms</summary>

```sql
INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, signature) VALUES ($1, core.current_tenant(), $3, $4, $2, $5, $6, $7)
```

```text
Insert on symbols
  ->  Result
```

</details>
<details><summary>#15 · 1 llamadas · media 4.524 ms</summary>

```sql
SELECT * FROM cm.retirar_snapshot($1)
```

```text
Function Scan on retirar_snapshot
```

</details>

## 8. Mantener este plan al día

- Cada migración que cree o retire un índice explícito debe actualizar la tabla de la sección 4. El test
  `packages/core/tests/integracion/test_indices_documentados.py` lo comprueba contra la base.
- El test también exige que cada fila tenga categoría, justificación y estado válidos, que los índices de
  categoría `consulta` citen consultas que existen en el Anexo A y que los candidatos a retiro tengan su
  justificación.
- Los índices que respaldan una restricción (`PRIMARY KEY`, `UNIQUE`) quedan fuera: se justifican por la
  restricción.

## Anexo A. Inventario provisional de consultas

Extraído con `python db/tools/inventario_consultas.py` (264 consultas). **Provisional, pendiente de
KC-BE-06-1.** La columna «Filtra / une por» recoge las columnas de `WHERE` y `ON`; los filtros que se arman
dinámicamente aparecen incompletos.

<!-- inventario:inicio -->
| Id | Endpoint o función | Tipo | Tablas | Filtra / une por | Ordena por | Origen |
|---|---|---|---|---|---|---|
| Q-001 | (api) `con_idempotencia` | INSERT | `core.idempotency_keys` | - | - | `apps/api/src/kallicode_api/idempotencia.py:25` |
| Q-002 | (api) `con_idempotencia` | SELECT | `core.idempotency_keys` | tenant_id, endpoint, clave | - | `apps/api/src/kallicode_api/idempotencia.py:43` |
| Q-003 | (api) `con_idempotencia` | UPDATE | `core.idempotency_keys` | tenant_id, endpoint, clave | - | `apps/api/src/kallicode_api/idempotencia.py:70` |
| Q-004 | (api) `con_idempotencia` | DELETE | `core.idempotency_keys` | tenant_id, endpoint, clave | - | `apps/api/src/kallicode_api/idempotencia.py:89` |
| Q-005 | GET /audit/events | SELECT | `audit.audit_events` | - | - | `apps/api/src/kallicode_api/routers/audit.py:72` |
| Q-006 | GET /audit/events | SELECT | `audit.audit_events` | - | - | `apps/api/src/kallicode_api/routers/audit.py:82` |
| Q-007 | GET /audit/events | SELECT | `audit.audit_events` | tenant_id, creado_en | - | `apps/api/src/kallicode_api/routers/audit.py:89` |
| Q-008 | GET /audit/tickets/{numero} | SELECT | `core.tickets` | tenant_id, numero | - | `apps/api/src/kallicode_api/routers/audit.py:114` |
| Q-009 | GET /audit/tickets/{numero} | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/audit.py:121` |
| Q-010 | GET /audit/tickets/{numero} | SELECT | `core.gate_signatures`, `core.users` | id, ticket_id | creado_en | `apps/api/src/kallicode_api/routers/audit.py:126` |
| Q-011 | GET /audit/tickets/{numero} | SELECT | `core.llm_steps` | ticket_id | creado_en | `apps/api/src/kallicode_api/routers/audit.py:135` |
| Q-012 | GET /audit/steps/{step_id} | SELECT | `core.llm_steps` | id | - | `apps/api/src/kallicode_api/routers/audit.py:168` |
| Q-013 | POST /audit/exports | SELECT | `core.audit_exports` | tenant_id, estado | - | `apps/api/src/kallicode_api/routers/audit.py:208` |
| Q-014 | POST /audit/exports | INSERT | `core.audit_exports` | - | - | `apps/api/src/kallicode_api/routers/audit.py:221` |
| Q-015 | GET /audit/exports/{export_id} | SELECT | `core.audit_exports` | id | - | `apps/api/src/kallicode_api/routers/audit.py:252` |
| Q-016 | (api) `_emitir_par` | SELECT | `core.subscriptions` | tenant_id, finaliza_el | - | `apps/api/src/kallicode_api/routers/auth.py:99` |
| Q-017 | (api) `_emitir_par` | INSERT | `core.refresh_tokens` | - | - | `apps/api/src/kallicode_api/routers/auth.py:106` |
| Q-018 | POST /auth/login | SELECT | `core.organizations`, `core.users` | id, email, eliminado_en | - | `apps/api/src/kallicode_api/routers/auth.py:159` |
| Q-019 | POST /auth/login | UPDATE | `core.users` | id | - | `apps/api/src/kallicode_api/routers/auth.py:202` |
| Q-020 | POST /auth/login | UPDATE | `core.users` | id | - | `apps/api/src/kallicode_api/routers/auth.py:218` |
| Q-021 | POST /auth/refresh | SELECT | `core.refresh_tokens` | token_hash | - | `apps/api/src/kallicode_api/routers/auth.py:242` |
| Q-022 | POST /auth/refresh | UPDATE | `core.refresh_tokens` | user_id | - | `apps/api/src/kallicode_api/routers/auth.py:252` |
| Q-023 | POST /auth/refresh | UPDATE | `core.refresh_tokens` | id | - | `apps/api/src/kallicode_api/routers/auth.py:262` |
| Q-024 | POST /auth/refresh | SELECT | `core.users` | id, estado | - | `apps/api/src/kallicode_api/routers/auth.py:267` |
| Q-025 | POST /auth/logout | SELECT | `core.refresh_tokens` | token_hash | - | `apps/api/src/kallicode_api/routers/auth.py:298` |
| Q-026 | POST /auth/logout | UPDATE | `core.refresh_tokens` | user_id | - | `apps/api/src/kallicode_api/routers/auth.py:306` |
| Q-027 | POST /auth/logout | UPDATE | `core.refresh_tokens` | token_hash | - | `apps/api/src/kallicode_api/routers/auth.py:315` |
| Q-028 | POST /auth/forgot-password | SELECT | `core.users` | email, estado | - | `apps/api/src/kallicode_api/routers/auth.py:351` |
| Q-029 | POST /auth/forgot-password | SELECT | `core.users` | email, tenant_id, estado | - | `apps/api/src/kallicode_api/routers/auth.py:355` |
| Q-030 | POST /auth/forgot-password | INSERT | `core.password_reset_tokens` | - | - | `apps/api/src/kallicode_api/routers/auth.py:363` |
| Q-031 | POST /auth/reset-password | SELECT | `core.password_reset_tokens` | token_hash, usado_en, expira_en | - | `apps/api/src/kallicode_api/routers/auth.py:392` |
| Q-032 | POST /auth/reset-password | UPDATE | `core.password_reset_tokens` | id | - | `apps/api/src/kallicode_api/routers/auth.py:404` |
| Q-033 | POST /auth/reset-password | UPDATE | `core.users` | id | - | `apps/api/src/kallicode_api/routers/auth.py:409` |
| Q-034 | POST /auth/reset-password | UPDATE | `core.refresh_tokens` | user_id | - | `apps/api/src/kallicode_api/routers/auth.py:414` |
| Q-035 | POST /auth/invitations/accept | SELECT | `core.invitations` | token_hash, estado, expira_en | - | `apps/api/src/kallicode_api/routers/auth.py:449` |
| Q-036 | POST /auth/invitations/accept | SELECT | `core.users` | email, estado | - | `apps/api/src/kallicode_api/routers/auth.py:463` |
| Q-037 | POST /auth/invitations/accept | SELECT | `core.organizations` | id | - | `apps/api/src/kallicode_api/routers/auth.py:475` |
| Q-038 | POST /auth/invitations/accept | INSERT | `core.users` | - | - | `apps/api/src/kallicode_api/routers/auth.py:480` |
| Q-039 | POST /auth/invitations/accept | UPDATE | `core.invitations` | id | - | `apps/api/src/kallicode_api/routers/auth.py:496` |
| Q-040 | POST /auth/invitations/accept | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/auth.py:510` |
| Q-041 | GET /onboarding/connections | SELECT | `core.connections` | tenant_id | categoria, proveedor | `apps/api/src/kallicode_api/routers/connections.py:69` |
| Q-042 | GET /onboarding/connections | SELECT | `core.branch_map` | tenant_id | - | `apps/api/src/kallicode_api/routers/connections.py:76` |
| Q-043 | POST /onboarding/connections | SELECT | `core.connections` | tenant_id, proveedor, config, estado | - | `apps/api/src/kallicode_api/routers/connections.py:112` |
| Q-044 | POST /onboarding/connections | INSERT | `core.connections` | - | - | `apps/api/src/kallicode_api/routers/connections.py:130` |
| Q-045 | PATCH /onboarding/connections/{connection_id} | SELECT | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:178` |
| Q-046 | PATCH /onboarding/connections/{connection_id} | UPDATE | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:194` |
| Q-047 | PATCH /onboarding/connections/{connection_id} | UPDATE | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:199` |
| Q-048 | PATCH /onboarding/connections/{connection_id} | UPDATE | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:204` |
| Q-049 | PATCH /onboarding/connections/{connection_id} | SELECT | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:218` |
| Q-050 | DELETE /onboarding/connections/{connection_id} | SELECT | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:230` |
| Q-051 | DELETE /onboarding/connections/{connection_id} | SELECT | `core.jobs` | tenant_id | - | `apps/api/src/kallicode_api/routers/connections.py:236` |
| Q-052 | DELETE /onboarding/connections/{connection_id} | DELETE | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:249` |
| Q-053 | POST /onboarding/connections/{connection_id}/test | SELECT | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:268` |
| Q-054 | POST /onboarding/connections/{connection_id}/test | UPDATE | `core.connections` | id | - | `apps/api/src/kallicode_api/routers/connections.py:276` |
| Q-055 | PUT /onboarding/connections/branch-map | SELECT | `core.jobs` | tenant_id, estado | - | `apps/api/src/kallicode_api/routers/connections.py:301` |
| Q-056 | PUT /onboarding/connections/branch-map | INSERT | `core.branch_map` | feature_pattern, staging_branch, prod_branch, actualizado_en | - | `apps/api/src/kallicode_api/routers/connections.py:313` |
| Q-057 | POST /onboarding/documents | INSERT | `core.onboarding_documents` | - | - | `apps/api/src/kallicode_api/routers/connections.py:363` |
| Q-058 | GET /onboarding/codemapping/status | SELECT | `core.onboarding_documents` | estado, tenant_id | - | `apps/api/src/kallicode_api/routers/connections.py:391` |
| Q-059 | GET /onboarding/codemapping/status | SELECT | `vec.definition_embeddings` | tenant_id | - | `apps/api/src/kallicode_api/routers/connections.py:399` |
| Q-060 | POST /onboarding/complete | SELECT | `core.organizations` | id | - | `apps/api/src/kallicode_api/routers/connections.py:423` |
| Q-061 | POST /onboarding/complete | SELECT | `core.production_lines` | tenant_id | - | `apps/api/src/kallicode_api/routers/connections.py:429` |
| Q-062 | POST /onboarding/complete | SELECT | `core.connections` | tenant_id, categoria, estado | - | `apps/api/src/kallicode_api/routers/connections.py:438` |
| Q-063 | POST /onboarding/complete | SELECT | `core.branch_map` | tenant_id | - | `apps/api/src/kallicode_api/routers/connections.py:447` |
| Q-064 | POST /onboarding/complete | INSERT | `core.production_lines` | - | - | `apps/api/src/kallicode_api/routers/connections.py:463` |
| Q-065 | POST /onboarding/complete | UPDATE | `core.organizations` | id | - | `apps/api/src/kallicode_api/routers/connections.py:468` |
| Q-066 | GET /dashboard/summary | SELECT | `core.tickets` | etapa, gate_pendiente, cerrado_en, tenant_id | - | `apps/api/src/kallicode_api/routers/dashboard.py:22` |
| Q-067 | GET /dashboard/lines | SELECT | `core.jobs`, `core.production_lines`, `core.tickets` | id, tenant_id | numero | `apps/api/src/kallicode_api/routers/dashboard.py:39` |
| Q-068 | GET /dashboard/lines | SELECT | `core.jobs` | tenant_id, estado | - | `apps/api/src/kallicode_api/routers/dashboard.py:50` |
| Q-069 | GET /dashboard/kanban | SELECT | `core.tickets` | tenant_id, etapa | creado_en | `apps/api/src/kallicode_api/routers/dashboard.py:69` |
| Q-070 | (api) `_ticket_y_job` | SELECT | `core.tickets` | tenant_id, numero | - | `apps/api/src/kallicode_api/routers/gates.py:50` |
| Q-071 | (api) `_ticket_y_job` | SELECT | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/gates.py:57` |
| Q-072 | GET /approvals/pending | SELECT | `core.tickets` | tenant_id, gate_pendiente | actualizado_en | `apps/api/src/kallicode_api/routers/gates.py:74` |
| Q-073 | GET /tickets/{numero}/gates | SELECT | `core.gate_signatures`, `core.users` | id, ticket_id | gate, creado_en | `apps/api/src/kallicode_api/routers/gates.py:107` |
| Q-074 | POST /tickets/{numero}/gates/{gate}/approve | SELECT | `core.specs` | ticket_id | version | `apps/api/src/kallicode_api/routers/gates.py:172` |
| Q-075 | POST /tickets/{numero}/gates/{gate}/approve | SELECT | `core.spec_alternatives` | spec_id | - | `apps/api/src/kallicode_api/routers/gates.py:186` |
| Q-076 | POST /tickets/{numero}/gates/{gate}/approve | UPDATE | `core.spec_alternatives` | spec_id, alt_id | - | `apps/api/src/kallicode_api/routers/gates.py:197` |
| Q-077 | POST /tickets/{numero}/gates/{gate}/approve | UPDATE | `core.specs` | id | - | `apps/api/src/kallicode_api/routers/gates.py:209` |
| Q-078 | POST /tickets/{numero}/gates/{gate}/approve | SELECT | `core.qa_runs` | ticket_id | corrida | `apps/api/src/kallicode_api/routers/gates.py:215` |
| Q-079 | POST /tickets/{numero}/gates/{gate}/approve | SELECT | `core.qa_results` | qa_run_id, resultado | - | `apps/api/src/kallicode_api/routers/gates.py:227` |
| Q-080 | POST /tickets/{numero}/gates/{gate}/approve | SELECT | `core.security_findings` | ticket_id, estado | - | `apps/api/src/kallicode_api/routers/gates.py:242` |
| Q-081 | POST /tickets/{numero}/gates/{gate}/approve | INSERT | `core.gate_signatures` | - | - | `apps/api/src/kallicode_api/routers/gates.py:270` |
| Q-082 | POST /tickets/{numero}/gates/{gate}/approve | UPDATE | `core.jobs` | id | - | `apps/api/src/kallicode_api/routers/gates.py:297` |
| Q-083 | POST /tickets/{numero}/gates/{gate}/request-changes | UPDATE | `core.jobs` | id | - | `apps/api/src/kallicode_api/routers/gates.py:348` |
| Q-084 | POST /tickets/{numero}/gates/{gate}/request-changes | INSERT | `core.gate_signatures` | - | - | `apps/api/src/kallicode_api/routers/gates.py:369` |
| Q-085 | POST /tickets/{numero}/gates/{gate}/request-changes | INSERT | `core.escalations` | - | - | `apps/api/src/kallicode_api/routers/gates.py:387` |
| Q-086 | (api) `_ticket` | SELECT | `core.tickets` | tenant_id, numero | - | `apps/api/src/kallicode_api/routers/internal.py:214` |
| Q-087 | (api) `_job_activo` | SELECT | `core.jobs` | id | - | `apps/api/src/kallicode_api/routers/internal.py:225` |
| Q-088 | POST /jobs | INSERT | `core.jobs` | - | - | `apps/api/src/kallicode_api/routers/internal.py:250` |
| Q-089 | (api) `transicionar_seguro` | SELECT | `core.jobs` | id | - | `apps/api/src/kallicode_api/routers/internal.py:292` |
| Q-090 | (api) `transicionar_seguro` | SELECT | `core.specs` | ticket_id, estado | - | `apps/api/src/kallicode_api/routers/internal.py:319` |
| Q-091 | (api) `transicionar_seguro` | SELECT | `core.qa_runs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:329` |
| Q-092 | (api) `transicionar_seguro` | SELECT | `core.tickets` | id | - | `apps/api/src/kallicode_api/routers/internal.py:356` |
| Q-093 | GET /jobs | SELECT | `core.jobs` | - | - | `apps/api/src/kallicode_api/routers/internal.py:401` |
| Q-094 | GET /jobs | SELECT | `core.jobs` | - | - | `apps/api/src/kallicode_api/routers/internal.py:410` |
| Q-095 | POST /jobs/{job_id}/assign | SELECT | `core.production_lines` | tenant_id, numero | - | `apps/api/src/kallicode_api/routers/internal.py:443` |
| Q-096 | POST /jobs/{job_id}/assign | UPDATE | `core.jobs` | id | - | `apps/api/src/kallicode_api/routers/internal.py:453` |
| Q-097 | POST /jobs/{job_id}/steps | SELECT | `core.tickets` | id | - | `apps/api/src/kallicode_api/routers/internal.py:531` |
| Q-098 | POST /jobs/{job_id}/steps | INSERT | `core.llm_steps` | - | - | `apps/api/src/kallicode_api/routers/internal.py:538` |
| Q-099 | POST /jobs/{job_id}/steps | INSERT | `core.usage_tokens` | - | - | `apps/api/src/kallicode_api/routers/internal.py:566` |
| Q-100 | POST /tickets/{numero}/triage-result | UPDATE | `core.tickets` | id | - | `apps/api/src/kallicode_api/routers/internal.py:639` |
| Q-101 | POST /tickets/{numero}/triage-result | SELECT | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:656` |
| Q-102 | POST /tickets/{numero}/spec-versions | SELECT | `core.specs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:728` |
| Q-103 | POST /tickets/{numero}/spec-versions | UPDATE | `core.specs` | ticket_id, estado | - | `apps/api/src/kallicode_api/routers/internal.py:736` |
| Q-104 | POST /tickets/{numero}/spec-versions | INSERT | `core.specs` | - | - | `apps/api/src/kallicode_api/routers/internal.py:744` |
| Q-105 | POST /tickets/{numero}/spec-versions | INSERT | `core.spec_alternatives` | - | - | `apps/api/src/kallicode_api/routers/internal.py:766` |
| Q-106 | POST /tickets/{numero}/spec-versions | INSERT | `core.spec_criteria` | - | - | `apps/api/src/kallicode_api/routers/internal.py:783` |
| Q-107 | POST /tickets/{numero}/spec-versions | SELECT | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:799` |
| Q-108 | POST /tickets/{numero}/spec-versions | SELECT | `core.users` | tenant_id, estado, rol | - | `apps/api/src/kallicode_api/routers/internal.py:818` |
| Q-109 | POST /tickets/{numero}/spec-versions | INSERT | `core.notifications` | - | - | `apps/api/src/kallicode_api/routers/internal.py:825` |
| Q-110 | POST /tickets/{numero}/evidence | SELECT | `core.specs` | ticket_id, estado | version | `apps/api/src/kallicode_api/routers/internal.py:867` |
| Q-111 | POST /tickets/{numero}/evidence | SELECT | `core.spec_criteria` | spec_id | - | `apps/api/src/kallicode_api/routers/internal.py:878` |
| Q-112 | POST /tickets/{numero}/evidence | INSERT | `core.qa_runs` | - | - | `apps/api/src/kallicode_api/routers/internal.py:893` |
| Q-113 | POST /tickets/{numero}/evidence | INSERT | `core.qa_results` | - | - | `apps/api/src/kallicode_api/routers/internal.py:912` |
| Q-114 | POST /tickets/{numero}/evidence | SELECT | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:935` |
| Q-115 | POST /tickets/{numero}/findings | INSERT | `core.security_findings` | - | - | `apps/api/src/kallicode_api/routers/internal.py:1005` |
| Q-116 | POST /tickets/{numero}/findings | SELECT | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:1044` |
| Q-117 | POST /tickets/{numero}/findings | INSERT | `core.tickets` | - | - | `apps/api/src/kallicode_api/routers/internal.py:1057` |
| Q-118 | POST /tickets/{numero}/deploy/preflight | SELECT | `core.gate_signatures` | ticket_id, accion | - | `apps/api/src/kallicode_api/routers/internal.py:1108` |
| Q-119 | POST /tickets/{numero}/deploy/preflight | SELECT | `core.security_findings` | ticket_id, estado | - | `apps/api/src/kallicode_api/routers/internal.py:1115` |
| Q-120 | POST /tickets/{numero}/deploy/result | UPDATE | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:1196` |
| Q-121 | POST /tickets/{numero}/deploy/result | SELECT | `core.ticket_watchers` | ticket_id | - | `apps/api/src/kallicode_api/routers/internal.py:1204` |
| Q-122 | POST /tickets/{numero}/deploy/result | INSERT | `core.notifications` | - | - | `apps/api/src/kallicode_api/routers/internal.py:1210` |
| Q-123 | POST /tickets/{numero}/deploy/result | SELECT | `core.jobs` | ticket_id | creado_en | `apps/api/src/kallicode_api/routers/internal.py:1238` |
| Q-124 | POST /usage/loc | INSERT | `core.usage_loc` | tipo | - | `apps/api/src/kallicode_api/routers/internal.py:1297` |
| Q-125 | POST /usage/loc | SELECT | `core.users` | tenant_id, rol, estado | - | `apps/api/src/kallicode_api/routers/internal.py:1331` |
| Q-126 | POST /usage/loc | INSERT | `core.notifications` | - | - | `apps/api/src/kallicode_api/routers/internal.py:1343` |
| Q-127 | POST /escalations | SELECT | `core.escalations` | job_id, estado | - | `apps/api/src/kallicode_api/routers/internal.py:1394` |
| Q-128 | POST /escalations | INSERT | `core.escalations` | - | - | `apps/api/src/kallicode_api/routers/internal.py:1404` |
| Q-129 | POST /escalations | UPDATE | `core.jobs` | id | - | `apps/api/src/kallicode_api/routers/internal.py:1421` |
| Q-130 | POST /escalations | SELECT | `core.tickets` | id | - | `apps/api/src/kallicode_api/routers/internal.py:1427` |
| Q-131 | GET /notifications | SELECT | `core.notifications` | - | - | `apps/api/src/kallicode_api/routers/notifications.py:31` |
| Q-132 | GET /notifications | SELECT | `core.notifications` | user_id | - | `apps/api/src/kallicode_api/routers/notifications.py:40` |
| Q-133 | PATCH /notifications/{notification_id} | UPDATE | `core.notifications` | id, user_id | - | `apps/api/src/kallicode_api/routers/notifications.py:63` |
| Q-134 | POST /notifications/read-all | UPDATE | `core.notifications` | user_id | - | `apps/api/src/kallicode_api/routers/notifications.py:78` |
| Q-135 | (api) `_org` | SELECT | `core.organizations` | id | - | `apps/api/src/kallicode_api/routers/organization.py:32` |
| Q-136 | PATCH /organization | UPDATE | `core.organizations` | - | - | `apps/api/src/kallicode_api/routers/organization.py:76` |
| Q-137 | GET /plans | SELECT | `core.plans` | - | - | `apps/api/src/kallicode_api/routers/plans.py:33` |
| Q-138 | POST /plans/upgrade-request | SELECT | `core.upgrade_requests` | tenant_id, estado | - | `apps/api/src/kallicode_api/routers/plans.py:57` |
| Q-139 | POST /plans/upgrade-request | INSERT | `core.upgrade_requests` | - | - | `apps/api/src/kallicode_api/routers/plans.py:70` |
| Q-140 | (api) `_resolver_ticket` | SELECT | `core.tickets` | tenant_id, numero | - | `apps/api/src/kallicode_api/routers/tickets.py:108` |
| Q-141 | (api) `_validar_adjuntos` | SELECT | `core.ticket_attachments` | id, tenant_id, ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:120` |
| Q-142 | (api) `_crear_ticket` | INSERT | `core.tickets` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:167` |
| Q-143 | (api) `_crear_ticket` | UPDATE | `core.ticket_attachments` | id | - | `apps/api/src/kallicode_api/routers/tickets.py:190` |
| Q-144 | GET /tickets | SELECT | `core.tickets` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:259` |
| Q-145 | GET /tickets | SELECT | `core.tickets` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:266` |
| Q-146 | POST /tickets/dedup-preview | SELECT | `core.tickets`, `vec.ticket_embeddings` | id, tenant_id | embedding, emb, vector | `apps/api/src/kallicode_api/routers/tickets.py:354` |
| Q-147 | POST /tickets/impact-preview | SELECT | `vec.definition_embeddings` | tenant_id | embedding, emb, vector | `apps/api/src/kallicode_api/routers/tickets.py:387` |
| Q-148 | GET /tickets/{numero} | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/tickets.py:425` |
| Q-149 | GET /tickets/{numero} | SELECT | `core.ticket_watchers`, `core.users` | id, ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:430` |
| Q-150 | GET /tickets/{numero} | SELECT | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:437` |
| Q-151 | PATCH /tickets/{numero} | UPDATE | `core.jobs` | ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:502` |
| Q-152 | PATCH /tickets/{numero} | UPDATE | `core.tickets` | id | - | `apps/api/src/kallicode_api/routers/tickets.py:509` |
| Q-153 | PATCH /tickets/{numero} | UPDATE | `core.tickets` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:547` |
| Q-154 | POST /tickets/attachments | INSERT | `core.ticket_attachments` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:591` |
| Q-155 | GET /tickets/{numero}/attachments/{adjunto_id} | SELECT | `core.ticket_attachments` | id, ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:621` |
| Q-156 | GET /tickets/{numero}/spec | SELECT | `core.specs` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:652` |
| Q-157 | GET /tickets/{numero}/spec | SELECT | `core.spec_alternatives` | spec_id | alt_id | `apps/api/src/kallicode_api/routers/tickets.py:664` |
| Q-158 | GET /tickets/{numero}/spec | SELECT | `core.spec_criteria` | spec_id | criterio_id | `apps/api/src/kallicode_api/routers/tickets.py:672` |
| Q-159 | GET /tickets/{numero}/evidence | SELECT | `core.qa_runs` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:702` |
| Q-160 | GET /tickets/{numero}/evidence | SELECT | `core.qa_results` | qa_run_id | criterio_id | `apps/api/src/kallicode_api/routers/tickets.py:714` |
| Q-161 | GET /tickets/{numero}/security | SELECT | `core.security_findings` | ticket_id | creado_en | `apps/api/src/kallicode_api/routers/tickets.py:738` |
| Q-162 | GET /tickets/{numero}/usage | SELECT | `core.usage_tokens` | ticket_id | etapa | `apps/api/src/kallicode_api/routers/tickets.py:770` |
| Q-163 | GET /tickets/{numero}/usage | SELECT | `core.usage_loc` | ticket_id, tipo | - | `apps/api/src/kallicode_api/routers/tickets.py:780` |
| Q-164 | GET /tickets/{numero}/activity | SELECT | `audit.audit_events`, `core.users` | id, tenant_id, ticket_id | id | `apps/api/src/kallicode_api/routers/tickets.py:809` |
| Q-165 | POST /tickets/{numero}/watchers | SELECT | `core.users` | id, estado | - | `apps/api/src/kallicode_api/routers/tickets.py:845` |
| Q-166 | POST /tickets/{numero}/watchers | SELECT | `core.ticket_watchers` | ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:853` |
| Q-167 | POST /tickets/{numero}/watchers | INSERT | `core.ticket_watchers` | - | - | `apps/api/src/kallicode_api/routers/tickets.py:860` |
| Q-168 | POST /tickets/{numero}/watchers | SELECT | `core.ticket_watchers`, `core.users` | id, ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:866` |
| Q-169 | DELETE /tickets/{numero}/watchers/{user_id} | DELETE | `core.ticket_watchers` | ticket_id, user_id | - | `apps/api/src/kallicode_api/routers/tickets.py:890` |
| Q-170 | DELETE /tickets/{numero}/watchers/{user_id} | SELECT | `core.ticket_watchers`, `core.users` | id, ticket_id | - | `apps/api/src/kallicode_api/routers/tickets.py:902` |
| Q-171 | GET /usage/summary | SELECT | `core.tickets` | etapa, tenant_id | - | `apps/api/src/kallicode_api/routers/usage.py:38` |
| Q-172 | GET /usage/by-ticket | SELECT | `core.tickets`, `core.usage_loc`, `core.usage_tokens` | ticket_id, ciclo, tenant_id | max, actualizado_en | `apps/api/src/kallicode_api/routers/usage.py:78` |
| Q-173 | GET /usage/by-ticket | SELECT | `core.tickets` | tenant_id | - | `apps/api/src/kallicode_api/routers/usage.py:98` |
| Q-174 | GET /users/me | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:69` |
| Q-175 | PATCH /users/me | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:78` |
| Q-176 | PATCH /users/me | UPDATE | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:90` |
| Q-177 | PATCH /users/me | UPDATE | `core.refresh_tokens` | user_id | - | `apps/api/src/kallicode_api/routers/users.py:94` |
| Q-178 | PATCH /users/me | UPDATE | `core.users` | - | - | `apps/api/src/kallicode_api/routers/users.py:113` |
| Q-179 | PATCH /users/me | UPDATE | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:119` |
| Q-180 | PATCH /users/me | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:124` |
| Q-181 | GET /users | SELECT | `core.users` | - | - | `apps/api/src/kallicode_api/routers/users.py:153` |
| Q-182 | GET /users | SELECT | `core.users` | - | - | `apps/api/src/kallicode_api/routers/users.py:158` |
| Q-183 | POST /users/invitations | SELECT | `core.invitations` | tenant_id, email, estado | - | `apps/api/src/kallicode_api/routers/users.py:179` |
| Q-184 | POST /users/invitations | INSERT | `core.invitations` | - | - | `apps/api/src/kallicode_api/routers/users.py:193` |
| Q-185 | PATCH /users/{user_id} | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:242` |
| Q-186 | PATCH /users/{user_id} | SELECT | `core.users` | tenant_id, rol, estado | - | `apps/api/src/kallicode_api/routers/users.py:252` |
| Q-187 | PATCH /users/{user_id} | SELECT | `core.users` | tenant_id, rol, estado, id | - | `apps/api/src/kallicode_api/routers/users.py:269` |
| Q-188 | PATCH /users/{user_id} | UPDATE | `core.users` | - | - | `apps/api/src/kallicode_api/routers/users.py:288` |
| Q-189 | PATCH /users/{user_id} | UPDATE | `core.refresh_tokens` | user_id | - | `apps/api/src/kallicode_api/routers/users.py:293` |
| Q-190 | PATCH /users/{user_id} | SELECT | `core.users` | id | - | `apps/api/src/kallicode_api/routers/users.py:306` |
| Q-191 | (api) `_recibir` | SELECT | `core.connections` | ruta_token, estado | - | `apps/api/src/kallicode_api/routers/webhooks.py:50` |
| Q-192 | (api) `_recibir` | INSERT | `core.webhook_inbox` | - | - | `apps/api/src/kallicode_api/routers/webhooks.py:75` |
| Q-193 | POST /kallicode-help/{token} | SELECT | `core.plans`, `core.subscriptions` | codigo, tenant_id, finaliza_el | - | `apps/api/src/kallicode_api/routers/webhooks.py:159` |
| Q-194 | (worker) `procesar` | SELECT | `core.audit_exports` | id | - | `apps/workers/src/kallicode_workers/audit_export.py:23` |
| Q-195 | (worker) `procesar` | UPDATE | `core.audit_exports` | id | - | `apps/workers/src/kallicode_workers/audit_export.py:29` |
| Q-196 | (worker) `procesar` | SELECT | `audit.audit_events` | - | - | `apps/workers/src/kallicode_workers/audit_export.py:35` |
| Q-197 | (worker) `procesar` | UPDATE | `core.audit_exports` | id | - | `apps/workers/src/kallicode_workers/audit_export.py:56` |
| Q-198 | (worker) `ciclo` | SELECT | `core.subscriptions` | finaliza_el, renueva_el | - | `apps/workers/src/kallicode_workers/billing_cycle.py:24` |
| Q-199 | (worker) `_renovar` | UPDATE | `core.subscriptions` | tenant_id, finaliza_el | - | `apps/workers/src/kallicode_workers/billing_cycle.py:35` |
| Q-200 | (worker) `_renovar` | INSERT | `core.usage_cycles` | - | - | `apps/workers/src/kallicode_workers/billing_cycle.py:42` |
| Q-201 | (worker) `_renovar` | UPDATE | `core.tickets` | tenant_id, etapa | - | `apps/workers/src/kallicode_workers/billing_cycle.py:49` |
| Q-202 | (worker) `_renovar` | UPDATE | `core.jobs` | tenant_id, estado | - | `apps/workers/src/kallicode_workers/billing_cycle.py:56` |
| Q-203 | (worker) `_renovar` | SELECT | `core.users` | tenant_id, rol, estado | - | `apps/workers/src/kallicode_workers/billing_cycle.py:70` |
| Q-204 | (worker) `_renovar` | INSERT | `core.notifications` | - | - | `apps/workers/src/kallicode_workers/billing_cycle.py:77` |
| Q-205 | (worker) `ciclo` | DELETE | `core.refresh_tokens` | expira_en | - | `apps/workers/src/kallicode_workers/housekeeping.py:15` |
| Q-206 | (worker) `ciclo` | DELETE | `core.idempotency_keys` | creado_en | - | `apps/workers/src/kallicode_workers/housekeeping.py:18` |
| Q-207 | (worker) `ciclo` | DELETE | `core.ticket_attachments` | ticket_id, creado_en | - | `apps/workers/src/kallicode_workers/housekeeping.py:21` |
| Q-208 | (worker) `ciclo` | DELETE | `core.webhook_inbox` | estado, procesado_en | - | `apps/workers/src/kallicode_workers/housekeeping.py:27` |
| Q-209 | (worker) `ciclo` | SELECT | `core.validar_integridad` | problemas | - | `apps/workers/src/kallicode_workers/housekeeping.py:34` |
| Q-210 | (worker) `procesar_inbox` | SELECT | `core.webhook_inbox` | id, estado | - | `apps/workers/src/kallicode_workers/normalizer.py:107` |
| Q-211 | (worker) `procesar_inbox` | UPDATE | `core.webhook_inbox` | id | - | `apps/workers/src/kallicode_workers/normalizer.py:116` |
| Q-212 | (worker) `procesar_inbox` | UPDATE | `core.webhook_inbox` | id | - | `apps/workers/src/kallicode_workers/normalizer.py:131` |
| Q-213 | (worker) `procesar_inbox` | INSERT | `core.tickets` | - | - | `apps/workers/src/kallicode_workers/normalizer.py:148` |
| Q-214 | (worker) `procesar_inbox` | UPDATE | `core.webhook_inbox` | id | - | `apps/workers/src/kallicode_workers/normalizer.py:176` |
| Q-215 | (worker) `procesar_triage` | SELECT | `core.tickets` | tenant_id, numero | - | `apps/workers/src/kallicode_workers/normalizer.py:215` |
| Q-216 | (worker) `procesar` | SELECT | `core.users` | id | - | `apps/workers/src/kallicode_workers/notifier.py:50` |
| Q-217 | (worker) `ciclo` | SELECT | `core.jobs` | estado | - | `apps/workers/src/kallicode_workers/scheduler.py:20` |
| Q-218 | (worker) `_asignar_tenant` | SELECT | `core.jobs`, `core.tickets` | id, tenant_id, estado | prioridad, creado_en, for, update, of, j, skip, locked | `apps/workers/src/kallicode_workers/scheduler.py:33` |
| Q-219 | (worker) `_asignar_tenant` | SELECT | `core.production_lines` | tenant_id, estado | numero, for, update, skip, locked | `apps/workers/src/kallicode_workers/scheduler.py:45` |
| Q-220 | (worker) `_asignar_tenant` | UPDATE | `core.jobs` | id | - | `apps/workers/src/kallicode_workers/scheduler.py:54` |
| Q-221 | (codemapping) `verificar_presupuesto_tenant` | SELECT | `core.subscriptions`, `core.usage_tokens` | tenant_id, creado_en | - | `packages/codemapping/src/kallicode_codemapping/budget.py:78` |
| Q-222 | (codemapping) `find_resumable_snapshot` | SELECT | `cm.repo_snapshots` | tenant_id, commit_sha, estado | iniciado_en | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:239` |
| Q-223 | (codemapping) `start_snapshot` | INSERT | `cm.repo_snapshots` | - | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:266` |
| Q-224 | (codemapping) `marcar_fase` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:276` |
| Q-225 | (codemapping) `complete_snapshot` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:288` |
| Q-226 | (codemapping) `complete_snapshot` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:296` |
| Q-227 | (codemapping) `write_symbols` | DELETE | `cm.edges` | tenant_id, tipo, src_id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:363` |
| Q-228 | (codemapping) `write_symbols` | INSERT | `cm.edges`, `cm.files`, `cm.symbols` | tenant_id, name, id, language | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:372` |
| Q-229 | (codemapping) `replace_table_usage` | DELETE | `cm.edges` | tenant_id, tipo, src_id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:479` |
| Q-230 | (codemapping) `load_semantic` | SELECT | `cm.symbols` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:539` |
| Q-231 | (codemapping) `load_semantic` | SELECT | `cm.definitions` | tenant_id, id | src_id, dst_id, props, confidence | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:625` |
| Q-232 | (codemapping) `get_last_sha` | SELECT | `cm.repo_snapshots` | tenant_id, estado | finalizado_en, iniciado_en | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:690` |
| Q-233 | (codemapping) `get_file_hashes` | SELECT | `cm.files` | tenant_id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:702` |
| Q-234 | (codemapping) `get_symbol_ids` | SELECT | `cm.files`, `cm.symbols` | tenant_id, id, path | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:712` |
| Q-235 | (codemapping) `get_table_names` | SELECT | `cm.db_tables` | tenant_id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:722` |
| Q-236 | (codemapping) `get_vector_hashes` | SELECT | `vec.definition_embeddings` | tenant_id, nodo_ref | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:732` |
| Q-237 | (codemapping) `delete_file_subgraph` | SELECT | `cm.files` | tenant_id, path | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:750` |
| Q-238 | (codemapping) `delete_file_subgraph` | SELECT | `cm.symbols` | tenant_id, file_id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:755` |
| Q-239 | (codemapping) `delete_file_subgraph` | DELETE | `cm.edges` | tenant_id, src_id, dst_id | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:763` |
| Q-240 | (codemapping) `delete_file_subgraph` | DELETE | `vec.definition_embeddings` | tenant_id, nodo_ref | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:768` |
| Q-241 | (codemapping) `delete_file_subgraph` | DELETE | `cm.files` | tenant_id, path | - | `packages/codemapping/src/kallicode_codemapping/graph_loader.py:773` |
| Q-242 | (codemapping) `obtener_o_crear_snapshot` | SELECT | `cm.repo_snapshots` | tenant_id, commit_sha, estado | iniciado_en | `packages/codemapping/src/kallicode_codemapping/snapshots.py:45` |
| Q-243 | (codemapping) `obtener_o_crear_snapshot` | INSERT | `cm.repo_snapshots` | - | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:93` |
| Q-244 | (codemapping) `registrar_evento` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:132` |
| Q-245 | (codemapping) `registrar_checkpoint` | SELECT | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:164` |
| Q-246 | (codemapping) `registrar_checkpoint` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:199` |
| Q-247 | (codemapping) `marcar_completado` | SELECT | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:218` |
| Q-248 | (codemapping) `marcar_completado` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:251` |
| Q-249 | (codemapping) `marcar_fallido` | SELECT | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:271` |
| Q-250 | (codemapping) `marcar_fallido` | UPDATE | `cm.repo_snapshots` | tenant_id, id | - | `packages/codemapping/src/kallicode_codemapping/snapshots.py:305` |
| Q-251 | (codemapping) `obtener_umbral_confianza_tenant` | SELECT | `core.organizations` | id | - | `packages/codemapping/src/kallicode_codemapping/tenant_thresholds.py:42` |
| Q-252 | (core) `registrar_evento` | INSERT | `audit.audit_events` | - | - | `packages/core/src/kallicode_core/auditoria.py:98` |
| Q-253 | (core) `expediente_ticket` | SELECT | `audit.audit_events` | tenant_id, ticket_id | id | `packages/core/src/kallicode_core/auditoria.py:143` |
| Q-254 | (core) `verificar_cadena` | SELECT | `audit.verify_chain` | - | - | `packages/core/src/kallicode_core/auditoria.py:158` |
| Q-255 | (core) `estado_cuota_loc` | SELECT | `core.plans`, `core.subscriptions`, `core.usage_cycles` | codigo, tenant_id, ciclo, finaliza_el | - | `packages/core/src/kallicode_core/comercial.py:60` |
| Q-256 | (core) `verificar_cupo_usuarios` | SELECT | `core.invitations`, `core.plans`, `core.subscriptions`, `core.users` | tenant_id, estado, codigo, finaliza_el | - | `packages/core/src/kallicode_core/comercial.py:96` |
| Q-257 | (core) `verificar_storage` | SELECT | `core.plans`, `core.subscriptions`, `core.ticket_attachments` | tenant_id, codigo, finaliza_el | - | `packages/core/src/kallicode_core/comercial.py:130` |
| Q-258 | (core) `plan_vigente` | SELECT | `core.plans`, `core.subscriptions` | codigo, tenant_id, finaliza_el | - | `packages/core/src/kallicode_core/comercial.py:192` |
| Q-259 | (core) `_registrar` | INSERT | `core.schema_migrations` | - | - | `packages/core/src/kallicode_core/migraciones.py:187` |
| Q-260 | (core) `migrar` | SELECT | `core.schema_migrations` | - | - | `packages/core/src/kallicode_core/migraciones.py:232` |
| Q-261 | (core) `upsert_embedding_ticket` | SELECT | `vec.ticket_embeddings` | ticket_id | - | `packages/core/src/kallicode_core/vectores.py:38` |
| Q-262 | (core) `upsert_embedding_ticket` | INSERT | `vec.ticket_embeddings` | embedding, modelo_embedding, texto_hash | - | `packages/core/src/kallicode_core/vectores.py:46` |
| Q-263 | (core) `upsert_definition_embedding` | SELECT | `vec.definition_embeddings` | tenant_id, nodo_ref | - | `packages/core/src/kallicode_core/vectores.py:85` |
| Q-264 | (core) `upsert_definition_embedding` | INSERT | `vec.definition_embeddings` | capa, nombre, descripcion, modulo, embedding, texto_hash, actualizado_en | - | `packages/core/src/kallicode_core/vectores.py:94` |
<!-- inventario:fin -->
