# ADR 003: Búsqueda Vectorial y Particionado Multi-Tenant con pgvector

## Contexto
KalliCode requiere capacidades de búsqueda semántica mediante embeddings para la resolución de tickets. Al ser una arquitectura multi-tenant, debemos garantizar el aislamiento estricto de los datos por cliente (tenant) y asegurar que las búsquedas de similitud vectorial (K-NN) sean de baja latencia. Adicionalmente, las pruebas en el pipeline CI/CD no pueden depender de APIs externas (como HuggingFace o OpenAI) por latencia, costos y límites de tasa.

## Decisión Técnica

1. **Motor Vectorial:** Utilizaremos la extensión `pgvector` nativa en nuestra instancia de PostgreSQL.
2. **Estrategia de Indexación (HNSW):** Se utilizarán índices HNSW (Hierarchical Navigable Small World) en lugar de IVFFlat. HNSW permite la inserción incremental de vectores sin necesidad de una fase de "entrenamiento" previa y ofrece mejor latencia (ideal para flujos de tickets dinámicos).
3. **Aislamiento Multi-Tenant:** La búsqueda vectorial se combinará **siempre** con el filtro estricto de `tenant_id` respaldado por Row-Level Security (RLS) en Postgres. El planificador de consultas de Postgres filtrará primero por tenant antes de calcular la distancia vectorial.
4. **Mocking en Pipeline:** Todas las llamadas a las funciones de "embebido" (ej. `_embeber`)serán interceptadas y falsificadas en la suite de pruebas mediante Pytest `monkeypatch`, retornando vectores estáticos pre-calculados.

## Consecuencias
- **Positivas:** Cero costos de facturación de IA durante la CI/CD. Tiempo de ejecución de pruebas garantizado <5s. Aislamiento criptográfico y lógico de la data vectorial por cliente.
- **Negativas/Riesgos:** El tamaño de almacenamiento de los índices HNSW en memoria RAM es mayor. El equipo de DB (Miembro A) deberá monitorear la métrica de `shared_buffers` en producción.
