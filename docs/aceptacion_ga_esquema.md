# Aceptación de GA del esquema: ensayo de migración en staging y smoke de release (KC-DB-12-5)

Procedimiento para desplegar el esquema de la release GA sobre **staging con base persistente**, comprobarlo y poder volver atrás,
con el acta que recoge la evidencia para la aceptación. Es **el mismo procedimiento** que reproduce `db/tools/ensayo_despliegue.py`
sobre un staging simulado; el acta del último ensayo está en [`aceptacion_ga_acta.md`](aceptacion_ga_acta.md) y el índice de evidencias
de la release en [`release/expediente.md`](release/expediente.md).

> **Esto no es el staging real.** El repositorio no tiene un staging: el ensayo documentado corre en contenedores Docker locales con
> volúmenes persistentes y datos mínimos. La confirmación definitiva es ejecutar estos pasos en el staging real y anotar el resultado en el
> acta. Y **este procedimiento no firma nada**: la aceptación la firman personas (§7).

## 0. Antes de empezar

| Dato | De dónde sale |
|---|---|
| Artefacto desplegado | el SHA de la release |
| `version_esperada` | `db/esquema_v2.snapshot.json`, clave `etiqueta` |
| `ultima_migracion` | `ls db/migrations/*.sql \| sort \| tail -n1` en ese SHA |
| `migraciones` | `ls db/migrations/*.sql \| wc -l` en ese SHA |
| Objetivo de RTO | `SELECT rto_minutos, estado FROM core.objetivos_recuperacion ORDER BY (estado = 'aprobado') DESC, id DESC LIMIT 1;` |

Las migraciones de la release que toquen RLS, privilegios o auditoría son de **clase B**: doble aprobación (Tech Lead y DBA), ventana
anunciada y punto de restauración previo (`docs/politica-cambios-esquema.md` §3). Este procedimiento es ese punto de restauración y esa
ventana.

## 1. Backup previo

1. Comprobar que el archivado de WAL está al día: `SELECT archivado_activado, archivado_al_dia FROM core.estado_archivado_wal();` (ambos `true`).
2. Tomar la copia base con `pg_basebackup -Fp -X stream -c fast` (o el snapshot del volumen que use el staging) y **anotar el instante T0** en UTC:
   `SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00';`
3. Forzar el cierre del segmento actual (`SELECT pg_switch_wal();`) y esperar a que se archive. Sin esto, T0 podría no estar cubierto.

Detalle y límites del archivado: [`recuperacion_pitr.md`](recuperacion_pitr.md).

## 2. Migraciones como paso separado

Se aplican **solas**, antes de arrancar la nueva versión de la aplicación, con el mismo runner que el CI:

```bash
KC_DATABASE_URL_ADMIN=postgresql+asyncpg://kallicode_admin:***@staging:5432/kallicode uv run python packages/core/migrate.py
```

* Las aplica **en orden**, una transacción por archivo, y registra cada una en `core.schema_migrations` con su `duracion_ms`.
* **Presupuesto** (propuesta, ver §6): ninguna migración por encima de 30 s y la suma por debajo de 120 s. Se lee del ledger:
  `SELECT archivo, duracion_ms FROM core.schema_migrations WHERE aplicado_en > '<T0>' ORDER BY duracion_ms DESC;`
* Si una migración falla, el runner se detiene: lo aplicado antes queda, lo fallido se revierte entero. **No se sigue**: ir a §4.

## 3. Smoke de release

Con el rol administrador (verificar la cadena de todos los tenants exige ver todos los tenants):

```bash
psql "$KC_DATABASE_URL_MIGRATIONS" -v ON_ERROR_STOP=1 \
     -v version_esperada=v2.0.0 -v ultima_migracion=<ultima_migracion> -v migraciones=<migraciones> \
     -f db/tools/smoke_release.sql
```

