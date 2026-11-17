# Refresco, purga y presupuesto de la capa documental

KC-CM-08-5 y KC-CM-08-6. Código en `ingest_docs.py`, `budget.py`, `fase_documental.py`; migración `045`.

## Refresco (KC-CM-08-5)

`refrescar_documentos` corre en cada refresco diario (`refresh.py`, también cuando el código no cambió):

1. **Purga** los documentos con `eliminado_en`: borra `vec.doc_chunks` y, por la FK en cascada `dcl_chunk_fk`,
   `cm.doc_chunk_links`, más `hash_contenido = NULL`, todo en una transacción (`GraphLoader.purgar_documento`).
   Un `DELETE` físico del documento ya arrastra sus chunks por `ON DELETE CASCADE` (040).
2. **Hash**: cada documento activo (`pendiente` o `procesado`) se relee del blob y se calcula su SHA-256.
   - Procesado y mismo hash → `omitido`: no se extrae, no se trocea, **0 llamadas a TEI**.
   - Hash distinto o documento nuevo → se reingiere; solo se re-embeben los chunks cuyo `texto_hash` cambió.
   - Mismo contenido que *otro* documento del tenant → `DOCUMENTO_DUPLICADO`, sin vectores ni enlaces nuevos.
   - Blob inexistente en un documento activo → `fallido` con `BLOB_NO_DISPONIBLE`.
3. **Stats**: `cm.repo_snapshots.stats -> 'documentos'` = `{procesados, omitidos, fallidos, purgados, pendientes_restantes,
   chunks_embebidos, presupuesto{...}}`. Si el código no cambió, quedan en el último snapshot completo.

Los enlaces documento→grafo viven en `cm.doc_chunk_links`, no en `cm.edges`; por eso la purga actúa sobre aquella tabla.

## Presupuesto (KC-CM-08-6)

`ControlPresupuestoDocs` limita la fase por **tokens estimados** (`CM_DOCS_PRESUPUESTO_TOKENS`), **tiempo**
(`CM_DOCS_PRESUPUESTO_SEGUNDOS`, 0 = sin límite) y **peso de los pendientes** frente a `core.plans.docs_mb`
(NULL = ilimitado). Flujo:

1. *Dry-run*: se leen, validan y trocean todos los documentos candidatos (sin TEI) y se fija la estimación
   (solo los chunks que se van a embeber).
2. Antes de embeber cada documento se pide autorización. Al superar un límite se detiene **solo la fase documental**
   (`presupuesto_documental_excedido` en logs y stats): lo ya ingerido se conserva, el resto sigue `pendiente` y el
   grafo de código queda utilizable. La siguiente corrida los retoma.
3. Consumo: una fila por corrida en `core.usage_tokens` con `origen = 'codemapping_docs'`, `ticket_id NULL` y
   `snapshot_id`. Si la migración 045 no está aplicada se avisa y la ingesta continúa.
4. Desviación: `(real - estimado) / estimado` sobre los documentos efectivamente embebidos, en
   `stats.documentos.presupuesto.desviacion_pct`.

Limitación: TEI no informa el uso, así que "real" se calcula con el mismo estimador (`estimar_tokens_texto`) sobre los
textos enviados a TEI. La desviación detecta lo que cambia entre la estimación y la ejecución (documentos que fallan,
chunks reutilizados), no el error del estimador frente a un tokenizador real.
