> **Acta generada por `python packages/codemapping/tests/ensayo_reconstruccion_grafo.py --acta <archivo>`** en un sandbox de desarrollo (Postgres 16 local, 2 CPU, base del cliente y LLM/TEI simulados). Es el resultado de UNA ejecución a escala completa (200.000 nodos); **no es un ensayo en staging** y los objetivos siguen propuestos hasta que Database los apruebe. El ensayo mensual (`.github/workflows/ensayo_reconstruccion_mensual.yml`) publica una nueva en cada corrida. Procedimiento: [runbook_reconstruccion_grafo.md](runbook_reconstruccion_grafo.md).

# Acta del ensayo de reconstrucción del grafo (KC-CM-12-1)

Ejecutado: 2026-11-16T19:15:38+00:00 · PostgreSQL 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1) · entorno: sandbox de desarrollo (Postgres 16 local, 2 CPU, base del cliente simulada); no es staging
Grafo de referencia: 200,000 nodos y 260,000 aristas (repositorio sintético @ bf8bc04eca). Reconstrucción por ingesta del mismo commit.
Resultado global: **CUMPLE** (recuentos dentro del 0,5 % y tiempo de recuperación dentro del RTO vigente).

## Tiempo de recuperación frente al objetivo

RTO objetivo: 60.0 min (fuente: core.objetivos_recuperacion (propuesto)). El tiempo cuenta desde que empieza el borrado del grafo del tenant hasta que el grafo vuelve a estar completo, con la alcanzabilidad recalculada; **no** incluye detectar el desastre, decidir reconstruir ni restaurar la capa semántica desde la copia.

| alcance | borrado | reconstrucción | recuperación | RTO objetivo | ¿cumple? |
|---|---:|---:|---:|---:|---|
| determinista | 5.9 s | 43.3 s | 49.2 s | 60.0 min | SÍ |
| completo | 6.0 s | 50.5 s | 56.5 s | 60.0 min | SÍ |

Ingesta completa de referencia (con llamadas al LLM y a TEI simuladas): 49.2 s. Con el LLM y TEI reales, el alcance `completo` suma su latencia y su costo en tokens; el `determinista` no los usa.

## Alcance `determinista`

Desastre simulado (cm.vaciar_grafo_tenant): cm.reachability 743,403, cm.edges 239,200, cm.db_tables 600, cm.files 8,000.

Capa determinista (archivos, símbolos, tablas, columnas, sus aristas y la alcanzabilidad):

| objeto | referencia | tras reconstruir | diferencia | ¿dentro del 0,5 %? |
|---|---:|---:|---:|---|
| arista:CALLS | 214,000 | 214,000 | 0.00 % | SÍ |
| arista:REFERENCES | 1,200 | 1,200 | 0.00 % | SÍ |
| arista:USES_TABLE | 24,000 | 24,000 | 0.00 % | SÍ |
| nodo:db_column | 9,000 | 9,000 | 0.00 % | SÍ |
| nodo:db_table | 600 | 600 | 0.00 % | SÍ |
| nodo:file | 8,000 | 8,000 | 0.00 % | SÍ |
| nodo:symbol | 176,000 | 176,000 | 0.00 % | SÍ |
| reachability | 743,403 | 743,403 | 0.00 % | SÍ |

Capa semántica y humana (no debe cambiar con un desastre determinista, y se repite igual en el completo):

| objeto | referencia | tras reconstruir | diferencia | ¿dentro del 0,5 %? |
|---|---:|---:|---:|---|
| arista:EXPRESSES | 6,000 | 6,000 | 0.00 % | SÍ |
| arista:REALIZED_BY | 12,000 | 12,000 | 0.00 % | SÍ |
| arista:RELATES | 2,800 | 2,800 | 0.00 % | SÍ |
| nodo:concept | 400 | 400 | 0.00 % | SÍ |
| nodo:definition | 6,000 | 6,000 | 0.00 % | SÍ |

Frente al generador del repositorio sintético: todos los objetos coinciden. Aristas semánticas sin nodo: 0. Símbolos con descripción LLM: 12,000 antes, 0 después.

## Alcance `completo`

Desastre simulado (cm.vaciar_grafo_tenant): cm.reachability 743,403, cm.edges 260,000, cm.definitions 6,000, cm.concepts 400, cm.db_tables 600, cm.files 8,000.

Capa determinista (archivos, símbolos, tablas, columnas, sus aristas y la alcanzabilidad):

| objeto | referencia | tras reconstruir | diferencia | ¿dentro del 0,5 %? |
|---|---:|---:|---:|---|
| arista:CALLS | 214,000 | 214,000 | 0.00 % | SÍ |
| arista:REFERENCES | 1,200 | 1,200 | 0.00 % | SÍ |
| arista:USES_TABLE | 24,000 | 24,000 | 0.00 % | SÍ |
| nodo:db_column | 9,000 | 9,000 | 0.00 % | SÍ |
| nodo:db_table | 600 | 600 | 0.00 % | SÍ |
| nodo:file | 8,000 | 8,000 | 0.00 % | SÍ |
| nodo:symbol | 176,000 | 176,000 | 0.00 % | SÍ |
| reachability | 743,403 | 743,403 | 0.00 % | SÍ |

Capa semántica y humana (no debe cambiar con un desastre determinista, y se repite igual en el completo):

| objeto | referencia | tras reconstruir | diferencia | ¿dentro del 0,5 %? |
|---|---:|---:|---:|---|
| arista:EXPRESSES | 6,000 | 6,000 | 0.00 % | SÍ |
| arista:REALIZED_BY | 12,000 | 12,000 | 0.00 % | SÍ |
| arista:RELATES | 2,800 | 2,800 | 0.00 % | SÍ |
| nodo:concept | 400 | 400 | 0.00 % | SÍ |
| nodo:definition | 6,000 | 6,000 | 0.00 % | SÍ |

Frente al generador del repositorio sintético: todos los objetos coinciden. Aristas semánticas sin nodo: 0. Símbolos con descripción LLM: 12,000 antes, 12,000 después.

## Objetivos de recuperación del esquema cm (propuestos a Database)

| capa | RPO | RTO | cómo se recupera |
|---|---|---|---|
| Determinista (archivos, símbolos, tablas, columnas y sus aristas, alcanzabilidad) | 0 commits: se reconstruye del HEAD vigente del repositorio | tiempo del alcance `determinista` de esta acta (≤ RTO de la base) | `full_build(alcance="determinista")` |
| Semántica y humana (definiciones, conceptos, aristas semánticas, curación, vectores) | el de la base (PITR) | el de la restauración PITR | copia de seguridad (core.politica_recuperacion) |

Pendiente: aprobación de estos objetivos por Database (los de la base, RPO 5 min y RTO 60 min, siguen en estado propuesto).

## Hallazgos

- Reconstruir el alcance `determinista` borra 12,000 descripciones LLM de símbolos (`cm.symbols.descripcion`) y los vectores de código que dependen de ellas: viven en la misma fila y core.politica_recuperacion marca `cm.symbols` como «sin LLM». Si hay que conservarlas, restaurarlas del backup o ingerir con alcance `completo` (cuesta tokens).

## Firmas

| Rol | Nombre | Fecha |
|---|---|---|
| Ejecutó | (completar) | |
| Revisó (Database) | (completar) | |
| Aprobó | (completar) | |
