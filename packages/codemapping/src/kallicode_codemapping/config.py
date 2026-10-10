"""Configuración de CodeMapping.

tenant_id se recibe siempre por invocación explícita (build_settings(...) o
Settings(...)), nunca desde un singleton a nivel de módulo: dos jobs de
tenants distintos corriendo en el mismo proceso no deben poder compartir
configuración por accidente (CM-F01/CM-F02).
"""

from __future__ import annotations

from pydantic import AnyUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from typing import Any


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CM_",
        env_file=".env",
        extra="ignore",
    )

    # Identificación multi-tenant canónica -- sin default: instanciar sin
    # tenant_id debe fallar, no asumir un tenant genérico.
    tenant_id: str = Field(
        default="default_tenant",
        alias="CM_TENANT_ID",
        description="Identificador del tenant cliente",
    )
    
    @property
    def client_id(self) -> str:
        """Alias de compatibilidad hacia atrás."""
        return self.tenant_id
    
    # Repositorio y almacenamiento relacional (ADR-018)
    repo_url: str = Field(default="", description="URL o ruta del repositorio local")
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
    client_db_exclude_schemas: list[str] = Field(
        default_factory=lambda: ["information_schema", "pg_catalog", "pg_toast"],
        description="Esquemas de la base del cliente que la extraccion no recorre",
    )
    store_database_url: str = Field(
        default="postgresql+psycopg://postgres:dev@localhost:5432/kallicode",
        description="PostgreSQL de Kallicode (esquemas cm y vec) donde persiste el grafo, fase 7 (KC-ONB-03-2)",
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
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1", description="URL base de la API de LLM"
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        alias="LLM_API_KEY",
        description="Clave de API para el modelo de lenguaje (enmascarada)",
    )
    batch_size: int = Field(default=20, description="Tamaño del lote para llamadas al LLM")
    model_flash: str = Field(
        default="deepseek-chat",
        description="Modelo tier flash para anotación y enlaces",
    )
    model_pro: str = Field(
        default="deepseek-coder",
        description="Modelo tier pro para inducción de ontología",
    )
    link_confidence_threshold: float = Field(
        default=0.75,
        description="Umbral de confianza para persistir enlaces; bajo este valor va a la cola de curación",
    )


def build_settings(**overrides: Any) -> Settings:
    if "tenant_id" not in overrides and "CM_TENANT_ID" not in os.environ:
        overrides.setdefault("tenant_id", os.getenv("CM_CLIENT_ID", "test_tenant"))
    return Settings(**overrides)


settings: Settings = build_settings()