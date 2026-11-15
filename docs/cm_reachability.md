# Límites de recorrido y cierre transitivo en `cm.reachability` (KC-DB-11-2)

Migración `db/migrations/059_cm_reachability.sql` · diagnóstico `db/tools/cm_impacto.sql` · prueba
`packages/core/tests/integracion/test_cm_reachability.py` (job `database` del CI).

La consulta de impacto («quién depende de este código») la hacen todos los agentes antes de actuar.
Según la estimación del ticket, con 17.323 nodos (el cliente medio) el recorrido a tres saltos tarda
menos de 10 ms; las cifras medidas están en [benchmark-grafo.md](benchmark-grafo.md) (KC-DB-11-3). A
200.000 nodos sigue siendo viable, pero tenía un problema: el `LIMIT` de `SQL_IMPACTO` solo recorta la salida. El `DISTINCT ON` obliga a
evaluar el recorrido entero antes de aplicarlo, así que un nodo central recorre todo el grafo.

Este ticket hace dos cosas:

- **Fija tres requisitos para todo recorrido recursivo del grafo:** cláusula `CYCLE`, una política RLS
  trivialmente indexable y un límite de profundidad y de nodos visitados.
- **Materializa el radio de impacto caliente en `cm.reachability`**, que se refresca dentro del cambio
  de snapshot.

## 1. Objetos

| Objeto | Qué es |
|---|---|
| `cm.reachability` | Cierre transitivo: `src_id` alcanza `dst_id` en `saltos` (el mínimo, entre 1 y 3) por las aristas de dependencia `CALLS`, `HAS_COLUMN`, `REFERENCES` y `USES_TABLE`. PK `(tenant_id, src_id, dst_id)`; índice inverso `reachability_inversa_ix (tenant_id, dst_id, src_id) INCLUDE (saltos, via_src)` |
| `cm.reachability_estado` | Una fila por tenant: `estado`, `snapshot_id` reflejado, filas, caminos recorridos, presupuesto, duración del refresco y fecha de invalidación |
| `cm.refrescar_reachability(p_presupuesto_caminos bigint DEFAULT 5000000)` | Reconstruye la tabla del tenant de la sesión. Devuelve `(estado, filas, caminos)` |
| `cm.impacto(...)` | La consulta de impacto o dependencias, con sus límites (sección 3) |
| Triggers `edges_reachability_{ins,upd,del,trunc}` | Una escritura en `cm.edges` deja la tabla `obsoleta` |
| `cm.reachability_tipos()` | Los cuatro tipos materializados, en orden `"C"` |

`via_src` y `via_dst` guardan el tipo de la primera y de la última arista de un camino mínimo. Si hay
varios caminos mínimos, se queda el tipo menor en orden `"C"`. Con eso la tabla reproduce el `via_tipo`
del recorrido en vivo en los dos sentidos:

- **Impacto:** el `via_tipo` es la arista que sale del nodo alcanzado, es decir, `via_src`.
- **Dependencias:** el `via_tipo` es la arista que entra en él, es decir, `via_dst`.

## 2. Ciclo de vida

| Estado | Significa | `cm.impacto` |
|---|---|---|
| `vigente` | Refleja `cm.edges` tal como estaba al refrescar, para `snapshot_id` | Usa la tabla, si además coincide con `cm.snapshot_vigente` |
| `obsoleta` | `cm.edges` cambió después del refresco: carga de un snapshot, curación o realimentación | Usa el CTE |
| `excedida` | El recorrido superó el presupuesto de caminos. La tabla queda vacía; nunca se deja a medias | Usa el CTE |

Cuándo se refresca:

- **`cm.publicar_snapshot()`** refresca al final, en su misma transacción. Ningún lector ve el
  snapshot nuevo con la tabla del anterior.
- **`cm.retirar_snapshot()`** borra el snapshot retirado, lo que invalida la tabla, y después la
  vuelve a refrescar.
- **A mano:** `SELECT * FROM cm.refrescar_reachability();` con el tenant en la sesión. Dos refrescos
  del mismo tenant se serializan con un advisory lock.

La tabla refleja **todas** las aristas de `cm.edges` del tenant, no solo las del snapshot vigente,
porque eso es lo que ve el recorrido en vivo: las consultas de `cm_impacto` no filtran por snapshot.
Por eso cualquier escritura en `cm.edges` la invalida.

## 3. `cm.impacto()`

```sql
SELECT * FROM cm.impacto(
    p_nodos         => ARRAY['sym_x'],            -- ids de cm.nodes
    p_ficheros      => '{}',                       -- rutas de cm.files (aportan el fichero y sus símbolos)
    p_k             => 3,                          -- 1..5
    p_tipos         => cm.reachability_tipos(),
    p_direccion     => 'entrante',                 -- entrante | saliente | ambas
    p_max_visitados => 5000,                       -- 1..100000
    p_ruta          => 'auto');                    -- auto | materializada | recursiva
-- -> id, kind, distancia, via_tipo, fuente ('materializada' | 'recursiva'), truncado
```

**Elección de ruta (`auto`).** Usa la tabla solo si se cumplen todas estas condiciones:

