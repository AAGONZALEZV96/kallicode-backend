# Runbook de incidentes de base de datos (KC-DB-11-5)

Los seis incidentes previstos, en un solo documento, cada uno con **síntoma, diagnóstico, contención, resolución y
verificación**, vinculado a la **alerta** que lo dispara y ensayado en el entorno de demostración.

* Alertas: migración `065_alertas_db.sql`, función `core.alertas_db()`.
* Ensayo: `db/tools/ensayo_incidentes.py` · acta del último ensayo: [`runbook_incidentes_db_acta.md`](runbook_incidentes_db_acta.md).
* Pruebas: `packages/core/tests/integracion/test_runbook_incidentes.py`.
* Se apoya en: [`recuperacion_pitr.md`](recuperacion_pitr.md) (KC-DB-11-1), [`recuperacion_cm_hnsw.md`](recuperacion_cm_hnsw.md)
  (KC-DB-11-4), [`runbook-vectorial.md`](runbook-vectorial.md), [`export_worm_auditoria.md`](export_worm_auditoria.md) y
  [`runbooks/particionado_operaciones.md`](runbooks/particionado_operaciones.md).

## 0. Cómo usar este runbook

| Incidente | Alerta | Severidad | Sección |
|---|---|---|---|
| Bloqueo prolongado por una migración | `DB-A01` bloqueo_prolongado | crítica | [I1](#i1) |
| Agotamiento de conexiones con PgBouncer | `DB-A02` conexiones_agotadas | crítica | [I2](#i2) |
| Cadena de auditoría rota | `DB-A03` cadena_auditoria_rota | crítica | [I3](#i3) |
| Chequeo de integridad con hallazgos | `DB-A04` integridad_con_hallazgos | alta | [I4](#i4) |
| Hinchazón de un índice HNSW | `DB-A05` hnsw_hinchado | media | [I5](#i5) |
| Partición DEFAULT con filas | `DB-A06` particion_default_con_filas | alta | [I6](#i6) |

**Reglas comunes**

1. **Contener antes de resolver.** Primero se detiene el daño o la cola; la causa se arregla después. Cada sección separa las dos cosas.
2. **No hay comandos destructivos sin confirmar** el objetivo (pid, tenant, partición, evento). Los de este runbook se pegan con el valor ya
   sustituido, nunca con comodines.
3. **Se anota** en el registro del incidente: hora de la alerta, qué se hizo y cuándo, y el resultado de la verificación.
4. **Roles.** El diagnóstico se hace con `kallicode_admin` (que tiene `pg_monitor`, concedido en `db/00_roles.sql`). Solo I3 (restaurar un
   evento) y la terminación de sesiones de otros roles pueden requerir superusuario; se dice en cada caso.
5. **Una alerta que no se puede evaluar no es «verde».** `core.alertas_db()` devuelve `evaluada = false` (y `activa` nula) cuando falta un
   permiso o un dato: eso es un problema a resolver, no un «sin incidentes».

### Las alertas: `core.alertas_db()`

```sql
-- las baratas (cada minuto): DB-A01, DB-A02, DB-A05, DB-A06
SELECT alerta, activa, valor, umbral, detalle, runbook FROM core.alertas_db();
-- las caras (cada noche): además DB-A03 y DB-A04, que ejecutan core.validar_integridad()
SELECT alerta, activa, valor, umbral, detalle, runbook FROM core.alertas_db(p_integridad => true);
```

| Alerta | Condición (umbral por defecto) | Coste |
|---|---|---|
| `DB-A01` | alguna sesión espera un bloqueo desde hace ≥ **30 s** (`p_umbral_bloqueo_s`) | baja |
| `DB-A02` | conexiones ≥ **80 %** de `max_connections` (`p_umbral_conexiones`) **o** una transacción «idle in transaction» de ≥ **300 s** (`p_umbral_idle_tx_s`) | baja |
| `DB-A03` | V16 + V17 de `core.validar_integridad()` > 0 | alta (nocturna) |
| `DB-A04` | cualquier otro chequeo de `core.validar_integridad()` > 0 | alta (nocturna) |
| `DB-A05` | hinchazón de algún HNSW > **30 %** sobre su línea base (`p_umbral_hinchazon`), la misma de `docs/vigencia_embeddings_hnsw.md` | baja |
| `DB-A06` | alguna partición `DEFAULT` de `core`, `audit`, `vec` o `cm` tiene filas | baja |

La columna `runbook` de cada fila apunta a la sección de este documento. **La función define y evalúa las alertas; no avisa a nadie**: ver
[«Qué falta»](#que-falta).

---

<a id="i1"></a>
## I1 · Bloqueo prolongado por una migración — `DB-A01`

**Síntoma.** La aplicación se queda colgada o da tiempos de espera; en las trazas aparecen consultas «esperando». Una migración lleva minutos
sin terminar.

**Por qué ocurre.** Una migración pide un bloqueo exclusivo sobre una tabla. Si otra transacción (a menudo una olvidada, «idle in transaction»)
ya tiene un bloqueo sobre ella, la migración **espera**; y todo lo que llega después a esa tabla se pone **en cola detrás de la migración**, de
modo que una transacción olvidada acaba bloqueando toda la tabla. Las migraciones recientes del repositorio (no todas las antiguas) lo evitan abriendo con `SET LOCAL lock_timeout = '5s'` (la
migración falla rápido sin encolarse); una migración lanzada con `psql` a mano, o una que no lo incluya, no.

**Diagnóstico**

```sql
-- quién espera y por quién
SELECT a.pid, a.state, round(extract(epoch FROM now() - a.state_change)) AS espera_s,
       pg_blocking_pids(a.pid) AS bloqueada_por, left(a.query, 60)
  FROM pg_stat_activity a
 WHERE a.datname = current_database() AND a.wait_event_type = 'Lock' ORDER BY a.state_change;
-- quién bloquea: su estado y cuánto lleva la transacción abierta
SELECT b.pid, b.usename, b.state, round(extract(epoch FROM now() - b.xact_start)) AS tx_abierta_s, left(b.query, 60)
  FROM pg_stat_activity b
 WHERE b.pid IN (SELECT unnest(pg_blocking_pids(a.pid)) FROM pg_stat_activity a WHERE a.wait_event_type = 'Lock');
```

Identificar tres piezas: **la migración** (la espera cuya consulta es un `ALTER`/`CREATE INDEX`/…), **la cola** (aplicación esperando detrás) y **el bloqueante
raíz** (normalmente `idle in transaction` con una `tx_abierta_s` grande).

**Contención** (libera la cola sin tocar al bloqueante todavía)

```sql
SELECT pg_cancel_backend(<pid de la migración>);   -- cancela solo esa sentencia
```

La cola avanza en cuanto la migración desaparece. **No se termina al bloqueante a ciegas**: puede ser una transacción legítima larga.

**Resolución**

1. Comprobar que el bloqueante es una transacción olvidada (estado `idle in transaction`, sin actividad, dueño identificado). Si lo es:
   `SELECT pg_terminate_backend(<pid del bloqueante>);`
2. Relanzar la migración **siempre con `lock_timeout`**, para que si vuelve a haber un bloqueante falle rápido en vez de encolar:
   ```sql
   SET lock_timeout = '2s';   -- las migraciones recientes ya abren con SET LOCAL lock_timeout = '5s'
   -- … la sentencia de la migración
   ```
   Con el bloqueante todavía presente, el reintento falla con `lock_not_available` en segundos y **sin** bloquear a la aplicación.
3. Si el bloqueante no puede terminarse (transacción legítima), esperar a su fin y reprogramar la migración en una ventana valle.

**Verificación**

```sql
-- no queda nadie esperando
SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock';   -- 0
SELECT alerta, activa FROM core.alertas_db() WHERE alerta = 'DB-A01';                                      -- activa = false
```
y que la migración quedó aplicada **una sola vez** (`core.schema_migrations`; el runner abre una transacción por archivo, así que o se aplica
entera o no se aplica).

**Prevención.** Migraciones solo con el runner y con `SET LOCAL lock_timeout` al principio de cada archivo; `idle_in_transaction_session_timeout` en los roles de aplicación (ver I2);
`log_lock_waits = on` y `deadlock_timeout` bajo para dejar rastro.

---

<a id="i2"></a>
## I2 · Agotamiento de conexiones con PgBouncer — `DB-A02`

**Síntoma.** La aplicación falla con «no hay conexiones disponibles» o con `query_wait_timeout` de PgBouncer; o PostgreSQL rechaza conexiones por
`max_connections`. Las peticiones se acumulan sin que la base esté realmente cargada.

**Por qué ocurre.** Con PgBouncer en **modo transacción**, una conexión del servidor queda reservada mientras dura la transacción del cliente. Si
varias transacciones se quedan abiertas sin actividad (un bucle que no cierra, un cliente colgado), se ocupan todas las conexiones del pool y los
demás clientes **esperan**. En el otro extremo, demasiados clientes directos agotan `max_connections`.

**Diagnóstico**

```text
-- en la consola de PgBouncer (base «pgbouncer»)
SHOW POOLS;     -- cl_active, cl_waiting (clientes esperando), sv_active, sv_idle, maxwait
SHOW CLIENTS;   SHOW SERVERS;
```
```sql
-- en PostgreSQL: quién ocupa las conexiones
SELECT usename, state, count(*) AS n, round(max(extract(epoch FROM now() - state_change))) AS la_mas_antigua_s
  FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1, 2 ORDER BY 3 DESC;
-- las que retienen el pool: transacciones abiertas sin actividad
SELECT pid, usename, round(extract(epoch FROM now() - xact_start)) AS tx_abierta_s, left(query, 50)
  FROM pg_stat_activity WHERE state = 'idle in transaction' ORDER BY xact_start;
```
La señal inequívoca es `cl_waiting ≥ 1` con `sv_active = default_pool_size` **y** sesiones `idle in transaction` del rol de la aplicación.

**Contención**

```sql
-- libera el pool: termina las transacciones colgadas del rol de la aplicación con más de N segundos
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE state = 'idle in transaction' AND usename = '<rol de la aplicación>' AND now() - state_change > interval '<N> seconds';
```
Los clientes que esperaban reciben su conexión. (Los clientes cuyas transacciones se terminaron verán un error en su siguiente sentencia: es lo
esperado.) Si el origen es un despliegue defectuoso, **revertirlo** es parte de la contención. En PgBouncer, `PAUSE <base>` / `RESUME <base>`
permiten detener y reanudar la entrada de clientes durante la intervención.

**Resolución**

1. Corregir la causa en la aplicación (transacción sin cerrar). Mientras tanto, que **una transacción olvidada no pueda retener el pool**:
   ```sql
   ALTER ROLE <rol de la aplicación> SET idle_in_transaction_session_timeout = '<valor>';   -- p. ej. 60 s; valorarlo con el equipo de aplicación
   ```
   Las conexiones del pool que ya estaban abiertas no tienen el ajuste nuevo: en PgBouncer, `RECONNECT <base>` las renueva.
2. Dimensionar el pool con una regla: **la suma de los `default_pool_size` de todos los PgBouncer ≤ `max_connections` − conexiones reservadas**
   (`superuser_reserved_connections`, migraciones, monitorización, trabajos de `pg_cron`). Subir `max_connections` o el pool sin revisar esa suma solo
   mueve el problema.
3. **Las migraciones y todo lo que use `pg_advisory_lock` de sesión o `SET` de sesión deben conectar directamente a PostgreSQL**, no por el pool en modo
   transacción: el runner de migraciones toma un `pg_advisory_lock` y, con transaction pooling, ese bloqueo no pertenecería a una sesión estable.
   El código de aplicación ya usa `set_config(..., true)` (local a la transacción) para el tenant, que sí es compatible.

**Verificación**

```text
SHOW POOLS;   -- cl_waiting = 0
```
```sql
SELECT alerta, activa, detalle FROM core.alertas_db() WHERE alerta = 'DB-A02';   -- activa = false
```
y que una consulta cualquiera a través del pool responde.

**Prevención.** `idle_in_transaction_session_timeout` por rol; `query_wait_timeout` y `server_idle_timeout` en PgBouncer; alertar también en el lado de
PgBouncer (`cl_waiting`), que **la base no ve** (`core.alertas_db()` solo ve el lado de PostgreSQL; ver «Qué falta»).

---

<a id="i3"></a>
## I3 · Cadena de auditoría rota — `DB-A03`

> **Es un incidente de seguridad, no solo de datos.** `audit.audit_events` es append-only y sellada por hash: los eventos no cambian. Una cadena rota
> significa que alguien con privilegios altos la alteró, que hubo un fallo grave de almacenamiento o un error de restauración. **Se avisa al
> responsable de seguridad desde el primer minuto** y no se cierra hasta conocer la causa.

**Síntoma.** `DB-A03` activa: `core.validar_integridad()` da V16 (sello alterado, eslabón perdido o eventos faltantes) o V17 (la cabeza de cadena no
cuadra) > 0; `audit.verify_chain(<tenant>)` devuelve `integra = false`.

**Diagnóstico**

```sql
-- qué tenant y cuál es el primer evento roto
SELECT integra, primer_evento_roto, eventos_verificados, eventos_esperados, eventos_observados FROM audit.verify_chain('<tenant>');
SELECT chequeo, problemas FROM core.validar_integridad() WHERE chequeo IN ('V16_cadena_auditoria', 'V17_cabeza_cadena');
```
```bash
# ¿qué cambió respecto al último export WORM? (alterados, borrados y posteriores al export; solo lectura)
python db/tools/worm_auditoria.py comparar <ruta>/<export>.manifest.json
# ¿el propio export es fiable? (verificación externa + hash del manifiesto REGISTRADO en la base)
python db/tools/worm_auditoria.py verificar <ruta>/<export>.manifest.json --sha256-manifiesto <manifiesto_sha256 de audit.worm_exports>
```
`comparar` dice **qué evento y qué columnas** difieren del export. Los «añadidos» no son necesariamente una manipulación (una partición en curso sigue
recibiendo eventos legítimos); los **alterados** y los **borrados** sí lo son.

**Contención** (preservar evidencia y no empeorar nada)

1. **Conservar el estado alterado como evidencia** antes de tocar nada:
   ```sql
   COPY (SELECT * FROM audit.audit_events WHERE tenant_id = '<tenant>' ORDER BY id) TO STDOUT WITH CSV HEADER;   -- a un archivo con acceso restringido
   ```
2. **No purgar ni descartar particiones de auditoría** (la política de `audit.audit_events` es `conservar`; comprobarlo) y **no exportar** la partición
   rota: `worm_auditoria.py exportar` se niega a exportar una cadena que no es íntegra, y la purga exige un comprobante WORM vigente.
3. Identificar **quién pudo hacerlo**: solo un superusuario (o quien desactive los disparadores) puede alterar un evento. No hay `pgaudit` en el
   repositorio: revisar los registros del servidor y los accesos de superusuario (ver «Qué falta»).

**Resolución** (solo si la causa está entendida y la autoridad de seguridad lo autoriza)

*Si el evento alterado está en una partición ya exportada*, devolverlo a su contenido original desde el export **verificado**:

```bash
python db/tools/worm_auditoria.py restaurar <ruta>/<export>.manifest.json --evento <id> --sha256-manifiesto <manifiesto_sha256 registrado>
```
Requiere rol **superusuario** (desactiva los disparadores solo en esa sentencia). Verifica el export (archivo, sellos y hash registrado) antes de tocar nada, y
**deshace todo** si, tras restaurar, la cadena del tenant no vuelve a ser íntegra (el daño no era solo ese evento).

*Si el evento no está en ningún export* (partición reciente) o *hay eventos borrados*: **no se «arregla» la cadena a mano**. Se restaura la partición desde
una copia a un instante anterior a la alteración (`docs/recuperacion_pitr.md`) en una instancia nueva y se compara. La herramienta actual no inserta eventos
borrados desde un export (ver «Qué falta»).

**Verificación**

```sql
SELECT integra, eventos_verificados FROM audit.verify_chain('<tenant>');                       -- integra = true
SELECT chequeo, problemas FROM core.validar_integridad() WHERE chequeo IN ('V16_cadena_auditoria', 'V17_cabeza_cadena');  -- 0 filas con problemas
SELECT alerta, activa FROM core.alertas_db(p_integridad => true) WHERE alerta = 'DB-A03';       -- activa = false
```
y que `comparar` ya no encuentra diferencias con el export. **Verificar la cadena de todos los tenants**, no solo del afectado
(`SELECT h.tenant_id FROM audit.chain_heads h CROSS JOIN LATERAL audit.verify_chain(h.tenant_id) v WHERE NOT v.integra;` debe dar 0 filas).

**Prevención.** Exports WORM al día (`worm_auditoria.py pendientes`); superusuario con uso mínimo y registrado; el export en almacenamiento realmente inmutable.

---

<a id="i4"></a>
## I4 · Chequeo de integridad con hallazgos — `DB-A04`

**Síntoma.** La ejecución nocturna de `core.validar_integridad()` devuelve alguna fila con `problemas > 0` distinta de V16/V17 (esas son I3).

**Diagnóstico**

```sql
SELECT chequeo, descripcion, problemas FROM core.validar_integridad() WHERE problemas > 0 ORDER BY chequeo;
```

Cada chequeo cuenta un tipo de incoherencia. Para ver **qué filas** son, abrir la definición y copiar la consulta del chequeo:

```sql
SELECT pg_get_functiondef('core.validar_integridad()'::regprocedure);   -- buscar el nombre del chequeo, p. ej. V01_suscripcion_vigente
```

Los 26 chequeos y lo que cuentan (de la propia función):

| Chequeo | Qué cuenta |
|---|---|
| V01 suscripcion_vigente | Tenants sin suscripción vigente (o con más de una) |
| V02 espejo_ticket_job | Tickets activos cuya etapa no corresponde al estado de su job |
| V03 gate3_sin_previos | Aprobaciones de Gate 3 sin aprobación previa de gates 1 y 2 |
| V04 produccion_sin_firmas | Tickets en producción sin las tres firmas de gates |
| V05 spec_recomendada | Specs sin exactamente una alternativa recomendada |
| V06 spec_aprobada_sin_eleccion | Specs aprobadas (Gate 1) sin alternativa elegida |
| V07 matriz_qa_completa | Corridas de QA que no cubren todos los criterios de la spec |
| V08 qa_criterio_fantasma | Resultados de QA con `criterio_id` que no existe en la spec de la corrida |
| V09 produccion_con_bloqueantes | Tickets en producción con hallazgos de seguridad bloqueantes abiertos |
| V10 cuota_cuadra | Ciclos cuyo agregado no cuadra con la suma de `usage_loc` |
| V11 lineas_coherentes | Líneas ocupadas sin job activo o jobs con línea no reflejada |
| V12 lineas_dentro_del_plan | Tenants con más jobs en línea que líneas de su plan |
| V13 duplicados_validos | Tickets duplicados que apuntan a otro duplicado (cadenas) |
| V14 embeddings_tickets | Tickets activos (> 1 h) sin embedding de deduplicación |
| V15 dimension_embeddings | Embeddings con dimensión distinta de 1024 |
| V16 cadena_auditoria | Cadena de auditoría rota (**→ I3**) |
| V17 cabeza_cadena | `chain_heads` que no cuadra con los eventos (**→ I3**) |
| V18 webhooks_estancados | Entradas de `webhook_inbox` fallidas hace más de 24 h |
| V19 adjuntos_huerfanos | Adjuntos sin ticket asociado hace más de 48 h |
| V20 gate_spec_coherente | Firmas de Gate 1 cuya spec no pertenece al mismo ticket |
| V21 coherencia_tenant | Filas cuyo tenant no coincide con el de su padre |
| V22 consumo_no_negativo | Ciclos de cuota con `loc_consumidas` negativo |
| V23 renovacion_vencida | Suscripciones activas con `renueva_el` vencido sin ciclo nuevo |
| V24 bloqueantes_sin_adjudicar | Hallazgos de seguridad bloqueantes abiertos hace más de 48 h |
| V25 org_borrada_con_actividad | Organizaciones borradas con actividad posterior al borrado |
| V26 tabla_sin_retencion | Tablas de `core`, `audit`, `vec` y `cm` sin política de retención declarada |

**Contención.** **No se corrige a ciegas.** Un hallazgo es la *consecuencia* de algo (un bug, una migración, una edición manual): arreglar solo la fila
y no la causa lo hace reaparecer. Identificar el tenant o los tickets afectados, comprobar si el hallazgo **bloquea** algo (p. ej. V09 y V24 afectan a
tickets en producción) y, si el flujo que lo produce sigue activo, detenerlo o avisar a su responsable. V15/V21 apuntan a un fallo de aislamiento o de
datos: tratarlos como urgentes.

**Resolución.** Corregir los datos con el dueño del dato y arreglar la causa. Ejemplo ensayado (V01): dar de alta la suscripción del tenant afectado.
Para el resto, la corrección depende del chequeo (consultar su definición y a su equipo responsable).

**Verificación**

```sql
SELECT count(*), coalesce(sum(problemas), 0) FROM core.validar_integridad();    -- 26 chequeos, 0 hallazgos
SELECT alerta, activa FROM core.alertas_db(p_integridad => true) WHERE alerta = 'DB-A04';   -- activa = false
```

**Prevención.** Que el job nocturno falle (y avise) ante cualquier hallazgo; cada hallazgo nuevo con su causa registrada.

---

<a id="i5"></a>
## I5 · Hinchazón de un índice HNSW — `DB-A05`

**Síntoma.** El recall semántico baja o las búsquedas vectoriales se ralentizan; el índice ocupa mucho más de lo que corresponde a sus filas vivas.
`DB-A05` activa: hinchazón > 30 % sobre la línea base.

**Por qué ocurre.** La rotación diaria del refresco borra y reescribe vectores. El HNSW **no devuelve el espacio** con el `VACUUM`: las tuplas muertas
quedan en el grafo y los bytes por fila viva crecen (`docs/vigencia_embeddings_hnsw.md`).

**Diagnóstico**

```sql
SELECT indice, tamano_bytes, filas_vivas, filas_muertas, bytes_por_fila, linea_base_bytes_por_fila, hinchazon_pct, muertas_pct, ultimo_vacuum, ultima_reconstruccion
  FROM vec.estado_hnsw() ORDER BY hinchazon_pct DESC NULLS LAST;
SELECT * FROM vec.hnsw_metricas ORDER BY id DESC LIMIT 10;                       -- historial de construcciones y mediciones
SELECT * FROM pg_stat_progress_create_index;                                      -- ¿ya hay una reconstrucción en curso?
```

**Contención.** No es una urgencia de servicio: el índice sigue siendo válido y las búsquedas funcionan. **No se reconstruye en hora punta** (satura el disco: en
KC-DB-11-4 hubo paradas puntuales de hasta 14 s en una operación aislada). Si hay una reconstrucción en curso, esperar a que termine.

**Resolución**

```sql
SET maintenance_work_mem = '2GB';                                    -- el valor de docs/runbook-vectorial.md §7; con menos se construye en disco
REINDEX INDEX CONCURRENTLY vec.<índice>;                             -- no bloquea lecturas ni escrituras
SELECT * FROM vec.medir_hnsw('despues', <minutos que tardó>, '<índice>');   -- renueva la línea base
```
En un índice particionado, `REINDEX INDEX CONCURRENTLY` reconstruye cada partición por separado. Requisitos de memoria, disco (≈ 8,3 GB por millón de filas
**además** del índice vigente) y ventana: `docs/runbook-vectorial.md` §2 y §7.

**Verificación**

```sql
SELECT indice, hinchazon_pct FROM vec.estado_hnsw() WHERE indice = '<índice>';       -- bajo el umbral
SELECT i.indisvalid FROM pg_index i WHERE i.indexrelid = 'vec.<índice>'::regclass;   -- true
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'vec' AND (c.relname LIKE '%\_ccnew%' OR c.relname LIKE '%\_ccold%');   -- 0: sin restos de reconstrucciones a medias
SELECT alerta, activa FROM core.alertas_db() WHERE alerta = 'DB-A05';                -- activa = false
```
Si un `REINDEX` se interrumpe deja un índice inválido o restos `_ccnew`: ver `docs/runbook-vectorial.md` §5 (recuperación).

**Prevención.** La rutina semanal de `pg_cron` (`hnsw_reindex_*`) y la métrica `kc_codemapping_hnsw_hinchazon_porcentaje` del worker.

---

<a id="i6"></a>
## I6 · Partición DEFAULT con filas — `DB-A06`

**Síntoma.** `DB-A06` activa: alguna tabla particionada por fecha tiene filas en su partición `DEFAULT`. No falla nada de inmediato, pero esas filas **no
se purgan** (el motor de retención nunca toca la `DEFAULT`), no se archivan y hacen más lentas las consultas por rango.

**Por qué ocurre.** (a) Un dato con una fecha **fuera del rango** de las particiones creadas (un error de fecha en origen o una fecha futura más allá de las
12 pre-creadas), o (b) el **mantenimiento de `pg_partman` no corre** y falta la partición del mes en curso.

**Diagnóstico**

```sql
SELECT * FROM core.alertas_db() WHERE alerta = 'DB-A06';                                   -- qué tabla(s)
SELECT count(*), min(<columna de fecha>), max(<columna de fecha>) FROM <tabla>_default;    -- qué fechas tienen
SELECT parent_table, premake, automatic_maintenance, partition_interval FROM partman.part_config WHERE parent_table = '<tabla>';
```
La **fecha de las filas** separa los dos casos: si cae en el pasado reciente o en el mes en curso, falla el mantenimiento (b); si está años en el futuro o en el
pasado, es un dato fuera de rango (a).

**Contención.** (a) Decidir si el dato es **legítimo** (se crea su partición) o **erróneo** (se corrige en origen; no se crea una partición para un error).
(b) Lanzar el mantenimiento: `CALL partman.run_maintenance_proc();` (o, en un trabajo de `pg_cron`/`pg_partman_bgw` parado, restablecerlo).

**Resolución** (tablas normales: `core.notifications`, `core.webhook_inbox`, `core.usage_tokens`, `core.job_transitions`). Crear la partición y mover las
filas, **en una transacción corta con `lock_timeout`** (el `DETACH` bloquea la tabla padre mientras dura):

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE <tabla> DETACH PARTITION <tabla>_default;
SELECT partman.create_partition_time('<tabla>', ARRAY['<fecha de las filas>'::timestamptz]);
INSERT INTO <tabla> SELECT * FROM <tabla>_default;            -- las filas se encaminan solas a la partición nueva
TRUNCATE <tabla>_default;
ALTER TABLE <tabla> ATTACH PARTITION <tabla>_default DEFAULT;
COMMIT;
```

> **`audit.audit_events` es distinta y NO se resuelve así.** El disparador de sellado fuerza `creado_en = now()` y recalcula el sello: reinsertar los eventos
> por la vía normal **cambiaría su fecha y su sello y rompería la cadena**. Si hay eventos en la `DEFAULT` de la auditoría (mantenimiento caído y falta la
> partición del mes), hay que crear la partición y moverlos **conservando `id`, `creado_en` y sello**, con los disparadores desactivados
> (`SET LOCAL session_replication_role = replica`, superusuario), y verificar después con `audit.verify_chain` de todos los tenants (I3). **Este caso está
> documentado pero NO se ha ensayado** (ver «Qué falta»).

**Verificación**

```sql
SELECT count(*) FROM <tabla>_default;                                                       -- 0
SELECT tableoid::regclass, count(*) FROM <tabla> WHERE <criterio de las filas> GROUP BY 1;   -- están en su partición, con el mismo recuento
SELECT alerta, activa FROM core.alertas_db() WHERE alerta = 'DB-A06';                        -- activa = false
```

**Prevención.** Que el mantenimiento de `pg_partman` corra (`docs/runbooks/particionado_operaciones.md`), validar las fechas en origen y mantener ≥ 12 particiones
futuras.

---

<a id="que-falta"></a>
## Qué falta y límites

* **Las alertas no avisan a nadie.** `core.alertas_db()` las define y evalúa, pero en el repositorio no hay un Alertmanager, un canal de guardia ni un job que
  la ejecute: hace falta **programar** su ejecución (cada minuto las baratas, cada noche las de integridad) y **conectar el resultado a una notificación**. Mientras
  tanto, es una consulta que alguien tiene que lanzar.
* **El lado de PgBouncer no lo ve la base.** `cl_waiting` y `maxwait` solo están en la consola de PgBouncer; una alerta completa de I2 necesita exportarlos.
* **El PgBouncer de producción no está en el repositorio.** El ensayo usó uno de demostración (pool de 4, modo transacción); los valores reales (pool, timeouts,
  roles) hay que confirmarlos con quien lo despliega.
* **No se sabe «quién» alteró la auditoría.** No hay `pgaudit` ni un registro de sentencias de superusuario en el repositorio; en I3 la investigación de la causa
  depende de los registros del servidor.
* **I3 no restaura eventos borrados** desde un export (solo corrige eventos alterados); para eso hay que usar una copia PITR.
* **La variante de I6 para `audit.audit_events` está documentada, no ensayada.**
* **El ensayo corre en Docker local con datos mínimos** y un solo usuario provocando los incidentes: mide el procedimiento, no una promesa de producción.
* **El canal de escalado** (a quién se llama y cuándo) **no está definido**: aquí solo se dice «avisar a seguridad» (I3) o «al equipo de aplicación» (I2, I4).
