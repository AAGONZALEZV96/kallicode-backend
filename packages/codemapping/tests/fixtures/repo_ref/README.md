# repo_ref — repositorio sintético de referencia (KC-CM-01-2)

Fixture versionado para los golden tests del analizador AST (`tests/test_golden_ast.py`).
**No editar sin regenerar los golden** (`KC_UPDATE_GOLDEN=1`): un byte distinto cambia
sha256, líneas e IDs esperados. Este README no es código: `walk_sources` lo ignora (.md).

| Archivo | Casos |
|---|---|
| reports.sql, src/*.{ts,tsx,js,py,java} | Casos base: uno por lenguaje, JS minificado, funciones anónimas en objeto |
| src/services/user_service.ts | namespace con clase, clase dentro de función, interface, enum, type alias, function expression, `export default` anónimo |
| src/ui/Widget.tsx | componente arrow genérico `<T,>`, componente de clase, handlers inline en JSX |
| src/generated/sdk.min.ts | minificado en 1 línea: varias anónimas en la misma línea (unicidad de IDs) |
| src/facturacion/facturacion.py | clases anidadas en 3 niveles, closures, decoradores, async, lambda, no ASCII (ñ, á) |
| src/main/java/com/acme/billing/InvoiceService.java | clase interna, static nested, clase anónima, lambda, enum con métodos |
| db/cte_avanzadas.sql | CTE simple, múltiples, recursiva, anidada en subconsulta, plpgsql con `$$` |
