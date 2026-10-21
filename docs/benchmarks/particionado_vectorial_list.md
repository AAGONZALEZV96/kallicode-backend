# Benchmark y Evaluación Técnica: Particionado LIST en Capa Vectorial (KC-DB-07-5)

## 1. Contexto y Objetivo
En Kallicode, la tabla `vec.definition_embeddings` almacena representaciones vectoriales densas de 1024 dimensiones (`bge-m3`) con índices HNSW. El objetivo de este benchmark es determinar empíricamente si la adopción de particionado declarativo `LIST` sobre la columna `capa` ('file', 'symbol', 'db_table', 'db_column', 'definition', 'concept') proporciona ventajas de rendimiento frente a la arquitectura actual de tabla plana con índice HNSW global.

## 2. Metodología
Se desplegó un entorno comparativo aislado en PostgreSQL 16 con `pgvector`:
- **Esquema A (Plana):** Tabla no particionada con índice HNSW global `(m=16, ef_construction=64, vector_cosine_ops)`.
- **Esquema B (Particionada LIST):** Tabla declarativa `PARTITION BY LIST (capa)` con 6 particiones fijas e índices HNSW locales independientes por partición.
- **Volumen de prueba:** Vectores sintéticos normalizados de 1024 dimensiones.
- **Consultas evaluadas ($k$-NN con operador `<=>`, $k=10$):**
  1. *Consulta con poda (Partition Pruning):* `WHERE capa = 'symbol' ORDER BY embedding <=> query LIMIT 10`
  2. *Consulta global:* `ORDER BY embedding <=> query LIMIT 10`

## 3. Hallazgos y Análisis de Rendimiento

### A. Consultas con Partition Pruning (`WHERE capa = '...'`)
- Cuando la consulta incluye explícitamente la clave de partición, el optimizador de PostgreSQL aplica poda estática y dinámica.
- El escaneo se limita exclusivamente al grafo HNSW de la partición objetivo, lo que reduce el espacio de memoria de trabajo requerido y ofrece latencias $p50$ ligeramente inferiores en datasets de gran escala.

### B. Consultas Globales (Multi-Partición)
- Cuando la búsqueda no restringe la `capa`, PostgreSQL debe ejecutar un nodo `Append` o `Merge Append` combinando los resultados de los índices HNSW de cada una de las 6 particiones hijas.
- Esto introduce una sobrecarga significativa en la coordinación de heap y puede comprometer la exhaustividad y el recall global si los subconjuntos retornados por cada índice local difieren en calibración de distancias relativas.

## 4. Veredicto Arquitectónico y Recomendación
1. **Mantener la tabla `vec.definition_embeddings` plana:** Dado que el motor de búsqueda y el agente frecuentemente realizan búsquedas semánticas abiertas sin conocer a priori la capa del artefacto de código, penalizar las consultas globales con un nodo `Append` degradaría la latencia general del sistema.
2. **Uso de índices combinados:** Para optimizar las búsquedas filtradas por inquilino y capa, la tabla plana ya cuenta con el índice compuesto B-tree `definition_emb_tenant_ix (tenant_id, capa, modulo)` junto con el índice HNSW global asistido por escaneo iterativo (`iterative_scan = relaxed`).