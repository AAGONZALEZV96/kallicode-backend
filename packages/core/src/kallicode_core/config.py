"""Configuración central de Kallicode."""

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KC_",
        env_file=".env",
        extra="ignore",
    )

    # Entorno
    env: str = "local"
    log_level: str = "INFO"
    log_formato: str = "json"

    # Base de datos
    database_url: str = "postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode"
    database_url_admin: str | None = None
    # KC-DB-02-2: barridos cross-tenant de los workers (solo tablas de cola).
    database_url_worker: str | None = None

    # Broker / caché
    redis_url: str = "redis://localhost:6379/0"
    redis_password: str | None = None

    # Blob storage
    blob_account_url: str = "http://localhost:10000/devstoreaccount1"
    blob_container_evidencia: str = "evidencia"

    # Seguridad / JWT
    jwt_private_key: str = ""
    jwt_public_key: str = ""
    jwt_private_key_svc: str = ""
    jwt_public_key_svc: str = ""
    access_token_ttl_min: int = 30
    refresh_token_ttl_dias: int = 30
    svc_token_ttl_min: int = 5

    # orquestador
    internal_api_base_url: str = "http://localhost:8000/internal/v1"
    orquestador_servicio: str = "orquestador"

    # Validación comercial
    rate_limit_usuario: int = 120
    rate_limit_tenant: int = 1200
    rate_limit_typing: int = 30
    rate_limit_webhook: int = 120
    max_iteraciones_gate: int = 5
    dedup_umbral: float = 0.70
    max_adjunto_bytes: int = 26_214_400
    max_tickets_hora_usuario: int = 30

    # LLM
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
    llm_max_reintentos_esquema: int = 2
    llm_umbral_confianza: float = 0.75

    # KC-BE-03-3: Activar cuando el check de tier (migracion 009) este aplicado en el entorno
    persistir_tier_fable: bool = False

    # Embeddings
    embeddings_url: str = "http://localhost:8080"
    embeddings_dim: int = 1024

    # Correo SMTP
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = False
    smtp_from: str = "no-reply@kallicode.dev"

    # Polling
    poll_notificaciones_s: int = 30
    poll_tablero_s: int = 60

    # CORS
    cors_origins: str = "http://localhost:5173"
    # KC-BE-01-6: Hosts permitidos para CORS. Separados por coma. Ej: http://localhost:5173,http://localhost:5174
    allowed_hosts: str = "localhost,127.0.0.1,test,testserver"
    # KC-BE-01-6: Limite global de cuerpo (5mb)
    max_body_bytes: int = 5_242_880

    @model_validator(mode="after")
    def validar_base_de_datos(self) -> "Settings":
        app_username = urlparse(self.database_url).username

        if app_username in {"postgres", "kallicode_admin"}:
            raise ValueError(
                "KC_DATABASE_URL debe usar el rol kallicode_app; "
                "no puede usar un usuario superusuario o BYPASSRLS"
            )

        if self.env != "local" and not self.database_url_admin:
            raise ValueError(
                "KC_DATABASE_URL_ADMIN es obligatoria cuando KC_ENV "
                "no es local y debe usar un rol con BYPASSRLS"
            )

        if self.env == "local" and not self.database_url_admin:
            self.database_url_admin = (
                "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
            )

        if self.database_url_worker:
            worker_username = urlparse(self.database_url_worker).username
            if worker_username in {"postgres", "kallicode_admin"}:
                raise ValueError(
                    "KC_DATABASE_URL_WORKER debe usar el rol kallicode_worker; "
                    "no puede usar un usuario superusuario o BYPASSRLS"
                )

        if self.env == "local" and not self.database_url_worker:
            self.database_url_worker = (
                "postgresql+asyncpg://kallicode_worker:dev@localhost:5432/kallicode"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
