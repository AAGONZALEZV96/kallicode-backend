# Lenguajes del analizador y cómo añadir uno (KC-CM-11-3)

Código: `packages/codemapping/src/kallicode_codemapping/parser_treesitter.py` · extensiones en
`ingest_repo.LANG_BY_EXT` · migración `db/migrations/066_cm_symbols_kind_sql.sql` · pruebas
`tests/test_golden_lenguajes.py` y `test/test_arquitectura_parser.py`.

## Lenguajes admitidos

| Lenguaje | Extensiones | Símbolos (kind) | Llamadas |
|---|---|---|---|
| TypeScript / TSX / JavaScript | `.ts` `.tsx` `.js` `.jsx` | function, method, class, interface, enum, type | `call_expression` |
| Python | `.py` | function, class | `call` |
| Java | `.java` | method, class, interface, enum | `call_expression` |
| **C#** | `.cs` | method (también constructores), function (local), class (class, struct, record), interface, enum | `invocation_expression` |
| **Go** | `.go` | function, method (con receptor y de interfaz), class (struct), interface, type | `call_expression` |
| **PHP** | `.php` | function, method, class (también trait), interface, enum | llamada a función, a método, estática y nullsafe |
| **Kotlin** | `.kt` `.kts` | function, method (dentro de una clase), class (también object), interface, enum | `call_expression` |
| **SQL** | `.sql` | **view** (también materializada), **function**, **procedure** | solo las de las vistas |

Las gramáticas vienen empaquetadas en `tree-sitter-languages` (sin descargas en ejecución). Se evaluó
`tree-sitter-language-pack`, que figuraba en el ticket, y no se adoptó: exige tree-sitter 0.25 o superior (el
paquete fija `<0.22`) y descarga las gramáticas por red al usarlas, algo que este paquete, que accede al
repositorio y a la base del cliente, no debe hacer en producción.

Directorios excluidos por defecto: se añadieron `vendor` (dependencias de Go y PHP) y `obj` (salida de .NET).

## Qué se extrae y qué no

- **Llamadas.** Se guarda el último identificador invocado: `repo.Buscar<T>()`, `Util::calc()`, `$this->x()` y
  `a.b.c()` quedan como `Buscar`, `calc`, `x` y `c`. Las llamadas de una lambda, closure o función local no se
  atribuyen a quien las contiene, y las de un método no se atribuyen a su clase. El grafo une una llamada con su
  destino por nombre y lenguaje, así que una llamada entre lenguajes distintos no genera arista.
- **SQL.** El cuerpo de una función o procedimiento es un literal de cadena (`$$ ... $$`) para la gramática, por
  eso sus llamadas internas no se ven; sí las de una vista (`SELECT upper(x), mi_funcion(y)`). Los `CREATE TABLE` y
  las lecturas de tablas siguen a cargo de `extract_sql`; no producen símbolos.
- **Go.** `type_spec` no incluye la palabra `type`: la firma de `type Servicio struct` es `Servicio struct`.
  Las conversiones y funciones predefinidas (`len`, `float64`) aparecen como llamadas.

## Añadir un lenguaje

Solo hacen falta tres cosas, ninguna en la lógica de `parse_file` (la prueba de arquitectura falla si alguien
añade una rama por lenguaje):

1. Una entrada en `LENGUAJES` con su gramática y su mapa de tipos de nodo:

   ```python
   "ruby": Lenguaje(
       captura={"method": "method", "class": "class", "module": "class"},
       llamadas={"call": "method"},
   ),
   ```

   Los demás campos de `Lenguaje` son opcionales y también son datos (cómo se llama el nombre, dónde empieza el
   cuerpo para cortar la firma, qué nodos son closures, cómo afinar el kind por campo, token o padre).
2. Su extensión en `ingest_repo.LANG_BY_EXT` y, si hace falta, en `Settings.include_ext`.
3. Un fixture en `tests/fixtures/lenguajes/` y su golden (`KC_UPDATE_GOLDEN=1`).

Si el lenguaje produce un kind nuevo, hay que añadirlo al CHECK de `cm.symbols.kind` con una migración
aditiva (como la 066), o la carga de símbolos fallará.
