"""CodeMapping - Configuración central validada mediante Pydantic Settings."""

import os
from typing import Self
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CM_",
        env_file=".env",
        extra="ignore",
    )

    client_id: str = Field(default="", alias="CM_CLIENT_ID")
    repo_url: str = Field(default="", alias="CM_REPO_URL")
    workdir: str = Field(default="/var/codemapping/repos", alias="CM_WORKDIR")

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="", alias="NEO4J_PASSWORD")

    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")

    llm_base_url: str = Field(default="http://localhost:8000/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    model_pro: str = Field(default="deepseek-v4", alias="MODEL_PRO")
    model_flash: str = Field(default="deepseek-v4-lite", alias="MODEL_FLASH")

    embedding_model: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL")
    embedding_dim: int = 1024

    client_db_url: str = Field(default="", alias="CLIENT_DB_URL")

    include_ext: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".sql")
    exclude_dirs: tuple[str, ...] = (
        "node_modules", "dist", "build", ".git", "vendor", "__pycache__", "coverage",
    )
    max_file_bytes: int = 1_500_000
    batch_size: int = 64
    link_confidence_threshold: float = 0.75

    @model_validator(mode="after")
    def validate_mandatory_envs(self) -> Self:
        c_id = self.client_id or os.getenv("CM_CLIENT_ID")
        r_url = self.repo_url or os.getenv("CM_REPO_URL")
        if not c_id or not r_url:
            raise ValueError("Las variables CM_CLIENT_ID y CM_REPO_URL son obligatorias.")
        self.client_id = c_id
        self.repo_url = r_url
        return self


settings = Settings()