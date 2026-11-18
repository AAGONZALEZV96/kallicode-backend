# Estabilización de CodeMapping antes de GA (KC-CM-12-2)

Cierre de deuda del paquete `packages/codemapping`: defectos abiertos con su severidad, cobertura por
ámbito, registro de cuarentena y estado del CI en la rama principal. Medido el 18 de noviembre de 2026.

## Resumen

| Criterio | Resultado |
|---|---|
| Cero defectos bloqueantes y como máximo dos altos abiertos | **Se cumple.** 0 bloqueantes y 1 alto abierto (D3). El bloqueante que quedaba (H1, pruebas que se saltaban en CI) y los altos H2, H4 y D8 se cierran en este ticket. Ver [Registro de defectos](#registro-de-defectos). |
| Cobertura global > 80 % y > 90 % en consulta, cargador y aislamiento | **Se cumple.** Global **94,5 %** (compuerta del CI: 90 %). Consulta **97,6 %**, cargador **94,2 %**, aislamiento **100 %**, con su propia compuerta en el CI. Ver [Cobertura](#cobertura). |
| Ninguna prueba en cuarentena sin ticket y con fecha de resolución | **Se cumple.** Una sola prueba en cuarentena, con ticket y fecha. El CI falla con cualquier prueba no ejecutada que no esté en el registro. Ver [Cuarentena](#cuarentena). |
| CI en verde en cinco ejecuciones consecutivas de la rama principal | **Pendiente de medir tras el merge.** Antes de este ticket, los cinco últimos `push` a `main` estaban en verde. Ver [CI en la rama principal](#ci-en-la-rama-principal). |

## Registro de defectos

Severidad: **bloqueante** impide la release; **alto** da un resultado incorrecto o deja sin verificar una
propiedad del producto; **medio** es una limitación conocida con alternativa; **bajo**, deuda sin efecto
funcional.

### Encontrados en este ticket

| # | Defecto | Severidad | Estado |
|---|---|---|---|
| H1 | **Pruebas de integración que se saltaban en silencio en CI.** `test_rls_cm` (la suite RLS de las 11 tablas de `cm`), `test_graph_loader`, `test_extract_sql`, `test_build_graph_resume` y `test_lenguajes_pg` tenían la base fija en `localhost:5432/kallicode`; en CI la base es `kallicode_test`, así que el `skipif` las saltaba. `test_cm_reachability_queries` pasaba a psycopg una URL `postgresql+asyncpg://` y se saltaba igual. El CI mostraba «21 skipped» sin detalle. | Bloqueante | **Cerrado.** Las URLs salen de `KC_DATABASE_URL_MIGRATIONS` / `KC_DATABASE_URL_ADMIN`. Las 17 pruebas corren contra PostgreSQL real; al activarse aparecieron H1b, H1c y H1d. El registro de cuarentena impide que vuelva a pasar. |
| H1c | Al activarse, **la suite RLS de `cm` fallaba**: sembraba `cm.nuggets` sin `tipo` y con una evidencia inexistente, que la migración 041 hizo obligatorios (`NOT NULL` y FK a `vec.doc_chunks`). La propiedad de aislamiento de `cm` llevaba desde esa migración sin verificarse en ninguna parte. | Alto | **Cerrado.** La suite siembra un chunk real y deja que el trigger de 041 abra la revisión del nugget. Las 4 pruebas pasan: ninguna fuga de lectura, escritura, borrado ni suplantación en las 11 tablas. |
| H1d | Al activarse, `test_cm_reachability_queries` fallaba: su conexión de aplicación leía `KC_DATABASE_URL_APP`, que no existe, y caía a un valor fijo con una contraseña literal en el código. | Medio | **Cerrado.** Usa `KC_DATABASE_URL`; se retiran las contraseñas literales de la prueba. |
| H1b | Al activarse, `test_build_graph_resume` fallaba: crea su repositorio con `git init` (rama `master` en muchas máquinas) y la ingesta clona `main`. Era D8 visto desde las pruebas. | Alto | **Cerrado** con D8 y `git init -b main` en la prueba. |
| H2 | El camino del refresco incremental del cargador (`delete_file_subgraph`, `obtener_ultimo_snapshot_completo`) solo se probaba con dobles o en pruebas afectadas por H1: el cargador estaba al 86 %. | Alto | **Cerrado.** Prueba contra PostgreSQL de los dos métodos, con aislamiento entre tenants. |
| H3 | Código muerto en el cargador: `get_file_hashes`, `get_symbol_ids`, `get_vector_hashes`, sin llamadas. | Bajo | **Cerrado.** Retirados. Nota para Database (KC-DB-12-4): el inventario provisional de `docs/plan-indices.md` cita Q-233, Q-234 y Q-236; los índices afectados siguen justificados por otras consultas (Q-228, Q-237, Q-241). |
| H4 | mypy tiene desactivados `call-arg` y `arg-type` en todo el repositorio: la causa de fondo de D2. | Alto | **Cerrado** para el código del paquete: override en `pyproject.toml` para `kallicode_codemapping.*`, con 3 errores corregidos (Protocol del cursor en `nuggets.py`, fábrica de loaders del worker y Protocol duplicado `FabricaLoader`). |
| H4b | Las pruebas de codemapping siguen sin esos dos códigos: 38 avisos, en su mayoría literales `str` donde se espera un `Literal`, coerción de Pydantic (`str` → `AnyUrl`) y dobles tipados con la clase concreta. | Bajo | Deuda registrada. |
| H5 | Sin registro de cuarentena: un skip no tenía ticket ni fecha. | (criterio 3) | **Cerrado.** Ver [Cuarentena](#cuarentena). |
| H6 | Compuerta de cobertura en 70 %, por debajo del criterio y de lo medido, y sin compuerta por módulo. | (criterio 2) | **Cerrado.** 90 % global y 90 % por ámbito. |

### Registrados en el ADR-018

| # | Defecto | Dueño | Severidad | Estado |
|---|---|---|---|---|
| D2 | Ingesta `completo` no ejecutable | CodeMapping | Bloqueante | **Cerrado** (en `develop`) |
| D3 | El pipeline no publica el snapshot: `cm.reachability` nunca queda vigente | CodeMapping + Database | **Alto** | **Abierto.** No basta con llamar a `cm.publicar_snapshot`: la carga solo marca `snapshot_id` en `cm.files` y el refresco incremental conserva el snapshot anterior en los archivos sin cambios. Publicar y retirar el snapshot previo borraría nodos vigentes. Hace falta diseñar con Database el marcado de todos los nodos y su arrastre en el refresco. Mientras tanto el impacto usa el CTE recursivo, que es correcto y cumple el presupuesto (ADR-020). |
| D8 | Rama a ingerir fija en `main` | CodeMapping | Alto | **Cerrado.** `CM_TARGET_BRANCH` (por defecto `main`) |
| D4, D5, D6 | `DEFINES`/`HAS_COLUMN` no se escriben · `USES_TABLE` sale del archivo · el esquema no restringe pares de nodos | Database / CodeMapping | Medio | Deuda registrada en el ADR-018 |
| D9 | Nadie publica el evento de ingesta desde el onboarding | Backend | Medio | Fuera del paquete |
| D10, D12 | Descripciones LLM perdidas en la reconstrucción · RPO/RTO sin aprobar | Database | Medio | Deuda registrada |
| D7, D11 | Sin comparación con Neo4j · coste del refresco de `cm.reachability` | CodeMapping / Database | Bajo | Seguimiento |

Las siete desviaciones de calidad semántica del [informe de calidad](informe-calidad-cm.md#desviaciones)
son limitaciones de exactitud con el criterio de KC-CM-12-4 cumplido: severidad media, con su propuesta en
el informe.

**Recuento:** 0 bloqueantes, 1 alto (D3), 7 medios (D4, D5, D6, D9, D10, D12 y las semánticas en
bloque) y 3 bajos (D7, D11, H4b).

## Cobertura

Fuente: suite completa del paquete contra PostgreSQL 16 y Redis reales, como el job `CI - codemapping`
(1038 pasan, 1 saltada y registrada, 0 fallos).
Fórmula de `coverage report --cov-branch`: (líneas + ramas cubiertas) / (líneas + ramas).

| Ámbito | Módulos | Antes (CI 18-11) | Después |
|---|---|---|---|
| Global | 37 módulos | 93,8 % | **94,5 %** |
| Consulta | `cm_queries.py`, `consulta_grafo.py`, `recuperacion.py` | 96-99 % | **97,6 %** |
| Cargador | `graph_loader.py` | **86 %** | **94,2 %** |
| Aislamiento | `cm_db.py` (confinamiento RLS por `app.tenant_id`), `security.py` (contenido no confiable para el LLM) | 100 % | **100 %** |

La suite RLS de `cm` (`tests/test_rls_cm.py`) cubre la propiedad de aislamiento en la base. Hasta este
ticket no corría en CI (H1).

**Compuertas en `.github/workflows/ci.yml` (matriz `codemapping`):**

- `--cov-fail-under=90` sobre el paquete (antes 70). La regla del repositorio: el umbral sube y nunca baja.
- `packages/codemapping/scripts/verificar_cobertura_modulos.py coverage.xml --minimo 90`: falla si un
  ámbito baja del 90 %, aunque el global aguante. La definición de cada ámbito está en el propio script.

## Cuarentena

Registro: [`packages/codemapping/tests/cuarentena.yaml`](../../packages/codemapping/tests/cuarentena.yaml).
Cada entrada lleva la prueba, el motivo, el ticket y la fecha `resolver_antes`.

Con `KC_CM_CUARENTENA_ESTRICTA=1`, que el CI pone en el job de codemapping, el conftest del paquete
(`packages/codemapping/conftest.py`) marca como fallida:

- toda prueba saltada o `xfail` que no esté en el registro;
- toda prueba del registro cuya fecha ya pasó;
- y detiene la sesión si el registro está mal formado (campo vacío, ticket que no es `KC-XX-NN-N`,
  fecha inválida o entrada repetida).

En local, sin la variable, un skip por falta de PostgreSQL sigue siendo un skip. `tests/test_cuarentena.py`
prueba el hook y comprueba que el registro real no tiene entradas vencidas ni pruebas que ya no existan.

| Prueba | Motivo | Ticket | Resolver antes de |
|---|---|---|---|
| `tests/test_calidad_semantica.py::test_las_50_preguntas_superan_los_umbrales_con_bge_m3` | Necesita TEI con BGE-M3; corre cada noche en `calidad_semantica_nocturno.yml` | KC-CM-12-4 | 2027-01-31 |

## CI en la rama principal

`ci.yml` corre en cada `push` a `main`. Antes de este ticket, las cinco últimas ejecuciones estaban en
verde: 44001630399, 44011345769, 44026932490, 44028365332 (17-11-2026) y 42762094792 (10-11-2026).

El criterio se comprueba de nuevo cuando este cambio llegue a `main` por el merge de `develop`. Se usa la
API de Actions: `GET /repos/{owner}/{repo}/actions/workflows/ci.yml/runs?branch=main&event=push`, y las
cinco primeras ejecuciones tienen que tener `conclusion = success`. El resultado se anota abajo.

| Fecha | Ejecuciones (más reciente primero) | Resultado |
|---|---|---|
| | | |
