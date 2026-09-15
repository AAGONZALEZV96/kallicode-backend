"""Configuración central de Kallicode (prefijo KC_).

Única fuente de configuración para la API y los workers (D05–D16 de la
Especificación de Entorno). El mismo binario corre en local/dev/staging/prod
cambiando solo variables de entorno.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KC_", env_file=".env", extra="ignore")

    # --- entorno ---
    env: str = "local"  # local | dev | staging | prod
    log_level: str = "INFO"
    log_formato: str = "json"  # json | texto

    # --- base de datos (D05/D06) ---
    # database_url: str = "postgresql+asyncpg://postgres:dev@localhost:5432/kallicode"
    # cambio el usuario por defecto de postgres a los roles correctos
    database_url: str = "postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode"
    database_url_admin: str = "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"

    # --- broker / caché (D02) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- blob storage ---
    blob_account_url: str = "http://localhost:10000/devstoreaccount1"
    blob_container_evidencia: str = "evidencia"

    # --- seguridad / JWT (RS256; en Azure las claves viven en Key Vault) ---
    jwt_private_key: str = ""  # PEM; vacío => se genera par efímero (solo local/tests)
    jwt_public_key: str = ""
    access_token_ttl_min: int = 30
    refresh_token_ttl_dias: int = 30
    svc_token_ttl_min: int = 5  # tokens de servicio de la API interna

    # --- validación comercial (D16) ---
    rate_limit_usuario: int = 120  # req/min (RL-1)
    rate_limit_tenant: int = 1200
    rate_limit_typing: int = 30  # RL-2 dedup/impact preview
    rate_limit_webhook: int = 120  # RL-3 por conexión
    max_iteraciones_gate: int = 5
    dedup_umbral: float = 0.70
    max_adjunto_bytes: int = 26_214_400  # 25 MB
    max_tickets_hora_usuario: int = 30

    # --- LLM: tres categorías (decisión julio-2026) ---
    # flash  = razonamiento simple      -> DeepSeek V4 Flash
    # pro    = razonamiento complejo    -> DeepSeek V4 Pro
    # fable  = muy complejo/orquestación-> Claude Fable 5
    llm_flash_base_url: str = "http://localhost:9001/v1"
    llm_flash_model: str = "deepseek-v4-flash"
    llm_flash_api_key: str = "dev"
    llm_pro_base_url: str = "http://localhost:9001/v1"
    llm_pro_model: str = "deepseek-v4-pro"
    llm_pro_api_key: str = "dev"
    llm_fable_base_url: str = "https://api.anthropic.com/v1"
    llm_fable_model: str = "claude-fable-5"
    llm_fable_api_key: str = ""
    llm_timeout_s: int = 120
    llm_max_reintentos_esquema: int = 2  # fallos de esquema antes de escalar de tier
    llm_umbral_confianza: float = 0.75  # confianza mínima antes de escalar

    # --- embeddings (D15) ---
    embeddings_url: str = "http://localhost:8080"
    embeddings_dim: int = 1024

    # --- correo SMTP genérico (D11) ---
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = False
    smtp_from: str = "no-reply@kallicode.dev"

    # --- polling (D12) ---
    poll_notificaciones_s: int = 30
    poll_tablero_s: int = 60

    # --- CORS ---
    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
