from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CM_",
        env_file=".env",
        extra="ignore",
    )

    # Identificación multi-tenant canónica
    tenant_id: str = Field(
        default="default_tenant",
        description="Identificador único del tenant cliente",
    )

    # Repositorio y almacenamiento relacional (ADR-018)
    repo_url: str = Field(default="", description="URL o ruta del repositorio local")
    database_url: str = Field(
        default="", description="URL de conexión a la base de datos DEL CLIENTE (fase 3, extract_sql.py)"
    )
    platform_database_url: str = Field(
        default="postgresql+psycopg://postgres:dev@localhost:5432/kallicode",
        description="URL de conexión a la base de datos DE KALLICODE, donde vive el esquema cm (KC-ONB-03-1)",
    )
    tei_endpoint_url: str = Field(
        default="http://localhost:8080",
        description="Endpoint del microservicio TEI para embeddings BGE-M3",
    )

    # Parámetros de Ingesta de Repositorio (ingest_repo.py)
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
        ],
        description="Carpetas excluidas del escaneo AST",
    )
    include_ext: list[str] = Field(
        default_factory=lambda: [".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".sql"],
        description="Extensiones de archivo soportadas por tree-sitter",
    )
    max_file_bytes: int = Field(
        default=500_000,
        description="Límite máximo de tamaño por archivo de código en bytes",
    )

    # Parámetros de Enriquecimiento LLM (llm_enrich.py)
    llm_base_url: str = Field(default="https://api.deepseek.com/v1", description="URL base de la API de LLM")
    llm_api_key: str = Field(default="test_key", description="Clave API del proveedor LLM")
    batch_size: int = Field(default=20, description="Tamaño del lote para llamadas al LLM")
    model_flash: str = Field(default="deepseek-chat", description="Modelo tier flash para anotación y enlaces")
    model_pro: str = Field(default="deepseek-coder", description="Modelo tier pro para inducción de ontología")
    link_confidence_threshold: float = Field(
        default=0.75,
        description="Umbral de confianza para persistir enlaces; bajo este valor va a la cola de curación",
    )


def build_settings() -> Settings:
    return Settings()


settings: Settings = build_settings()
