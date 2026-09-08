"""CodeMapping - Configuración central validada mediante Pydantic Settings.

tenant_id se recibe siempre por invocación explícita, nunca desde
variables de entorno del proceso. Esto evita que dos jobs de tenants
distintos, corriendo en el mismo proceso, compartan configuración.
"""

from typing import Any

from pydantic import AnyUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    tenant_id: str = Field(...)
    repo_url: str = Field(...)
    workdir: str = Field(default="/var/codemapping/repos")

    database_url: AnyUrl = Field(...)
    embeddings_url: AnyUrl = Field(...)

    llm_base_url: str = Field(...)
    llm_api_key: str = Field(...)
    model_pro: str = Field(default="deepseek-v4")
    model_flash: str = Field(default="deepseek-v4-lite")

    embedding_model: str = Field(default="BAAI/bge-m3")
    embedding_dim: int = 1024

    include_ext: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".sql")
    exclude_dirs: tuple[str, ...] = (
        "node_modules", "dist", "build", ".git", "vendor", "__pycache__", "coverage",
    )
    max_file_bytes: int = 1_500_000
    batch_size: int = 64
    link_confidence_threshold: float = 0.75


def build_settings(**overrides: Any) -> Settings:
    """Fábrica explícita: cada llamada crea una instancia propia,
    sin estado compartido entre tenants."""
    return Settings(**overrides)