- el estado es `vigente` para el snapshot actual;
- `k ≤ 3`;
- el sentido es uno solo (`entrante` o `saliente`);
- los tipos son exactamente los cuatro de dependencia, en cualquier orden.

En cualquier otro caso usa el CTE recursivo con `CYCLE`. Con `materializada`, la función falla si la
tabla no se puede usar; con `recursiva`, fuerza el CTE. Las dos opciones sirven para pruebas y para el
diagnóstico.

**Mismo resultado.** Las dos rutas devuelven lo mismo que `SQL_IMPACTO` / `SQL_DEPENDENCIAS` de
`kallicode_core.cm_impacto` (contrato `cm-impacto-v1`):

- las mismas semillas;
- cada nodo una vez, con su distancia mínima;
- el `via_tipo` menor en orden `"C"`;
- orden por `distancia, id COLLATE "C"`.

**Límites.**

- **Profundidad:** `k` entre 1 y 5 (`K_MAX`); fuera de ese rango, error `invalid_parameter_value`.
- **Visitas, ruta recursiva:** el `LIMIT` se aplica dentro del recorrido. PostgreSQL evalúa la
  recursión nivel a nivel y bajo demanda, así que se detiene al llegar al tope. Se cuentan las filas
  del CTE, semillas incluidas.
- **Visitas, ruta materializada:** se cuentan los nodos devueltos.
- **Al superar el tope:** se devuelven los nodos más cercanos y `truncado = true` en todas las filas.
  No es un error ni un resultado silenciosamente incompleto.

## 4. Requisitos de los recorridos y cómo se vigilan

| Requisito | Prueba |
|---|---|
| Todo `WITH RECURSIVE` de una función de `cm` lleva `) CYCLE` | `test_todo_recorrido_recursivo_de_cm_lleva_cycle` |
| La política de `cm.nodes`, `cm.edges`, `cm.reachability` y `cm.reachability_estado` es exactamente `(tenant_id = core.current_tenant())` en `qual` y `with_check`, sin subconsultas, y con `FORCE RLS` | `test_las_politicas_son_exactamente_…` |
| El límite de visitas trunca de forma explícita en las dos rutas | `test_el_limite_de_visitas_…` |
| El límite detiene el trabajo: 30 nodos conectados todos con todos a k = 5, unos 2·10⁷ caminos sin límite, terminan dentro de un `statement_timeout` de 10 s | `test_el_limite_detiene_el_recorrido_de_un_grafo_denso` |
| La tabla y el CTE coinciden entre sí y con la SQL de Python | `test_las_dos_rutas_…` (30 combinaciones) y `test_grafo_aleatorio_…` (100 consultas sobre 80 nodos) |

El grafo de referencia de las pruebas cubre los casos que podrían separar las rutas:

- un atajo más corto que la cadena;
- un empate de tipos entre dos aristas;
- un ciclo y una autorreferencia;
- un nodo colgante fuera de `cm.nodes`, que se recorre pero no se devuelve;
- aristas `DEFINES`, que quedan fuera de los tipos de dependencia.

## 5. Diagnóstico

```bash
psql -d <base> -U kallicode_app -v ON_ERROR_STOP=1 -v tenant=<tenant_id> -v nodo=<id> \
     [-v k=3] [-v max_visitados=5000] [-v direccion=entrante] -f db/tools/cm_impacto.sql
```

El script muestra, en este orden:

1. el estado de la tabla frente al snapshot vigente;
2. el resultado de cada ruta;
3. las diferencias entre las dos rutas (vacío significa que son idénticas);
4. el tiempo de cada llamada.

El plan detallado del cuerpo de la función solo aparece si está cargado `auto_explain`. Todo corre en
una transacción que se revierte.

**Si la sección 3 no sale vacía con la tabla `vigente`, hay un defecto:** se rompió la equivalencia
entre las rutas. Mientras se corrige, `cm.impacto(..., p_ruta => 'recursiva')` da el resultado
correcto.

## 6. Límites conocidos

- **Coste del refresco:** se suma a `cm.publicar_snapshot`. Con 200 nodos y 600 aristas tarda unos
  35 ms; la medición a 200.000 nodos es KC-DB-11-3. Si un grafo denso supera el presupuesto, la tabla
  queda `excedida` y todo va por el CTE. Funciona, pero sin la aceleración.
- **Escrituras fuera de snapshot:** la tabla queda `obsoleta` hasta el siguiente refresco. El refresco
  incremental desde el refresco diario es KC-CM-11-2.
- **Resultados truncados:** cuando `truncado = true`, las dos rutas pueden devolver conjuntos
  distintos, porque una cuenta visitas y la otra nodos. Las dos devuelven los más cercanos.
- **Cliente Python:** que `cm_queries` / `cm_impacto` usen `cm.impacto()` y publiquen como métrica las
  caídas al recorrido en vivo es KC-CM-11-2. Este ticket deja la base lista.
- **Retención:** las dos tablas se declaran `conservar`. Son datos derivados que se reconstruyen en
  cada snapshot y se eliminan con el tenant mediante `ON DELETE CASCADE`.
