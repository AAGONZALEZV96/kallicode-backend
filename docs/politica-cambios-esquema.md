# Política de cambios del esquema post-GA (modelo v2)

**Ticket:** KC-DB-12-1 · **Épica:** EDB1 · **Estado:** propuesta para aprobación del Architecture Board
**Versión del esquema congelada:** `v2.0.0` (fijada por `db/migrations/067_version_esquema.sql`)

Este documento fija qué cambios del esquema de PostgreSQL se pueden hacer después de GA, cómo se clasifican
y qué procedimiento sigue cada clase. Se aplica a todo lo que vive en `db/`: tablas, columnas, constraints,
índices, vistas, funciones, triggers, políticas RLS, privilegios y tipos de los esquemas `core`, `vec`,
`audit` y `cm`.

## 1. El modelo v2 congelado

Al cerrar el plan, el esquema se congela como **modelo v2**. Las cifras salen de la instantánea committeada
`db/esquema_v2.snapshot.json`, no de una cuenta a mano:

| Objeto | Cantidad |
|---|---|
| Tablas (sin contar particiones hijas) | 84: `core` 49 · `cm` 22 · `vec` 8 · `audit` 5 |
| Tablas particionadas | 7 (5 por RANGE de fecha y 2 por LIST de tenant) |
| Tablas con RLS y FORCE | 66 (las 18 restantes son catálogos globales, ledgers o tablas de operación sin `tenant_id`) |
| Columnas / constraints / índices | 764 / 445 / 230 |
| Políticas RLS / triggers / funciones / vistas | 71 / 57 / 100 / 4 |
| Chequeos de `core.validar_integridad()` | 26 |
| Migraciones aplicadas | `001` … `068` (66 archivos) |

La versión se lee en cualquier entorno con `SELECT * FROM core.version_esquema();`. La función devuelve:

- la etiqueta vigente;
- la migración que la fijó;
- la última migración aplicada;
- cuántas migraciones se aplicaron después de la etiqueta.

## 2. Versionado del esquema

La etiqueta sigue `vMAYOR.MENOR.PARCHE`:

- **MAYOR:** se sube con un cambio incompatible (clase C), una vez completado el *contract*.
- **MENOR:** se sube en una release que agrupa cambios de clase A o B que un consumidor puede notar (tablas,
  columnas o funciones nuevas que usa la API).
- **PARCHE:** se sube con correcciones sin efecto visible (índices, estadísticas, cuerpos de función con la
  misma firma y la misma semántica).

No todas las migraciones fijan una versión. Las que no la fijan aparecen en `migraciones_posteriores`, y eso
es normal entre dos releases.

Para fijar una versión, la migración que la cierra termina con esta línea:

```sql
SELECT set_config('kc.etiqueta_version', 'v2.1.0', true);
```

El trigger `schema_migrations_etiqueta` copia ese valor a la fila del ledger que inserta el runner. Hay tres
condiciones:

- La migración debe ser transaccional, es decir, no puede llevar `-- kc:sin-transaccion`.
- No sirve con `--adoptar`.
- Una etiqueta no se puede repetir (hay un índice único) y debe tener el formato válido (hay un CHECK).

## 3. Clasificación de cambios

| | **Clase A · Aditivo** | **Clase B · Con ventana** | **Clase C · Incompatible** |
|---|---|---|---|
| Cuándo se aplica | En cualquier despliegue | En una ventana programada | En varias releases, por fases (expand/contract) |
| Aprobación | Revisión del PR y Tech Lead (todo `db/` la necesita) | **Doble:** Tech Lead y DBA de guardia (o dueño del dominio) | Architecture Board, Tech Lead y DBA, más un ADR |
| Versión | Ninguna o MENOR/PARCHE | MENOR o PARCHE | MAYOR |
| Rollback | No hace falta: el código anterior sigue funcionando | Plan escrito en el PR y punto de restauración PITR | Cada fase tiene vuelta atrás hasta el *contract* |

### Clase A — Aditivo (se puede aplicar en cualquier despliegue)

Un cambio es aditivo si el código desplegado **antes** del cambio sigue funcionando **después** y la
migración no toma bloqueos largos. Ejemplos:

- Tabla, vista o función nueva. Una tabla nueva con `tenant_id` lleva RLS, FORCE y una política con cláusula
  TO desde la primera migración.
