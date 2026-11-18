# Nota de release: Contrato congelado de consultas CodeMapping (`cm-grafo-v1` y `cm-consulta-v1`)

- **Ticket:** KC-CM-12-5
- **Contratos congelados:** `contratos/cm-grafo-v1.json` (`x-version: 1.0.0`) y `contratos/cm-consulta-v1.schema.json` (`v1`)
- **Módulos dueños:** `kallicode_core.cm_grafo`, `kallicode_core.cm_consulta`, `kallicode_codemapping.cm_queries`, `kallicode_codemapping.consulta_grafo`
- **Consumidores:** Backend (`KC-BE-12-1`) y Testing (`KC-QA-12-1`)

## 1. Operaciones disponibles y presupuestos de latencia (p95)

Todas las operaciones aplican aislamiento multi-tenant estricto por RLS (`tenant_id = core.current_tenant()`), ejecutan bajo fase de solo lectura con `statement_timeout` acotado y están verificadas en CI contra PostgreSQL 16 (`test_contrato_consulta.py`) y contra el doble en memoria (`ConsultaGrafoDoble`).

| Operación | Módulo / Función SQL | Presupuesto p95 | Condiciones de referencia |
| :--- | :--- | ---: | :--- |
| `resolver_nodos` | `cm_impacto.SQL_RESOLVER_NODOS` | 50 ms | Resolución por lote de identificadores sobre grafo de ~4.350 nodos |
| `semillas` | `cm_impacto.SQL_SEMILLAS` | 80 ms | Expansión inicial de ficheros y símbolos |
| `impacto` | `cm.impacto(..., 'entrante', 'auto')` | 500 ms | Cierre transitivo materializado (`cm.reachability`) con respaldo recursivo a `k <= 3` |
| `dependencias` | `cm.impacto(..., 'saliente', 'auto')` | 500 ms | Cierre transitivo materializado (`cm.reachability`) con respaldo recursivo a `k <= 3` |
| `subgrafo` | `cm_impacto.SQL_SUBGRAFO_*` | 350 ms | Subgrafo inducido hasta `k = 3` saltos |
| `vecinos` | `cm_impacto.SQL_VECINOS` | 60 ms | Vecindad directa (`k = 1`) entrante y saliente |
| `dependencias_ocultas` | `cm_impacto.SQL_DEPENDENCIAS_OCULTAS` | 250 ms | Detección de acoplamiento indirecto por tablas compartidas |
| `buscar_hibrido` | `cm.buscar_hibrido(...)` | 300 ms | Búsqueda vectorial HNSW (`halfvec(1024)`) + léxica con `ef_search = 64` |
| `resolver_nuggets` | `cm_consulta.SQL_NUGGETS_APLICABLES` | 100 ms | Selección y recorte por presupuesto de caracteres (`cm-consulta-v1`) |
| `resolver_incidentes` | `cm_incidentes` | 200 ms | Incidentes históricos vinculados al área (`saltos <= 2`) |

## 2. Límites conocidos y política de compatibilidad

1. **Profundidad máxima de recorrido:** El parámetro `k` está acotado a `1..3` saltos en las operaciones de impacto, dependencias y subgrafo; consultas con `k > 3` son rechazadas en validación de entrada.
2. **Tope de nodos alcanzados:** Los recorridos aplican un límite máximo de `MAX_NODOS = 500` nodos (`max_visitados`). Si el subgrafo supera ese umbral, la respuesta devuelve `truncado = true` de forma explícita.
3. **Respaldo de cierre transitivo:** Cuando `cm.reachability` queda marcada como `obsoleta` tras una mutación directa en `cm.edges`, `cm.impacto(..., 'auto')` cae de manera transparente al recorrido recursivo en vivo sin alterar el esquema de salida.
4. **Inmutabilidad de `v1`:** Cualquier cambio incompatible en entrada, salida, catálogo de operaciones o subida de presupuesto p95 rompe el CI (`test_cm_grafo.py` y `test_contrato_stats_ingesta.py`) si no publica una nueva versión y se anuncia previamente en `contratos/cambios-anunciados.yaml` con al menos un sprint de convivencia.

## 3. Aceptación formal de consumidores (KC-CM-12-5)

| Rol consumidor | Ticket relacionado | Responsable | Estado |
| :--- | :--- | :--- | :--- |
| Tech Lead de Backend | `KC-BE-12-1` | _Pendiente de firma_ | **pendiente** |
| Tech Lead de Testing | `KC-QA-12-1` | _Pendiente de firma_ | **pendiente** |