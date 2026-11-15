# Informe de cumplimiento de retención (KC-DB-10-4)

Migración `db/migrations/057_informe_retencion.sql` · script `db/tools/informe_retencion.sql` (lo envuelve
`db/tools/informe_retencion.sh`) · prueba `packages/core/tests/integracion/test_informe_retencion.py` · cierra
DB-F23.

Este es el artefacto que se presenta en una auditoría de cumplimiento. Muestra, tabla por tabla:

- la política de retención declarada;
- el dato más antiguo que queda;
- la última purga;
- las particiones de auditoría exportadas a WORM;
- si hay alguna desviación.

## 1. Cómo se obtiene

| Dónde | Qué hace | Artefacto |
|---|---|---|
| CI de cada PR, job `database`, paso «Informe de cumplimiento de retención» | Ciclo completo de retención y después el informe. Falla si hay alertas o hallazgos de integridad | `informe-retencion` |
| `.github/workflows/retencion_nocturno.yml` (04:00 UTC) | Lo mismo sobre una base nueva con los datos de demostración. Guarda el informe 90 días | `informe-retencion-nocturno` |
| Smoke post-deploy | `db/tools/informe_retencion.sh <destino> --sin-ciclo` contra la base desplegada. Solo lee y falla si hay alertas | se publica desde el workflow de deploy |
| A mano | `psql -v ON_ERROR_STOP=1 -f db/tools/informe_retencion.sql` como `kallicode_admin` | — |

El ciclo completo consiste en:

1. `core.purgar_retencion()` (10-1), con el gancho WORM previo al descarte de auditoría (10-2);
2. `core.purgar_idempotency_keys()` (10-3);
3. `core.minimizar_pii()` (10-3).

Al terminar el ciclo, `core.validar_integridad()` tiene que devolver 0 problemas en los 26 chequeos, V26 incluido.

> **Pendiente:** todavía no hay workflow de deploy en `.github/workflows/`. Cuando exista, su smoke tiene que
> ejecutar el script con `--sin-ciclo` y publicar la carpeta resultante. El smoke no purga, porque las purgas
> reales las lanza pg_cron en la ventana nocturna.

La consulta versionada es `core.reporte_cumplimiento_retencion(p_ahora, p_tolerancia)`, con una tolerancia de 7 días
por defecto. `core.alertas_retencion(p_ahora, p_tolerancia)` devuelve solo las filas con alerta. Las dos son de
solo lectura y solo las puede ejecutar `kallicode_admin`.

## 2. Qué tablas cubre

El informe saca **una fila por cada tabla** (no por partición) de los esquemas `core`, `audit`, `vec` y `cm`. Las
obtiene de `core.tablas_retencion()` y les añade:

- toda entidad declarada en `core.retention_policies` o en `core.retencion_dedicada`, aunque la tabla no exista
  (sale con estado `inexistente`);
- una fila por cada política activa de un tenant.

Ninguna tabla queda fuera. V26 cuenta las tablas sin política, de modo que **una migración que crea una tabla
tiene que declarar su política en la misma migración**, o `make check-db` y el CI fallan. Hay tres formas de
declararla:

| Fuente | Cuándo se usa |
|---|---|
| `core.retention_policies` con `drop`, `detach` o `archive` | La tabla la purga el motor (`core.purgar_retencion`) con un plazo en días |
| `core.retention_policies` con `conservar` | No se purga. El `motivo` explica por qué |
| `core.retencion_dedicada` | La purga otro proceso con su propio plazo: TTL o housekeeping |

La migración 057 declaró `conservar` para todas las tablas que no tenían política, con su motivo. Eso recoge la
situación de hecho, porque nada las borraba. Fijar un plazo es una decisión de negocio y se hace con un `UPDATE`
(`action = 'drop'`, `retention_days = N` y `columna_tiempo` si la tabla no está particionada).

## 3. Columnas y estados

| Columna | Significado |
|---|---|
| `origen` | `retention_policies`, `purga_dedicada` o `sin_politica` |
| `accion` y `retencion` | Qué se hace con los datos y el plazo declarado (sin plazo en `conservar`) |
| `dato_mas_antiguo` | El valor mínimo de la columna de fecha. En las purgas dedicadas, solo de las filas que la purga alcanza; por ejemplo, adjuntos sin ticket |
| `referencia` y `antiguedad` | La fecha con la que se mide y su antigüedad. En las tablas **particionadas por rango** es el límite superior de la partición que contiene el dato más antiguo (ver más abajo) |
| `exceso` | `antiguedad - retencion`. Si es positivo, el dato está fuera de plazo |
| `ultima_purga` y `filas_ultima_purga` | Para el motor, la última acción real en `core.retention_purgas`. Para las purgas dedicadas, la última ejecución en `core.purgas_log` |
| `ultima_omision` | Si lo último que hizo el motor fue omitir un descarte, el motivo; por ejemplo, «sin comprobante de export WORM» |
| `particiones`, `particiones_worm` y `ultimo_export_worm` | Para `audit.audit_events`: cuántas particiones hay, cuántas tienen comprobante WORM (`audit.worm_exports`) y la fecha del último export |
| `justificacion` | El motivo de la política o la causa de la desviación |