- Columna nueva nullable, o con un `DEFAULT` constante. En PG ≥ 11 no reescribe la tabla.
- Índice nuevo con `CREATE INDEX CONCURRENTLY IF NOT EXISTS` en una migración `-- kc:sin-transaccion`.
- CHECK o FK nuevas como `NOT VALID` seguidas de `VALIDATE CONSTRAINT` (que toma `SHARE UPDATE EXCLUSIVE`).
- Ampliar un CHECK a un superconjunto, como hace `066_cm_symbols_kind_sql.sql`.
- Valor nuevo en una tabla de dominio, o etiqueta nueva añadida al final de un enum.
- Cambiar el cuerpo de una función sin cambiar su firma ni su semántica observable.

Todas las migraciones de clase A siguen estas reglas:

- Empiezan con `SET LOCAL lock_timeout = '5s';`.
- Son reejecutables (`IF NOT EXISTS` / `DROP … IF EXISTS`).
- Añaden COMMENT a cada objeto nuevo.

### Clase B — Necesita ventana y doble aprobación

Un cambio es de clase B si no rompe el contrato con el código, pero hay que planificarlo por **riesgo
operativo** o porque toca el **aislamiento o las pruebas** del sistema. Ejemplos:

- Operaciones que reescriben la tabla o toman `ACCESS EXCLUSIVE` durante un tiempo que depende de su
  tamaño: cambiar el tipo de una columna compatible, `SET NOT NULL` sin un CHECK validado antes, o crear
  un índice sin `CONCURRENTLY` en una tabla grande.
- Backfills de datos en tablas de alta rotación. Se hacen por lotes, con una función versionada.
- Cualquier cambio de RLS, políticas, privilegios (`GRANT`/`REVOKE`), roles o funciones
  `SECURITY DEFINER`.
- Cualquier cambio en tablas probatorias o de auditoría (`audit.*`, sellado y cadena), en la retención o
  en las purgas.
- Cambios de particionado, de parámetros HNSW o de autovacuum por tabla.

El procedimiento tiene seis pasos:

1. **PR.** Lo marca como «Clase B». Incluye la duración medida en un ensayo con un volumen representativo,
   los bloqueos que toma y el plan de rollback.
2. **Doble aprobación.** Tech Lead y DBA. Si toca seguridad o auditoría, lo indica de forma explícita
   (CLAUDE.md).
3. **Ensayo en staging.** Se ensaya el despliegue completo: migración, smoke y `make check-db` (KC-DB-12-5).
4. **Ventana.** Se anuncia la ventana. Antes de empezar se crea un punto de restauración
   (`docs/recuperacion_pitr.md`).
5. **Aplicación.** El runner aplica la migración. Después se ejecutan
   `db/tools/smoke_post_deploy.sql` y `core.validar_integridad()`, que debe dar 0 problemas.
6. **Cierre.** Si algo falla, se hace rollback según el plan. El cierre se registra en el PR.

### Clase C — Incompatible

Un cambio es de clase C si el código desplegado antes del cambio **deja de funcionar** después, o si
se pierde información. Ejemplos:

- `DROP` de una tabla, columna, función o vista que se usa.
- `RENAME` de cualquier objeto que se usa.
- Cambiar el tipo de una columna a uno incompatible.
- Estrechar un CHECK o hacer NOT NULL una columna con nulos.
- Cambiar la semántica de una columna o de una función.
- Quitar un valor de una enumeración.

Estos cambios **nunca** se hacen en una sola migración. Siguen el patrón *expand/contract*:

1. **ADR.** Se escribe en `docs/adr/` con el motivo, las alternativas, los consumidores afectados (API,
   workers, CodeMapping, Testing y contratos) y el calendario. Lo aprueba el Architecture Board.
2. **Expand (release N).** Una migración de clase A o B añade lo nuevo junto a lo viejo: una columna o
   tabla nueva, o un trigger o vista de compatibilidad. El código pasa a escribir en las dos versiones y a
   leer la nueva.
3. **Migración de datos.** El backfill se hace por lotes, con el procedimiento de clase B, y se verifica
   con un chequeo de `validar_integridad()` si la invariante lo merece.
4. **Corte.** Se despliega un código que ya no usa lo viejo. Hay que esperar al menos **una release
   completa** en producción sin errores.
5. **Contract (release N+1 o posterior).** En una migración de clase B se retira lo viejo. El PR se marca
   como **migración destructiva** (CLAUDE.md) y sube la versión MAYOR.
6. **Anuncio.** Si el cambio afecta a un contrato publicado (`contratos/`), se anuncia en
   `contratos/cambios-anunciados.yaml` con `breaking: true`.

### Árbol de decisión

1. ¿El código desplegado antes del cambio sigue funcionando después, y no se pierde ningún dato?
   Si la respuesta es **no**, es **Clase C**.
