> **Acta generada por `python db/tools/ensayo_incidentes.py --salida <dir>`** en el entorno de demostración (Docker Desktop en Windows: PostgreSQL 16 de `db/postgres` + PgBouncer en modo transacción, esquema completo y semilla `db/seeds/dev.sql`). Es el resultado de UNA ejecución local; los tiempos miden el procedimiento con datos mínimos, no una promesa de producción. Procedimientos: [runbook_incidentes_db.md](runbook_incidentes_db.md). **El acta la firma quien ejecuta el ensayo; no sustituye a la aprobación del responsable de operaciones.**

# Acta del ensayo de incidentes de base de datos (KC-DB-11-5)

Ejecutado: 2026-11-16T03:13:56.562524+00:00 · PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) · PgBouncer 1.26.1
Resultado global: **LOS SEIS INCIDENTES SE ENSAYAN Y VERIFICAN** (6/6)

| Incidente | Alerta | Detectado | Contenido | Resuelto | Verificado | Resultado |
|---|---|---|---|---|---|---|
| I1 · Bloqueo prolongado por una migración | DB-A01 | 5.6 s | 5.7 s | 7.7 s | 7.9 s | OK |
| I2 · Agotamiento de conexiones con PgBouncer | DB-A02 | 3.7 s | 3.8 s | 9.9 s | 11.7 s | OK |
| I3 · Cadena de auditoría rota (provocada a propósito) | DB-A03 | 0.2 s | 0.2 s | 0.2 s | 0.4 s | OK |
| I4 · Chequeo de integridad con hallazgos | DB-A04 | 0.2 s | 0.2 s | 0.3 s | 0.5 s | OK |
| I5 · Hinchazón de un índice HNSW | DB-A05 | 3.7 s | 3.8 s | 4.5 s | 4.7 s | OK |
| I6 · Partición DEFAULT con filas | DB-A06 | 0.1 s | 0.1 s | 0.5 s | 0.7 s | OK |

Los tiempos son segundos desde que el incidente está activo, en Docker local con datos mínimos: miden el procedimiento, no una promesa de producción.

## I1 · Bloqueo prolongado por una migración (DB-A01)

| t (s) | Fase | Qué se hizo | Resultado | OK |
|---|---|---|---|---|
| 5.5 | provocación | transacción abierta + ALTER TABLE sin lock_timeout + consulta de la aplicación en cola | bloqueante pid 161 | sí |
| 5.6 | detección | SELECT * FROM core.alertas_db() -> DB-A01 | valor 5.5 s ≥ umbral 3 s; 2 sesión(es) esperando un bloqueo; la más antigua, 5.5 s; bloqueada(s) por el pid 161, 162 | sí |
| 5.7 | diagnóstico | pg_stat_activity + pg_blocking_pids: quién espera y quién bloquea | 2 esperando; migración pid 162 | sí |
| 5.7 | diagnóstico | bloqueante raíz: transacción abierta sin actividad | ('idle in transaction', Decimal('6')) | sí |
| 5.7 | contención | pg_cancel_backend(<pid de la migración>) | migración cancelada; la aplicación esperó 4.6 s | sí |
| 7.7 | resolución | reintento con SET lock_timeout = '2s' mientras el bloqueante sigue: falla rápido | LockNotAvailable sin encolar | sí |
| 7.7 | resolución | pg_terminate_backend(<bloqueante>) tras confirmar que es una transacción olvidada |  | sí |
| 7.7 | resolución | reintento de la migración con lock_timeout |  | sí |
| 7.9 | verificación | sin sesiones en espera; migración aplicada una sola vez; DB-A01 apagada | valor 0.0 | sí |

## I2 · Agotamiento de conexiones con PgBouncer (DB-A02)

