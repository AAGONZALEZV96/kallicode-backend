# orm_ref — repositorio de referencia de acceso a datos (KC-CM-09-3, CM-F17)

Fixture multilingüe de `tests/test_uses_table_cobertura.py`. Mide la cobertura de las aristas
USES_TABLE (archivo -> tabla) frente a una verdad de referencia escrita a mano en
`esperado.json`:

- `tablas`: el esquema de la base del cliente (lo que devolvería `extract_schema`).
- `accesos`: 60 accesos a datos, 30 en SQL crudo y 30 por ORM (10 Prisma, 10 TypeORM,
  10 SQLAlchemy). Incluye uno no detectable a propósito (nombre de tabla en un f-string).
- `trampas`: coincidencias que no deben entrar al grafo (tablas inexistentes, CTE que
  sombrea a una tabla real, función en FROM, nombre ambiguo sin esquema).

| Carpeta | Casos |
|---|---|
| `sql/` | CTE encadenadas, `esquema.tabla`, vistas, sentencias trampa |
| `python/` | psycopg con concatenación y comillas, `sqlalchemy.text` con CTE y esquemas, modelos declarativos (`__tablename__`, `__table_args__` dict y tupla), `Table(...)` Core, usos `select/update/query/get` directos y vía módulo |
| `ts/`, `js/` | `pg` con template strings, Prisma (`prisma.x.op`, `tx.x.op`, `this.prisma.x.op`), TypeORM (`@Entity` con nombre/objeto/por defecto, `@InjectRepository`, `Repository<T>`, `getRepository`, `createQueryBuilder`, `manager.find*`), relación `() => T` que no es acceso |
| `java/` | JDBC con text block, `USING`, concatenación |
| `prisma/` | `model` con `@@map` (posicional y `name:`), `@@schema` |

Si se añade un caso, se actualiza `esperado.json` a mano: no se regenera desde el detector.
Este fixture no forma parte de `repo_ref` (los golden del AST no cambian).
