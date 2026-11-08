"""Validación de configuración de base de datos con claves JWT válidas."""

import pytest
from kallicode_core.config import Settings
from kallicode_core.seguridad import _generar_par_efimero
from pydantic import ValidationError


@pytest.fixture(scope="module")
def claves_jwt() -> dict[str, str]:
    privada, publica = _generar_par_efimero()
    privada_svc, publica_svc = _generar_par_efimero()

    return {
        "jwt_private_key": privada,
        "jwt_public_key": publica,
        "jwt_private_key_svc": privada_svc,
        "jwt_public_key_svc": publica_svc,
    }


def test_database_url_admin_is_required_outside_local(claves_jwt) -> None:
    with pytest.raises(
        ValidationError,
        match="KC_DATABASE_URL_ADMIN",
    ):
        Settings(
            _env_file=None,
            **claves_jwt,
            env="prod",
            database_url="postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode",
            database_url_admin=None,
        )


def test_database_url_admin_uses_local_default(claves_jwt) -> None:
    settings = Settings(
        _env_file=None,
        **claves_jwt,
        env="local",
        database_url="postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode",
        database_url_admin=None,
    )

    assert settings.database_url_admin == (
        "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
    )


def test_database_url_admin_is_accepted_outside_local(claves_jwt) -> None:
    settings = Settings(
        _env_file=None,
        **claves_jwt,
        env="prod",
        database_url="postgresql+asyncpg://kallicode_app:password@db:5432/kallicode",
        database_url_admin="postgresql+asyncpg://kallicode_admin:password@db:5432/kallicode",
    )

    assert settings.database_url_admin is not None


def test_application_database_cannot_use_postgres(claves_jwt) -> None:
    with pytest.raises(
        ValidationError,
        match="kallicode_app",
    ):
        Settings(
            _env_file=None,
            **claves_jwt,
            env="local",
            database_url="postgresql+asyncpg://postgres:dev@localhost:5432/kallicode",
            database_url_admin=(
                "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
            ),
        )


def test_application_database_cannot_use_admin_role(claves_jwt) -> None:
    with pytest.raises(
        ValidationError,
        match="kallicode_app",
    ):
        Settings(
            _env_file=None,
            **claves_jwt,
            env="local",
            database_url="postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode",
            database_url_admin=(
                "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
            ),
        )


def test_admin_database_is_configured_for_a_distinct_role(claves_jwt) -> None:
    settings = Settings(
        _env_file=None,
        **claves_jwt,
        env="prod",
        database_url="postgresql+asyncpg://kallicode_app:password@db:5432/kallicode",
        database_url_admin="postgresql+asyncpg://kallicode_admin:password@db:5432/kallicode",
    )

    assert settings.database_url != settings.database_url_admin
