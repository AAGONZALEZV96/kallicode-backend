# ADR-020: El grafo de CodeMapping vive en PostgreSQL y se retira Neo4j

- **Estado:** propuesto (reconstruido en KC-DB-11-3). Pendiente de aprobación del Architecture Board.
- **Decisión en una línea:** el grafo de código se guarda en `cm.nodes` y `cm.edges`, y se consulta con
  CTE recursivos en la misma base que el resto de la plataforma, con RLS por tenant. Neo4j se retira.

> **Por qué «reconstruido».** Los tickets de los sprints 9 a 12 citan el ADR-020 («las ocho consultas
> críticas del ADR-020», KC-DB-11-3), pero el documento nunca llegó al repositorio. Este texto lo
> reconstruye a partir de lo que sí está implementado: KC-CM-09-1, KC-CM-09-4, KC-DB-04-2 y KC-DB-11-2.
> Si aparece el original, prevalece. Las diferencias se anotan aquí.

## Contexto

CodeMapping construye un grafo del repositorio del cliente: ficheros, símbolos, tablas y columnas, con
aristas `DEFINES`, `CALLS`, `USES_TABLE`, `HAS_COLUMN` y `REFERENCES`, más las capas semánticas. Todos
los agentes del pipeline lo consultan antes de actuar, para saber qué toca un cambio y qué depende de
ello.

El grafo vivía en Neo4j. Eso obligaba a mantener un segundo almacén con otro modelo de aislamiento por
tenant, otro backup y otra ruta de despliegue. Además, `testing/gen_tests.py` abría Neo4j por su cuenta
(KC-CM-09-1). El resto de los datos del tenant, incluidos los embeddings en `vec.*`, ya está en
PostgreSQL 16 con RLS.

## Decisión

1. **Modelo.** Un nodo por fila en `cm.nodes (tenant_id, id, kind)`, sincronizado por triggers desde
   las tablas de cada capa (migración 022). Una arista por fila en `cm.edges (tenant_id, tipo, src_id,
   dst_id)`. Los dos índices cubrientes, `edges_out_ix` y `edges_in_ix` (migración 020), permiten
   recorrer en los dos sentidos solo con el índice.
2. **Consultas.** Las ocho consultas críticas de la sección siguiente son la única interfaz del grafo.
   Están en `kallicode_core.cm_impacto.CONSULTAS` y su contrato congelado es `cm-grafo-v1`
   (KC-CM-09-4).
3. **Tres requisitos no negociables para todo recorrido recursivo** (KC-DB-11-2):
   - `WITH RECURSIVE ... CYCLE`, para que un ciclo corte la rama en lugar de colgar la consulta;
   - una política RLS trivialmente indexable: exactamente `tenant_id = core.current_tenant()`, más el
     mismo filtro explícito en cada nivel del recorrido;
   - un límite de profundidad (k ≤ 5) y de nodos visitados (5.000), con resultado truncado explícito.
4. **Ruta materializada.** El impacto y las dependencias a ≤ 3 saltos por las aristas de dependencia
   pueden leerse de `cm.reachability`, el cierre transitivo que se refresca al publicar cada snapshot.
   `cm.impacto(p_ruta => 'auto')` la usa cuando está vigente y cae al CTE cuando no.
5. **Neo4j se retira** cuando el benchmark de KC-DB-11-3 demuestre el presupuesto de latencia de la
   sección siguiente a la escala declarada.

## Las ocho consultas críticas

La fuente única es `CONSULTAS` en `packages/core/src/kallicode_core/cm_impacto.py`. El presupuesto p95
de cada una sale del contrato (`kallicode_core.cm_grafo.OPERACIONES`), salvo el recorrido a tres
saltos, que tiene un presupuesto más estricto.

| # | Consulta | Qué responde | Presupuesto p95 |
|---|---|---|---|
| 1 | `resolver_nodos` | Qué ids existen en el tenant, con su kind | 500 ms |
| 2 | `semillas` | Los nodos de partida de un área: los nodos pedidos y cada fichero con sus símbolos | 500 ms |
| 3 | `impacto` | Quién depende del área hasta k saltos (aristas en sentido inverso) | **120 ms a k = 3 sobre 200.000 nodos** |
| 4 | `dependencias` | De qué depende el área hasta k saltos (aristas hacia delante) | **120 ms a k = 3 sobre 200.000 nodos** |
| 5 | `subgrafo_nodos` | Vecindad en los dos sentidos hasta k saltos | 500 ms |
| 6 | `aristas_entre` | Aristas cuyos dos extremos están en un conjunto de nodos | 500 ms |
| 7 | `vecinos` | Aristas a un salto de un nodo, por sentido y tipo | 500 ms |
| 8 | `dependencias_ocultas` | Nodos acoplados por una tabla compartida (`CALLS*` → `USES_TABLE` ←) que no se alcanzan por `CALLS` | 500 ms |

Desde KC-CM-11-2, producción resuelve `impacto` y `dependencias` con `cm.impacto()` (ruta `auto`). Las
otras seis van con la SQL de `CONSULTAS`.

## Evidencia

El benchmark es `packages/core/tests/bench_cm_khop.py` y corre cada noche en
`.github/workflows/grafo_nocturno.yml`. El informe con las cifras, la ruta elegida para cada consulta y
la comparación con Neo4j está en [`docs/benchmark-grafo.md`](../benchmark-grafo.md).

Resultados del 2026-11-15, con 200 semillas por consulta y un 5 % de ellas en nodos centrales:

| Perfil | p95 impacto (k = 3) | p95 dependencias (k = 3) | Peor p95 de las otras seis |
|---|---:|---:|---:|
| Cliente medio: 17.323 nodos, 23.309 aristas (sintético calibrado) | 8,8 ms | 10,7 ms | 12,0 ms |
| Proyección: 200.000 nodos, 300.000 aristas | 16,1 ms | 1,8 ms | 68,1 ms |

- Las dos consultas a tres saltos cumplen por el CTE recursivo; `cm.reachability` es una mejora para
  los nodos centrales, no un requisito.
- El benchmark encontró que `aristas_entre` agotaba el `statement_timeout` con planes genéricos. Se
  corrigió en el mismo ticket.
- **Pendiente para cerrar la decisión:** las cifras equivalentes de Neo4j (CodeMapping) y una medición
  con el dump real del cliente medio en lugar del perfil calibrado.

## Consecuencias

- **Positivas:**
  - Un solo almacén con un solo modelo de aislamiento (RLS), un solo backup y PITR (KC-DB-11-1).
  - Las consultas del grafo pueden cruzarse con `vec.*`, la curación y los incidentes en la misma
    transacción y con el mismo snapshot.
  - Una operación menos que desplegar y vigilar.
- **Negativas / riesgos:**
  - Los recorridos profundos (k > 3) o desde nodos muy centrales cuestan más que en un motor de grafos.
    Se acotan con los límites de visitas y, para el caso caliente, con `cm.reachability`.
  - `cm.reachability` puede no caber en su presupuesto de caminos en grafos grandes y densos; entonces
    queda `excedida` y el impacto vuelve al CTE.
  - Cualquier consulta nueva del grafo debe cumplir los tres requisitos y entrar en el benchmark antes
    de llegar a producción.
