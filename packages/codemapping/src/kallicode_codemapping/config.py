"""CodeMapping - Configuración central validada mediante Pydantic Settings."""

from __future__ import annotations

from pydantic import AnyUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CM_",
        env_file=".env",
        extra="ignore",
    )

    # Identificador multi-tenant estricto y obligatorio (sin default)
    tenant_id: str = Field(
        ...,
        description="Identificador único del tenant cliente",
    )

    @property
    def client_id(self) -> str:
        """Alias de compatibilidad hacia atrás para módulos en transición."""
        return self.tenant_id

    # Repositorio y almacenamiento relacional (ADR-018)
    repo_url: str = Field(..., description="URL o ruta del repositorio local")
    database_url: AnyUrl | None = Field(
        default=None,
        description="URL de conexión a la base de datos DEL CLIENTE (fase 3, extract_sql.py)",
    )
    client_db_max_connections: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Tope de conexiones simultaneas contra la base del cliente (pool + overflow), CM-F13",
    )
    client_db_statement_timeout_ms: int = Field(
        default=30_000,
        ge=100,
        description="statement_timeout aplicado a cada sentencia contra la base del cliente",
    )
    # KC-CM-12-6: el resto de salvaguardas de la conexión a la base productiva del cliente.
    client_db_connect_timeout_s: int = Field(
        default=10,
        ge=1,
        le=120,
        description="connect_timeout de libpq: una base del cliente inalcanzable no cuelga la ingesta",
    )
    client_db_lock_timeout_ms: int = Field(
        default=5_000,
        ge=100,
        description="lock_timeout: la extracción nunca espera un bloqueo de la base del cliente",
    )
    client_db_idle_tx_timeout_ms: int = Field(
        default=60_000,
        ge=1_000,
        description="idle_in_transaction_session_timeout: ninguna transacción queda abierta",
    )
    client_db_exclude_schemas: list[str] = Field(
        default_factory=lambda: ["information_schema", "pg_catalog", "pg_toast"],
        description="Esquemas de la base del cliente que la extraccion no recorre",
    )
    store_database_url: str = Field(
        default="postgresql+psycopg://postgres:dev@localhost:5432/kallicode",
        description="PostgreSQL de Kallicode (esquemas cm y vec) donde persiste el grafo",
    )
    tei_endpoint_url: str = Field(
        default="http://localhost:8080",
        description="Endpoint del microservicio TEI para embeddings BGE-M3",
    )

    # Parámetros de Ingesta de Repositorio (ingest_repo.py)
    target_branch: str = Field(
        default="main",
        min_length=1,
        description="Rama del repositorio del cliente que se ingiere (D8 del ADR-018, KC-CM-12-2)",
    )
    workdir: str = Field(default="./workdir", description="Directorio de trabajo para clonar repos")
    exclude_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git",
            "node_modules",
            "venv",
            ".venv",
            "dist",
            "build",
            "__pycache__",
            "vendor",  # dependencias de Go y PHP
            "obj",  # salida de compilación de .NET (incluye .cs generados)
        ],
        description="Carpetas excluidas del escaneo AST",
    )
    include_ext: list[str] = Field(
        default_factory=lambda: [
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".py",
            ".java",
            ".cs",
            ".go",
            ".php",
            ".kt",
            ".kts",
            ".sql",
            ".prisma",
        ],
        description="Extensiones de archivo a ingerir (tree-sitter; .prisma solo para USES_TABLE)",
    )
    max_file_bytes: int = Field(
        default=500_000,
        description="Límite máximo de tamaño por archivo de código en bytes",
    )

    # Parámetros de Enriquecimiento LLM (llm_enrich.py)
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1", description="URL base de la API de LLM"
    )
    llm_api_key: str = Field(default="test_key", description="Clave API del proveedor LLM")
    batch_size: int = Field(default=20, description="Tamaño del lote para llamadas al LLM")
    model_flash: str = Field(
        default="deepseek-chat", description="Modelo tier flash para anotación y enlaces"
    )
    model_pro: str = Field(
        default="deepseek-coder", description="Modelo tier pro para inducción de ontología"
    )
    presupuesto_tokens_tenant_mes: int = Field(
        default=2_000_000,
        ge=0,
        description="Cuota de tokens LLM del tenant por ciclo de facturación; la ingesta no arranca "
        "las fases 4-5 si su estimación no cabe en lo que queda (core.usage_tokens)",
    )
    link_confidence_threshold: float = Field(
        default=0.75,
        description="Umbral de confianza para persistir enlaces; bajo este valor va a la cola de curación",
    )

    # Ingesta de documentación (ingest_docs.py, KC-CM-08-1)
    blob_account_url: str = Field(
        default="http://localhost:10000/devstoreaccount1",
        description="Cuenta de Blob Storage donde el portal sube core.onboarding_documents.blob_ref",
    )
    blob_container: str = Field(
        default="evidencia", description="Contenedor de los documentos de onboarding"
    )
    docs_chunk_max_chars: int = Field(
        default=1500, ge=200, description="Tamaño máximo de un chunk de documentación (caracteres)"
    )
    docs_chunk_overlap_chars: int = Field(
        default=200, ge=0, description="Solape entre ventanas de una misma sección larga"
    )
    docs_link_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Similitud coseno mínima para enlazar un chunk a un nodo por vía semántica",
    )
    docs_link_top_k: int = Field(
        default=5, ge=0, le=50, description="Nodos semánticos candidatos por chunk (0 = desactivar)"
    )
    docs_ontology_max_excerpts: int = Field(
        default=200,
        ge=0,
        description="Chunks de documentación que entran a la inducción de ontología",
    )
    tei_batch_size: int = Field(
        default=32, ge=1, description="Textos por petición a TEI (--max-client-batch-size)"
    )
    # Presupuesto de la ingesta documental (KC-CM-08-6)
    docs_presupuesto_tokens: int = Field(
        default=2_000_000,
        ge=0,
        description="Tokens (estimados) que la fase documental puede embeber por corrida",
    )
    docs_presupuesto_segundos: int = Field(
        default=1800,
        ge=0,
        description="Tiempo máximo de la fase documental por corrida (0 = sin límite)",
    )
    # Reparación de la vigencia de los embeddings tras el refresco (KC-CM-11-4)
    vigencia_max_reembebidos: int = Field(
        default=5000,
        ge=0,
        description="Nodos obsoletos o sin embedding que el refresco reembebe por corrida (0 = no repara)",
    )
    # Propuesta de nuggets desde la documentación (KC-CM-08-2)
    nuggets_enabled: bool = Field(
        default=True,
        description="Proponer cm.nuggets (pendientes de curación) desde los documentos procesados",
    )
    nuggets_max_chunks_por_lote: int = Field(
        default=8, ge=1, le=50, description="Chunks de documentación por llamada al extractor"
    )
    nuggets_max_caracteres_por_lote: int = Field(
        default=12000,
        ge=1000,
        description="Caracteres de contenido por llamada al extractor (presupuesto de entrada)",
    )
    # Recuperación en dos fases para Diseño y duplicados (KC-CM-09-5)
    diseno_presupuesto_tokens: int = Field(
        default=6_000,
        ge=100,
        description="Tokens (estimados) máximos del contexto que se entrega a la etapa Diseño",
    )
    diseno_peso_topologico: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Peso de la cercanía en el grafo frente a la similitud vectorial (fase 2)",
    )
    diseno_saltos: int = Field(
        default=2, ge=1, le=5, description="Saltos del grafo que cuentan como vecindad del área"
    )
    dedup_umbral: float = Field(
        default=0.70,
        ge=0.0,
        le=1.0,
        description="Coseno por encima del cual un ticket histórico es duplicado semántico",
    )


def build_settings(**overrides: object) -> Settings:
    """Fábrica explícita: cada llamada crea una instancia propia, sin estado
    compartido entre tenants."""
    return Settings(**overrides)  # type: ignore[arg-type]