| t (s) | Fase | Qué se hizo | Resultado | OK |
|---|---|---|---|---|
| 3.6 | provocación | 4 transacciones abiertas a través de PgBouncer (pool de 4) + una quinta consulta | cl_active 4, cl_waiting 1, sv_active 4 | sí |
| 3.7 | detección | SHOW POOLS en PgBouncer: clientes esperando conexión | cl_waiting 1, maxwait 3 s | sí |
| 3.7 | detección | SELECT * FROM core.alertas_db() -> DB-A02 (transacciones colgadas) | 5 de 30 conexiones (16.7 %); 4 en «idle in transaction», la más antigua hace 3.6 s (umbral 2 s) | sí |
| 3.8 | diagnóstico | pg_stat_activity agrupado por rol y estado | [('kallicode_app', 'idle in transaction', 4, Decimal('4')), ('postgres', 'active', 1, Decimal('0'))] | sí |
| 3.8 | diagnóstico | transacciones abiertas sin actividad (las que retienen el pool) | 4 sesiones idle in transaction | sí |
| 3.8 | contención | pg_terminate_backend de las idle in transaction del rol de la aplicación con más de 2 s | terminadas 4; la quinta consulta ok tras 3.7 s | sí |
| 9.9 | resolución | ALTER ROLE kallicode_app SET idle_in_transaction_session_timeout + RECONNECT: una transacción olvidada se corta sola | cortada por el servidor tras ~3 s; idle in transaction restantes: 0 | sí |
| 10.5 | detección | variante: 25 conexiones directas con max_connections = 30 -> DB-A02 por ratio | 26 de 30 conexiones (86.7 %); 0 en «idle in transaction», la más antigua hace 0.0 s (umbral 2 s) | sí |
| 11.7 | verificación | cl_waiting = 0, el pool sirve consultas y DB-A02 apagada | cl_waiting 0; 1 de 30 conexiones (3.3 %); 0 en «idle in transaction», la más antigua hace 0.0 s (umbral 2 s) | sí |

## I3 · Cadena de auditoría rota (provocada a propósito) (DB-A03)

| t (s) | Fase | Qué se hizo | Resultado | OK |
|---|---|---|---|---|
| - | preparación | export WORM verificado de la partición antes del incidente | 8 eventos; manifiesto d523d4f6829d… | sí |
| 0.0 | provocación | UPDATE de un evento con los disparadores desactivados (manipulación) | evento 5 | sí |
| 0.2 | detección | SELECT * FROM core.alertas_db(p_integridad => true) -> DB-A03 | V16 + V17 de core.validar_integridad(): 1 problema(s) | sí |
| 0.2 | diagnóstico | audit.verify_chain(<tenant>): integra = false y primer evento roto | primer evento roto 5 | sí |
| 0.2 | diagnóstico | worm_auditoria.py comparar: qué evento y qué columnas difieren de su export WORM | alterado el evento 5, columnas ['resumen']; borrados [] | sí |
| 0.2 | diagnóstico | el export WORM sigue íntegro (verificación externa con el hash registrado) | export intacto | sí |
| 0.2 | contención | COPY de la evidencia (estado alterado) a un archivo y comprobación de que no se purga ni se exporta la partición rota | 8 eventos copiados; política conservar; exportar rechaza la cadena rota | sí |
| 0.2 | resolución | worm_auditoria.py restaurar --evento <id> --sha256-manifiesto <registrado> | columnas restauradas: 18 | sí |
| 0.4 | verificación | verify_chain íntegra, partición idéntica al export y DB-A03 apagada | 8 eventos verificados; V16 + V17 de core.validar_integridad(): 0 problema(s) | sí |

## I4 · Chequeo de integridad con hallazgos (DB-A04)

| t (s) | Fase | Qué se hizo | Resultado | OK |
|---|---|---|---|---|
| - | preparación | línea base: core.validar_integridad() sin hallazgos | 0 hallazgos | sí |
| 0.0 | provocación | organización creada sin suscripción vigente (V01) | org_sin_suscripcion | sí |
| 0.2 | detección | SELECT * FROM core.alertas_db(p_integridad => true) -> DB-A04 | chequeos con hallazgos: V01_suscripcion_vigente=1 | sí |
| 0.2 | diagnóstico | core.validar_integridad() WHERE problemas > 0: qué chequeo y cuántos | [('V01_suscripcion_vigente', 1)] | sí |
| 0.2 | diagnóstico | consulta de detalle del chequeo (V01): qué tenants | [('org_sin_suscripcion',)] | sí |
| 0.2 | contención | no se corrige a ciegas: el hallazgo no se propaga (el tenant afectado queda identificado) y se decide la corrección con el dueño del dato | decisión humana: dar de alta la suscripción | sí |
| 0.3 | resolución | alta de la suscripción del tenant afectado | INSERT INTO core.subscriptions | sí |
| 0.5 | verificación | 26 chequeos en 0 y DB-A04 apagada | 26 chequeos, 0 hallazgos | sí |

