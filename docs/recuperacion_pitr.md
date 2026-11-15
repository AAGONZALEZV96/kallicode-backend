# Recuperación a un punto en el tiempo (PITR): RPO, RTO y procedimiento (KC-DB-11-1)

Migración `061_objetivos_recuperacion.sql` · herramientas `db/tools/ensayo_pitr.py` y `db/tools/archivar_wal.sh` ·
override de compose `docker-compose.pitr.yml` · ensayo semanal `.github/workflows/pitr_semanal.yml` · pruebas
`packages/core/tests/integracion/test_recuperacion_pitr.py` · acta del último ensayo: `docs/recuperacion_pitr_acta.md`.

## 1. Objetivos

| | Propuesto | Cómo se garantiza |
|---|---|---|
| **RPO ≤ 5 min** (cuánto dato puede perderse) | 300 s | Archivado continuo de WAL con `archive_timeout = 60 s`: el peor caso es lo escrito desde el último segmento archivado (≤ 60 s + el tiempo de archivado). Hay que **vigilar el archivado** (§5): si falla, el RPO deja de cumplirse sin que nada lo avise. |
| **RTO ≤ 60 min** (cuánto se tarda en volver a operar) | 3600 s | Copia base + reproducción del WAL en una instancia nueva. Depende del tamaño de la base y del WAL a reproducir desde la copia base: **hay que repetir el ensayo con el volumen real** (§7). |

Los objetivos están declarados en `core.objetivos_recuperacion` como **`propuesto`**. Que sean **aprobados por el
Architecture Board** es una decisión humana que ni el ensayo ni este documento sustituyen: cuando se apruebe, se
registra con `SELECT core.aprobar_objetivos_recuperacion(<id>, '<quién>', '<referencia del acta>')` (solo el rol
administrador; exige quién y la referencia, y deja un único conjunto aprobado: los anteriores pasan a `sustituido`).

## 2. Qué se protege y con qué

* **Copia base** (`pg_basebackup`, formato plano, `-X stream`): la foto física de toda la instancia (todas las bases,
  roles y tablespaces). Se hace periódicamente (p. ej. diaria): cuanto más reciente, menos WAL hay que reproducir (RTO).
* **WAL archivado** (`archive_command` → `db/tools/archivar_wal.sh`): cada segmento terminado (16 MB o 60 s de
  actividad) se copia a un archivo aparte. Es lo que permite llegar a **cualquier instante** posterior a la copia base.
* El script **no sobrescribe jamás** un segmento ya archivado y distinto, es idempotente en el reintento y copia por
  fichero temporal + renombrado (nunca queda un segmento a medias con el nombre bueno).

Archivado activado con `docker compose -f docker-compose.yml -f docker-compose.pitr.yml up -d postgres`.

## 3. Procedimiento de restauración (a un instante T o hasta el final del WAL)

Se restaura **siempre en una instancia nueva**; la original, si existe, no se toca hasta verificar.

1. **Decidir el objetivo.** Hasta el final del WAL (desastre: pérdida mínima) o a un instante T anterior a un error
   humano (p. ej. un `DELETE` sin `WHERE`). Para T, usar un instante **anterior** al error, en UTC.
2. **Detener la entrada** a la base dañada (o no hay nada que detener si el servidor se perdió).
3. **Si la base original sigue viva**, forzar el cierre del segmento actual para que el WAL hasta el error esté archivado:
   `SELECT pg_switch_wal();` y esperar a que `core.estado_archivado_wal()` lo refleje.
4. **Preparar el directorio de datos** de la instancia nueva copiando la copia base elegida (la más reciente anterior a T).
5. En `postgresql.auto.conf` de la instancia nueva:
   ```
   restore_command = 'cp /ruta/del/archivo/wal/%f %p'
   recovery_target_time = '2026-11-15 18:36:52+00'   # omitir para llegar al final del WAL
   recovery_target_action = 'promote'
   ```
   y crear el fichero vacío `recovery.signal`.
6. **Arrancar sin `pg_cron` ni `pg_partman_bgw` precargados** (`shared_preload_libraries=pg_stat_statements`) y sin
   archivar (`archive_mode=off`). Si no, la copia restaurada dispararía las purgas de retención, el TTL, la minimización
   de PII y el mantenimiento de particiones **antes de que se verifique**.
