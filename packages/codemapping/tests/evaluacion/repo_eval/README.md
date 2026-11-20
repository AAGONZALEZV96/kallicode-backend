# repo_eval: repositorio de evaluación de la calidad semántica (KC-CM-12-4)

Mini aplicación de facturación (Python con SQLAlchemy y SQL crudo, portal en TypeScript y TSX).
Sobre ella se hacen las 50 preguntas de `../preguntas.yaml`. La respuesta de cada pregunta está
escrita **a mano** leyendo este código. No se deriva del grafo ni se regenera desde él.

Si se cambia el código, también se revisan a mano las respuestas afectadas y se regenera la línea
base (`docs/calidad/linea_base_calidad_cm.json`). Este README no es código: `walk_sources` lo ignora.

| Fichero | Para qué está |
|---|---|
| `facturacion/modelos.py` | Modelos declarativos (`__tablename__`) y métodos que se llaman como `factura.total_bruto()` |
| `facturacion/utilidades.py` | `redondear`, homónimo del de `portal/formato.ts` (otro lenguaje), y `validar`, homónimo del de `clientes/validaciones.py` (el mismo lenguaje) |
| `facturacion/servicio_facturas.py` | Constructores de modelos (`Factura(...)` y `LineaFactura(...)` con `session.add`), y `session.get` de SQLAlchemy, homónimo de `Cache.get` |
| `facturacion/pagos.py`, `impuestos.py`, `auditoria.py`, `contabilidad.py` | ORM y SQL crudo, también con esquema (`contab.asientos`) |
| `clientes/` | SQL crudo con JOIN; el `validar` local |
| `notificaciones/` | Patrón outbox (`notificaciones`), cadenas de llamadas de 2 y 3 saltos |
| `informes/` | CTE que no es una tabla (`pendientes`) y despacho por diccionario (`MOTORES[formato](filas)`) |
| `api/` | Endpoints y tareas: los llamadores finales; la caché con su método `get` |
| `portal/*.ts` | El `validar` y el `redondear` de TypeScript, y un callback anónimo dentro de `map` |
| `portal/PantallaPago.tsx` | Un `.tsx` que llama a funciones de `.ts` y un closure con nombre (`pagar`) |
| `esquema.yaml` | El esquema de la base del cliente: lo que devolvería `extract_schema` |
| `descripciones.yaml` | La salida fija de la fase de descripción (LLM) para el texto de los embeddings |

## Reglas de la respuesta

- Se recorren k saltos por las aristas de dependencia del contrato `cm-impacto-v1` (CALLS, USES_TABLE,
  REFERENCES y HAS_COLUMN). Llamar al constructor de una clase es depender de esa clase.
- USES_TABLE va de fichero a tabla, como en la ontología del grafo. Cuenta como uso declarar el
  modelo, o leer o escribir la tabla por SQL o por ORM.
- El código de un closure con nombre también pertenece a la función que lo contiene. Los anónimos no
  se listan y se ignoran en lo devuelto.
- Las semillas (distancia 0) no se cuentan.
- `dependencias_ocultas` responde a esta pregunta: quién toca las mismas tablas que el código que
  el área alcanza por llamadas, sin que se llegue a él por llamadas. Un fichero cuyos símbolos se
  alcanzan no está oculto.
- Búsqueda: los k primeros resultados de la capa `symbol`, con k igual al número de esperados
  (R-precisión).
