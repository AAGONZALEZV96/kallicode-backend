# Conversión a halfvec (preparada, no aplicada)

KC-DB-08-3 / ADR-022. Las colecciones de `vec.*` siguen siendo `vector(1024)`: con el HNSW, halfvec pierde 4-5 puntos de
recall@10 (criterio de adopción: < 1). Estos scripts dejan la conversión lista y ensayada para cuando el benchmark
nocturno avise «REABRIR ADR-022» o el ADR lo decida por presión de memoria.

| Script | Qué hace |
|---|---|
| `aplicar_halfvec.sql` | Convierte cada colección del catálogo a `halfvec(1024)`, reconstruye su HNSW con `halfvec_cosine_ops` y adapta la consulta híbrida. Idempotente. |
| `revertir_halfvec.sql` | Vuelve a `vector(1024)` y `vector_cosine_ops`. |

Requieren la migración `046_hnsw_opclase.sql`. Se ensayan en CI (`packages/core/tests/integracion/test_halfvec.py`) con
filas y dentro de una transacción que se deshace. Procedimiento, criterio de reversión y efecto en almacenamiento:
`docs/adr/ADR-022-representacion-vectorial.md`.
