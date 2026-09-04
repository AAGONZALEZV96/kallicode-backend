"""CodeMapping - configuracion central del pipeline de ingesta.

Todo parametro sale de variables de entorno: el mismo paquete corre
en cloud y en el despliegue soberano on-premise sin cambios de codigo.
"""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    client_id: str = os.getenv("CM_CLIENT_ID", "cliente_demo")
    repo_url: str = os.getenv("CM_REPO_URL", "")
    workdir: str = os.getenv("CM_WORKDIR", "/var/codemapping/repos")

    # Neo4j (grafo de conocimiento)
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    # Qdrant (capa vectorial)
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")

    # LLM: endpoint OpenAI-compatible (DeepSeek-V4 servido con vLLM/SGLang)
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "sovereign")
    model_pro: str = os.getenv("MODEL_PRO", "deepseek-v4")           # modo thinking
    model_flash: str = os.getenv("MODEL_FLASH", "deepseek-v4-lite")  # modo rapido

    # Embeddings (BGE-M3: open source, multilingue, 1024 dims)
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    embedding_dim: int = 1024

    # Base de datos del cliente (extraccion de esquema)
    client_db_url: str = os.getenv("CLIENT_DB_URL", "")

    # Ingesta
    include_ext: tuple = (".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".sql")
    exclude_dirs: tuple = ("node_modules", "dist", "build", ".git",
                           "vendor", "__pycache__", "coverage")
    max_file_bytes: int = 1_500_000   # descarta generados/minificados
    batch_size: int = 64
    link_confidence_threshold: float = 0.75  # bajo esto -> cola de curacion


settings = Settings()