Comprueba, en una sola llamada a `core.smoke_release()`: versión del esquema y última migración, número de migraciones y que se aplicaron en
orden, **los 26 chequeos de `core.validar_integridad()` en cero**, **la cadena de auditoría íntegra para todos los tenants**, **cero tablas sin
política** (retención, recuperación y aislamiento) y particiones `DEFAULT` vacías. Sale con código distinto de 0 si algo falla.

Y con el rol de la **aplicación**, que además demuestra que puede leer su versión:

```bash
psql "$KC_DATABASE_URL_APP" -v ON_ERROR_STOP=1 -v version_esperada=v2.0.0 -v ultima_migracion=<ultima_migracion> \
     -f db/tools/smoke_post_deploy.sql
```

**Control negativo recomendable:** repetir el smoke de release con `-v version_esperada=v9.9.9`; debe fallar. Si pasara, el smoke no sirve.

## 4. Vuelta atrás (si el smoke falla o la ventana se agota)

Restaurar en una instancia **nueva** al instante T0 (`recovery_target_time = '<T0>'`) siguiendo [`recuperacion_pitr.md`](recuperacion_pitr.md) §3, con
`pg_cron` y `pg_partman_bgw` desactivados hasta verificar. La instancia restaurada debe tener como última migración la anterior al despliegue.
La aplicación se apunta a ella y se investiga la causa con calma.

## 5. Segundo ensayo de restauración PITR

Con el esquema ya desplegado, repetir la restauración del Sprint 11 y **medir el RTO contra el objetivo** de §0:

```bash
python db/tools/ensayo_despliegue.py --salida ./acta_ga        # todo el procedimiento, sobre un staging simulado
python db/tools/ensayo_pitr.py --salida ./acta_pitr            # solo la restauración, con carga continua y RPO
```

En cada restauración se verifica `audit.verify_chain()` de todos los tenants y `core.validar_integridad()` (26 chequeos, 0 hallazgos). El RTO
cuenta desde que empieza el procedimiento hasta que la instancia acepta conexiones fuera de recuperación; no incluye detectar el desastre, decidir
restaurar ni la verificación posterior.

## 6. Presupuesto de migraciones

Nadie había definido el presupuesto que pide el ticket. **Propuesta**, derivada de la medición en Docker local con las 67 migraciones (suma 6,7 s,
la más lenta 2,3 s) con un margen de unas veinte veces:

| Límite | Valor propuesto |
|---|---|
| Cada migración | 30 s |
| Suma de las migraciones del despliegue | 120 s |

Es una **propuesta que hay que ratificar** con el volumen del staging real: algunas migraciones (índices, particionado) crecen con el tamaño de las
tablas. `evaluar_presupuesto()` de `db/tools/ensayo_despliegue.py` aplica esos valores y una prueba comprueba que cubren con holgura lo medido hoy.

## 7. Firmas

El acta ([`aceptacion_ga_acta.md`](aceptacion_ga_acta.md)) recoge la evidencia y deja un bloque de firmas. **El ensayo no firma ni aprueba nada**: lo
firman el **Product Owner**, el **Architecture Board** y el **Tech Lead de Testing** (aceptación) y el **DBA** (firma del despliegue del esquema). Hasta
entonces el estado es *pendiente de firmas*, y así consta en [`release/expediente.md`](release/expediente.md).

## Automatización y límites

* `.github/workflows/aceptacion_ga_manual.yml` ejecuta `ensayo_despliegue.py` a petición y publica el acta como artefacto.
* El CI ejecuta el smoke de release en cada PR (paso «Smoke de release del esquema (GA)»).
* El objetivo de RTO sigue **propuesto**, no aprobado por el Architecture Board; el RTO se mide contra él, pero no equivale a una aprobación.
* Los tiempos del ensayo miden el procedimiento con un volumen mínimo; el staging real puede tardar más en las migraciones y en la restauración.
* «Release anterior» en el ensayo es un punto de partida arbitrario (por defecto, el esquema hasta `060_vigencia_embeddings_hnsw.sql`).