2. ¿Toca RLS, privilegios, roles, `SECURITY DEFINER`, `audit.*` o la retención? ¿Reescribe una tabla o
   bloquea durante un tiempo que crece con su tamaño? Si la respuesta es **sí**, es **Clase B**.
3. En cualquier otro caso, es **Clase A**.

Si hay duda, se elige la clase más alta.

## 4. Reglas comunes a todas las clases

1. **Todo cambio es una migración nueva** `db/migrations/NNN_descripcion.sql`. Las migraciones ya
   aplicadas no se editan (el runner verifica el sha256 y aborta).
2. **No se hace DDL a mano** en ningún entorno compartido. Una corrección urgente también es una migración,
   y pasa por un PR acelerado con la misma doble aprobación de la clase B.
3. **La instantánea se regenera en el mismo PR:**
   `python db/tools/congelar_esquema.py` (contra una base con todas las migraciones aplicadas). El diff de
   `db/esquema_v2.snapshot.json` muestra a los revisores, objeto por objeto, lo que cambia de verdad.
4. **El smoke post-deploy** se ejecuta tras cada despliegue con la etiqueta y la última migración del
   artefacto desplegado:
   ```bash
   psql "$KC_DATABASE_URL_APP" -v ON_ERROR_STOP=1 \
        -v version_esperada=v2.0.0 -v ultima_migracion=067_version_esquema.sql \
        -f db/tools/smoke_post_deploy.sql
   ```
5. **Los modelos de SQLAlchemy** se actualizan en el mismo PR (manda el SQL).

## 5. Verificación automatizada

| Qué se verifica | Dónde |
|---|---|
| El esquema aplicado coincide con `db/esquema_v2.snapshot.json`: tablas, columnas, constraints, índices, vistas, secuencias, funciones (hash del cuerpo), triggers, políticas, ACL, privilegios por defecto y event triggers | `packages/core/tests/integracion/test_esquema_congelado.py` (CI, job `database`) |
| Toda migración del repo está en la instantánea, con el mismo sha256 | El mismo test |
| Un DDL sin migración (columna, nulabilidad, índice, FORCE RLS, política, privilegio, cuerpo de función o tabla) se detecta | El mismo test (prueba de deriva parametrizada) |
| La versión y la última migración del entorno son las esperadas | `db/tools/smoke_post_deploy.sql` (CI y post-deploy) |
| Comprobación manual contra cualquier entorno | `python db/tools/congelar_esquema.py --dsn … --comprobar` |

La instantánea **no** incluye, a propósito:

- Las particiones hijas, porque pg_partman y la partición por tenant las crean con el tiempo.
- El interior de `retencion_archivo`, que recibe particiones desenganchadas.
- Los objetos de extensiones ni sus versiones, porque `pg_stat_statements` y `pg_cron` son opcionales
  según el servidor.
- Los COMMENT. Su cobertura la vigila KC-DB-12-2.

## 6. Aprobación

| Rol | Nombre | Decisión | Fecha |
|---|---|---|---|
| Tech Lead / DBA (DB1) | | Pendiente | |
| Architecture Board | | Pendiente | |

La aprobación del Architecture Board es el criterio de aceptación 1 de KC-DB-12-1. Se registra en esta
tabla, en el PR que la recoja.

## Documentación generada del modelo (KC-DB-12-2)

El modelo de datos ya no se mantiene a mano: [`docs/modelo_datos_v2.md`](modelo_datos_v2.md) se genera desde `pg_catalog` y
los `COMMENT` con `db/tools/documentar_esquema.py`. Para quien cambia el esquema, esto significa dos reglas más:

- **Todo objeto nuevo lleva `COMMENT`.** Cada tabla, columna y constraint que cree una migración debe describirse en esa
  misma migración (`COMMENT ON TABLE`, `COMMENT ON COLUMN` y `COMMENT ON CONSTRAINT`). El CI ejecuta
  `documentar_esquema.py --cobertura` y falla si falta alguno. Una migración solo de `COMMENT` es de clase A.
- **El documento se regenera en el mismo PR.** Tras añadir una migración, ejecuta `python db/tools/documentar_esquema.py`
  y sube el resultado junto con la instantánea (`congelar_esquema.py`). Si no, el paso «Documentación del modelo v2» del
  CI falla al compararlo con el catálogo.

Las afirmaciones corregidas de la auditoría viven en `db/documentacion/afirmaciones_auditoria.json`, con una verificación
SQL por afirmación que se ejecuta en cada generación.

