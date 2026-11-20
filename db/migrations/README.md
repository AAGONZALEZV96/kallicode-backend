# Migraciones versionadas (D06 · KC-DB-01-1)

Todo cambio de esquema posterior a `db/01..05.sql` llega como un archivo
**inmutable** en esta carpeta y lo aplica **solo** el runner:

```bash
uv run python packages/core/migrate.py                # usa KC_DATABASE_URL_ADMIN
uv run python packages/core/migrate.py --dsn postgresql://...
```

El runner registra cada archivo en `core.schema_migrations` (archivo, sha256,
aplicado_en, duracion_ms) y sale con código 1 ante cualquier error.

## Reglas

1. **Nombre:** `NNN_descripcion.sql` en minúsculas (`010_cm_indices.sql`).
   Se admite un sufijo de letra para intercalar (`004b_...`). Se aplican en
   orden alfabético.
2. **Inmutables:** una migración ya aplicada no se edita ni se borra. Si el
   sha256 cambia o el archivo desaparece, el runner aborta **sin aplicar
   nada**. Para corregir, crea un archivo nuevo.
3. **Sin `BEGIN;` / `COMMIT;`:** el runner envuelve cada archivo en su propia
   transacción junto con su fila del ledger. Si un archivo falla, se revierte
   entero y los anteriores quedan aplicados.
4. **Sentencias que no admiten transacción** (`CREATE INDEX CONCURRENTLY`):
   añade la línea `-- kc:sin-transaccion`. Cada sentencia se ejecuta por
   separado, así que deben ser reejecutables (`IF NOT EXISTS`).
5. **Fin de línea:** el hash se calcula con LF, de modo que un checkout con
   CRLF (Windows) da el mismo sha256 que el CI.
6. **Concurrencia:** el runner toma `pg_advisory_lock`; dos despliegues a la
   vez se serializan y nadie aplica un archivo dos veces.

## Entornos preexistentes: `--adoptar`

Las bases donde 001, 002 y 009 se aplicaron a mano con `psql` (antes de que
existiera el runner) no tienen esas filas en el ledger. Ejecuta **una sola
vez** por entorno:

```bash
uv run python packages/core/migrate.py --adoptar
```

Registra las pendientes sin ejecutarlas. Una base nueva nunca lo necesita.

> **Cuidado:** `--adoptar` marca como aplicadas **todas** las pendientes.
> Ejecútalo con el repo en el commit que corresponde a lo que la base ya
> tiene, nunca con migraciones nuevas presentes, o quedarán registradas sin
> haberse ejecutado.

## Versión del esquema e instantánea congelada (KC-DB-12-1)

El esquema está congelado como **modelo v2** (`v2.0.0`, fijada por `067_version_esquema.sql`). La
política de cambios después de GA (clases A, B y C) está en
[`docs/politica-cambios-esquema.md`](../../docs/politica-cambios-esquema.md).

- **Cualquier migración nueva** obliga a regenerar la instantánea en el mismo PR. Si no se regenera,
  `test_esquema_congelado.py` falla. Se regenera con:
  ```bash
  uv run python db/tools/congelar_esquema.py              # contra KC_DATABASE_URL_ADMIN
  uv run python db/tools/congelar_esquema.py --comprobar  # solo compara; sale con 1 si diverge
  ```
- **Para fijar una versión nueva**, la migración termina con
  `SELECT set_config('kc.etiqueta_version', 'v2.1.0', true);`. Esa migración debe ser transaccional, y
  la etiqueta no se puede repetir.
- **Para consultar la versión** desde cualquier rol de la aplicación: `SELECT * FROM core.version_esquema();`.
- **Después de cada despliegue** se ejecuta el smoke: `db/tools/smoke_post_deploy.sql`.
- **Todo objeto nuevo lleva `COMMENT`** (tabla, columna y constraint), en la misma migración que lo crea. La
  documentación del modelo (`docs/modelo_datos_v2.md`) se genera desde el catálogo y esos `COMMENT`:
  ```bash
  uv run python db/tools/documentar_esquema.py              # regenera el documento (súbelo en el mismo PR)
  uv run python db/tools/documentar_esquema.py --cobertura  # falla si falta algún COMMENT
  uv run python db/tools/documentar_esquema.py --comprobar  # falla si el documento difiere del catálogo
  ```
