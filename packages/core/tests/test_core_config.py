import pytest
from kallicode_core.config import Settings
from pydantic import ValidationError


def test_database_url_admin_is_required_outside_local() -> None:
    with pytest.raises(
        ValidationError,
        match="KC_DATABASE_URL_ADMIN",
    ):
        Settings(
            env="prod",
            database_url=("postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode"),
            database_url_admin=None,
        )


def test_database_url_admin_uses_local_default() -> None:
    settings = Settings(
        env="local",
        database_url=("postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode"),
        database_url_admin=None,
    )

    assert settings.database_url_admin == (
        "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
    )


def test_database_url_admin_is_accepted_outside_local() -> None:
    settings = Settings(
        env="prod",
        database_url=("postgresql+asyncpg://kallicode_app:password@db:5432/kallicode"),
        database_url_admin=("postgresql+asyncpg://kallicode_admin:password@db:5432/kallicode"),
    )

    assert settings.database_url_admin is not None


def test_application_database_cannot_use_postgres() -> None:
    with pytest.raises(
        ValidationError,
        match="kallicode_app",
    ):
        Settings(
            env="local",
            database_url=("postgresql+asyncpg://postgres:dev@localhost:5432/kallicode"),
            database_url_admin=(
                "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
            ),
        )


def test_application_database_cannot_use_admin_role() -> None:
    with pytest.raises(
        ValidationError,
        match="kallicode_app",
    ):
        Settings(
            env="local",
            database_url=("postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"),
            database_url_admin=(
                "postgresql+asyncpg://kallicode_admin:dev@localhost:5432/kallicode"
            ),
        )


def test_admin_database_is_configured_for_a_distinct_role() -> None:
    settings = Settings(
        env="prod",
        database_url=("postgresql+asyncpg://kallicode_app:password@db:5432/kallicode"),
        database_url_admin=("postgresql+asyncpg://kallicode_admin:password@db:5432/kallicode"),
    )

    assert settings.database_url != settings.database_url_admin
