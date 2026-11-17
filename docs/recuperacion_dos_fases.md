# Recuperación en dos fases para Diseño y duplicados de tickets (KC-CM-09-5)

Contrato `cm-recuperacion-v1` en `kallicode_core.cm_recuperacion`. Orquestación en
`kallicode_codemapping.recuperacion`.

## 1. Contexto para la etapa Diseño

```python
with GraphLoader(settings=settings) as loader:
    ctx = contexto_diseno(loader, TEIEmbeddingClient(settings.tei_endpoint_url),
                          "La factura duplica el IVA al reemitir",
                          ficheros=["src/billing/invoice.py"], settings=settings)
    ctx.texto                                   # va a la plantilla de Diseño
    ctx.tokens_estimados <= ctx.presupuesto_tokens
```

| Fase | Qué hace | Dónde |
|---|---|---|
| 1. Búsqueda amplia | Los `k_amplio` (50 por defecto, máx. 100) elementos más parecidos al texto del ticket en las capas símbolo, fichero, tabla, columna, definición, documentación y nuggets, con los parámetros HNSW calibrados de `diseno`. | `cm.buscar_hibrido` (KC-CM-08-4) |
| 2. Re-ranking topológico | Cada candidato que es un nodo del grafo recibe `puntuacion = (1 - w) * similitud + w * 1 / (1 + saltos)`. `saltos` es la distancia mínima, en ambos sentidos, al área del ticket. Fuera del vecindario, o si no es un nodo (documentación, nuggets), la cercanía es 0. | `cm_queries.subgrafo` (KC-CM-09-1) |
| Recorte | Se agregan elementos por puntuación mientras el texto completo quepa en el presupuesto. | `ajustar_a_presupuesto` |

**Área del ticket.** Son los `nodos` y `ficheros` que se pasan (por ejemplo, los que estima Triage).
Sin ellos, el área son los 3 mejores nodos de la fase 1. Si tampoco hay nodos entre los candidatos
(por ejemplo, solo documentación), no hay re-ranking y se conserva el orden de la fase 1
(`reordenado = False`).

**Presupuesto.** `tokens_estimados <= presupuesto_tokens` siempre: el modelo de salida rechaza lo
contrario. Un elemento que no cabe se omite y se prueba con el siguiente, porque uno más corto puede
caber. Si ninguno ha entrado todavía, el primero se recorta a lo que quepa, para no entregar un
contexto vacío habiendo candidatos. Cada elemento aporta como máximo 1.200 caracteres. Los tokens son
la misma estimación determinista que usa `budget.py` (`estimar_tokens_texto`).

| Parámetro (`CM_*`) | Defecto | Efecto |
|---|---|---|
| `DISENO_PRESUPUESTO_TOKENS` | 6000 | Máximo de tokens del contexto de Diseño (mínimo 100). |
| `DISENO_PESO_TOPOLOGICO` | 0,35 | `w`. 0 = solo la fase 1; 1 = solo el grafo. |
| `DISENO_SALTOS` | 2 | Hasta cuántos saltos cuenta como vecindad (1 a 5). |

## 2. Duplicados semánticos

```python
with GraphLoader(settings=settings) as loader:
    r = buscar_duplicados(loader, tei, "Error al exportar facturas a PDF",
                          ticket_id=None, umbral=settings.dedup_umbral)
    [(d.numero, d.similitud) for d in r.duplicados]
```

- Busca en `vec.ticket_embeddings` con los parámetros HNSW de `dedup` (`vec.configurar_busqueda`,
  KC-DB-08-1), en la misma transacción.
- **Duplicado = coseno estrictamente superior al umbral** (`CM_DEDUP_UMBRAL`, 0,70 por defecto, igual
  que `kallicode_core.config.Settings.dedup_umbral`). Con el umbral exacto no lo es.
- Devuelve también los `k` candidatos más parecidos (`candidatos`), útil para mostrar "similares".
- `ticket_id` excluye al propio ticket cuando ya tiene embedding.

## 3. Aislamiento por tenant

Las dos consultas filtran con `tenant_id = core.current_tenant()` (el tenant de la sesión) además
de RLS. `tests/test_recuperacion_pg.py` siembra dos organizaciones con los mismos ids y vectores
idénticos: ningún resultado cruza de tenant.

## 4. Pruebas

| Archivo | Qué cubre |
|---|---|
| `test/test_recuperacion_unit.py` | Fórmula de puntuación, re-ranking, recorte al presupuesto (nunca lo supera), parámetros, umbral estricto, SQL y filtro de tenant. Sin base de datos. |
| `tests/test_recuperacion_pg.py` | Contra Postgres 16 + pgvector con vectores de coseno exacto: el grafo cambia el orden, presupuesto, umbral, aislamiento entre tenants y exclusión del propio ticket. |

## 5. Limitaciones

- La cercanía usa saltos en ambos sentidos con todos los tipos de arista (`subgrafo`). No pondera por
  tipo de arista.
- Documentación y nuggets compiten solo por similitud (no tienen posición en el grafo). Quedan por
  debajo de un nodo del área con la misma similitud cuando `w > 0`.
- Los tokens son una estimación (≈ 3,7 caracteres por token), no el conteo del modelo de Diseño.
- El peso 0,35 y los 2 saltos son valores iniciales razonables, no calibrados con un conjunto de
  tickets reales. Se pueden ajustar sin tocar código.
- El embedding de un ticket nuevo hay que guardarlo (`upsert_embedding_ticket`) para que sea
  candidato en búsquedas futuras. Eso lo sigue haciendo Triage, no este módulo.