## I5 · Hinchazón de un índice HNSW (DB-A05)

| t (s) | Fase | Qué se hizo | Resultado | OK |
|---|---|---|---|---|
| - | preparación | índice cargado y línea base registrada con vec.medir_hnsw | hinchazón de partida 0.0 % | sí |
| 3.6 | provocación | borrado del 75 % de las filas + VACUUM (la rotación diaria del refresco, en pequeño) | 1500 -> 375 filas | sí |
| 3.7 | detección | SELECT * FROM core.alertas_db() -> DB-A05 | la mayor hinchazón es la de doc_chunks_hnsw_ix: 300.0 % sobre su línea base | sí |
| 3.8 | diagnóstico | SELECT * FROM vec.estado_hnsw(): tamaño, filas vivas e hinchazón sobre la línea base | 375 filas vivas; hinchazón 300.0 % | sí |
| 3.8 | diagnóstico | comprobar que no hay ya una reconstrucción en curso (pg_stat_progress_create_index) | 0 en curso | sí |
| 3.8 | contención | no urgente: no se reconstruye en hora punta; el índice sigue siendo válido y las búsquedas funcionan | se programa la ventana (docs/runbook-vectorial.md) | sí |
| 4.5 | resolución | REINDEX INDEX CONCURRENTLY vec.doc_chunks_hnsw_ix + vec.medir_hnsw('despues', ...) | 0.7 s | sí |
| 4.7 | verificación | índice válido, sin restos de reconstrucción, hinchazón bajo el umbral y DB-A05 apagada | hinchazón 0.2 % | sí |

## I6 · Partición DEFAULT con filas (DB-A06)

| t (s) | Fase | Qué se hizo | Resultado | OK |
|---|---|---|---|---|
| - | preparación | línea base: la partición DEFAULT de core.notifications está vacía | 0 filas | sí |
| 0.0 | provocación | 3 notificaciones con fecha fuera del rango de las particiones (enero de 2040) | van a core.notifications_default | sí |
| 0.1 | detección | SELECT * FROM core.alertas_db() -> DB-A06 | particiones DEFAULT con filas: core.notifications_default | sí |
| 0.1 | diagnóstico | rango de fechas de lo que cayó en la DEFAULT y configuración de pg_partman de la tabla | 3 filas entre 2040-01-10 y 2040-01-12; premake 12, mantenimiento automático on | sí |
| 0.1 | contención | la fecha está más allá de las particiones pre-creadas: no es un fallo del mantenimiento sino un dato fuera de rango; se decide si es legítimo (crear su partición) o erróneo (corregirlo en origen) | decisión: legítimo | sí |
| 0.5 | resolución | DETACH de la DEFAULT + partman.create_partition_time + INSERT SELECT + TRUNCATE + ATTACH DEFAULT, en una transacción con lock_timeout | 0.4 s | sí |
| 0.7 | verificación | DEFAULT vacía, las 3 filas en su partición de 2040 y DB-A06 apagada | [('core.notifications_p20400101', 3)] | sí |

## Límites del ensayo

* No se ensaya ningún canal de notificación: no hay Alertmanager en el repositorio. La «alerta» es la evaluación de `core.alertas_db()`, no un aviso a una persona.
* El PgBouncer del ensayo no es el de producción (no está en el repositorio): se configura con valores de demostración (pool de 4, modo transacción).
* Docker local, datos mínimos y un solo usuario provocando los incidentes: los tiempos miden el procedimiento.
* La cadena rota (I3) se provoca con permisos de superusuario saltándose el disparador de inmutabilidad: es lo único que puede alterar un evento, y la restauración solo es legítima si el export WORM es el registrado y supera la verificación externa.
* El acta la firma quien ejecuta el ensayo; no sustituye a la aprobación del responsable de operaciones.