| Estado | Desviación | Alerta | Significado |
|---|---|---|---|
| `cumple` | no | no | La antigüedad está dentro del plazo, o la tabla está vacía |
| `en_tolerancia` | no | no | Fuera de plazo por 7 días o menos: la purga está pendiente de la siguiente ejecución |
| `excede` | **sí** | **sí** | Fuera de plazo por más de 7 días |
| `conservar` | no | no | Política declarada de no purgar, con su motivo |
| `sin_politica` | **sí** | no | No hay política o está desactivada. V26 la cuenta |
| `no_medible` | **sí** | no | Tiene plazo pero no hay una columna de fecha válida con la que medirlo |
| `inexistente` | **sí** | no | La política declara una tabla que no existe |
| `ignorada` | no | no | Política de tenant que el motor no aplica: alarga el plazo de la plataforma, o la plataforma no purga esa tabla |

**Granularidad de partición.** El motor descarta particiones enteras: una partición se elimina cuando su límite
superior queda por debajo de «ahora − plazo». Con particiones mensuales, una fila puede vivir hasta un mes más que
el plazo y eso no es un incumplimiento. Por eso, en las tablas particionadas por rango, la antigüedad se mide desde
el límite superior de su partición. Si se midiera desde la fila, el informe daría una alerta falsa casi todo el mes.

## 4. Compromisos con el cliente y cómo se acreditan

| Compromiso | Tablas | Política | Evidencia en el informe |
|---|---|---|---|
| Evidencia del ciclo de vida conservada al menos un año (D14) | `core.gate_signatures`, `core.llm_steps`, `core.qa_runs`, `core.qa_results`, `core.job_transitions`, `core.usage_loc` | `conservar`. Son tablas append-only (025) y se eliminan solo con su tenant | Estado `conservar`. Ninguna purga las toca, así que se cumple el mínimo de un año |
| Webhooks 90 días | `core.webhook_inbox` (y `core.notifications`) | `drop`, 90 días, por partición | Estado `cumple`, con su `referencia` y su `ultima_purga` |
| Auditoría permanente | `audit.audit_events` | `conservar` | Particiones y particiones con comprobante WORM. Si un día se decide descartar, solo se podrá con un export WORM verificado (10-2), y quedará en `ultima_purga` |
| TTL de idempotencia de 48 h | `core.idempotency_keys` | Dedicada: `core.purgar_idempotency_keys()` | `ultima_purga` sale de `core.purgas_log` |
| Sesiones expiradas, 7 días | `core.refresh_tokens` | Dedicada: housekeeping | `dato_mas_antiguo` según `expira_en` |
| Adjuntos huérfanos, 48 h | `core.ticket_attachments` | Dedicada: housekeeping, solo filas con `ticket_id` NULL | `dato_mas_antiguo` de los huérfanos |
| Minimización de PII | `refresh_tokens.ip` y `user_agent`; `invitations.email` | Ventanas en `core.pii_ventanas` (anonimizar, no borrar) | Sección 5 del informe (`core.purgas_log`, tareas `pii:*`) |

## 5. Si salta una alerta

Una alerta se dispara en tres sitios:

- el housekeeping la registra como `housekeeping.retencion_excedida` (nivel error), con la tabla, el tenant y el
  exceso;
- el CI y el workflow nocturno fallan;
- el script devuelve código de salida 1.

Pasos:

1. **Mirar la columna `ultima_omision`.** Si el motor omitió el descarte, el motivo dice por qué. Por ejemplo,
   «tiene N entregas pendientes» en `webhook_inbox`, o «sin comprobante de export WORM» en auditoría.
2. **Comprobar que pg_cron se ejecuta:** `SELECT * FROM cron.job_run_details WHERE jobname = 'retencion_purgas'`.
   Ver `docs/retencion_purgas.md`.
3. **Simular antes de purgar a mano:** `SELECT * FROM core.purgar_retencion(now(), true)`. Si el resultado es el
   esperado, ejecutar sin simulación.
4. **Volver a generar el informe** y adjuntar las dos versiones, la de antes y la de después, como evidencia del
   incidente.

## 6. Límites conocidos

- **El housekeeping no escribe en `core.purgas_log`.** `core.refresh_tokens` y `core.ticket_attachments` salen sin
  `ultima_purga`. El cumplimiento se acredita igualmente por `dato_mas_antiguo`, que se mide siempre.
- **El dato más antiguo se calcula con `min()`.** Solo se calcula en las tablas que tienen columna de fecha: las que
  tienen plazo y las particionadas por rango. Las tablas `conservar` no particionadas no se miden, porque no tienen
  plazo con el que comparar.
- **Las políticas de tenant solo acortan el plazo de la plataforma.** Se informan en filas propias (`tenant` no
  nulo) y se miden con el borrado por filas, sin la granularidad de partición.