7. Esperar a que acepte conexiones y `SELECT pg_is_in_recovery()` devuelva `false` (RTO medido hasta aquí).
8. **Verificar** (obligatorio antes de usarla):
   ```sql
   -- cadena de auditoría íntegra para TODOS los tenants (debe devolver 0 filas)
   SELECT h.tenant_id FROM audit.chain_heads h CROSS JOIN LATERAL audit.verify_chain(h.tenant_id) v WHERE NOT v.integra;
   -- los 26 chequeos de integridad (debe devolver 0 filas)
   SELECT * FROM core.validar_integridad() WHERE problemas > 0;
   ```
9. **Poner en servicio**: reactivar `pg_cron` y `pg_partman_bgw` (reiniciar con la configuración normal y el archivado
   activado), tomar una **nueva copia base** y apuntar la aplicación a la instancia restaurada.

`python db/tools/ensayo_pitr.py --salida <dir>` hace los pasos 4–8 de forma automática y repetible sobre contenedores
desechables y deja el acta con los tiempos.

## 4. Qué hace el ensayo

1. Levanta un origen con archivado continuo, aplica el esquema completo y siembra 3 tenants con cadena de auditoría real.
2. Copia base y carga continua (un evento de auditoría cada ~0,2 s, cada confirmación apuntada).
3. Fija un instante T con la carga pausada un momento (sin transacciones a medias), y provoca un «accidente» posterior:
   borra las claves de idempotencia. Cierra el segmento y espera a que se archive.
4. **Desastre**: `docker kill` (SIGKILL) del origen; se da por perdido con su disco.
5. **Restauración A (PITR a T)** y **B (hasta el final del WAL archivado)** en instancias nuevas, cada una cronometrada.
6. Comprueba: el punto A es exacto (eventos hasta T, ninguno posterior, claves de idempotencia recuperadas); en B cuenta
   los eventos confirmados que se perdieron (RPO real); y en ambas `audit.verify_chain()` íntegra para todos los tenants y
   `core.validar_integridad()` con 0 hallazgos.

## 5. Vigilancia: sin ella el RPO es una promesa vacía

`SELECT * FROM core.estado_archivado_wal()` expone `archivado_activado`, `wal_level`, `archive_timeout_s`,
`archivado_al_dia` (el archivado está activo y el último intento no falló), el último segmento archivado, `fallos` y
`ultimo_fallo_en`. **Alerta recomendada:** `archivado_activado = false` o `archivado_al_dia = false`, y que el directorio de
archivo no se llene. `segundos_desde_ultimo` no es criterio de alerta por sí solo: con la base inactiva no hay WAL nuevo
que archivar y crece sin que sea un fallo. (La alerta en sí —Prometheus/Grafana— no se ha creado aquí: ver §7.)

## 6. Qué NO cubre el PITR

* **Los exports WORM de auditoría** (KC-DB-10-2) y **los adjuntos/blobs** fuera de la base: son otro plano de respaldo.
* **Datos derivados** (`cm.*`, índices HNSW): se pueden reconstruir en lugar de restaurarse (KC-DB-11-4).
* **Cambios de esquema posteriores a la copia base** se recuperan con el WAL, pero la **versión del servidor** (16.x) debe
  coincidir con la de la copia base.

## 7. Límites y cosas que NO están hechas

* **El destino del archivo de WAL y de las copias base en producción no está decidido.** El ensayo y el override de
  compose usan un volumen Docker del mismo anfitrión: **no sobrevive a la pérdida del servidor**. Cambiar a otro destino es
  sustituir `archivar_wal.sh`; qué destino (otro volumen, bucket, pgBackRest/WAL-G) debe decidirlo el Architecture Board.
* **Los tiempos medidos son de una base mínima** en Docker local (decenas de MB). El RTO real de producción crece con el
  tamaño de la copia base y el WAL a reproducir: hay que repetir el ensayo con un volumen representativo antes de aprobar el
  RTO. La medición cuenta desde que empieza el procedimiento; no incluye detectar el desastre ni decidir restaurar.
* **El RPO medido es una muestra**: depende de cuándo cae el corte dentro del intervalo de archivado. La cota es
  `archive_timeout` + tiempo de archivado, y solo vale mientras el archivado funcione (§5). Con el disco del origen
  accesible se puede recuperar más WAL que el archivado (RPO menor).
* **La retención de las copias base y del WAL archivado no está definida** (cuántas copias, cuánto WAL conservar): sin ella
  el archivo crece sin límite o se pierde la capacidad de ir a un T antiguo.
* **No hay alerta creada**; solo la función que la alimenta. Y el ensayo programado (`pitr_semanal.yml`) no se ha ejecutado aún en GitHub.
* **Cifrado y control de acceso del archivo de WAL**: el WAL contiene todos los datos en claro; el destino de producción debe
  cifrarse y restringirse.
