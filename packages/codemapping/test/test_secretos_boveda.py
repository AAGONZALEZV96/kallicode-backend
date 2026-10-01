"""Pruebas para la resolución segura de secretos y enmascaramiento (KC-CM-01-4)."""

import pytest
from kallicode_codemapping.secretos import (
    CredencialesConexion,
    ResolvedorBovedaCliente,
    enmascarar_url,
)
from pydantic import SecretStr


def test_enmascarar_url_con_credenciales() -> None:
    url_con_token = "https://ghp_tokenSuperSecreto12345@github.com/org/repo.git"
    url_con_user_pass = "postgresql://admin:Password123@db.empresa.com:5432/prod"
    # pragma: allowlist secret

    enmascarada_git = enmascarar_url(url_con_token)
    enmascarada_db = enmascarar_url(url_con_user_pass)

    assert "ghp_tokenSuperSecreto12345" not in enmascarada_git
    assert "****" in enmascarada_git
    assert "Password123" not in enmascarada_db
    assert ":****@" in enmascarada_db


def test_credenciales_model_dump_enmascarado() -> None:
    creds = CredencialesConexion(
        tenant_id="org_alpha",
        connection_id="conn_1",
        repo_url_segura=SecretStr("https://token@github.com/org/repo.git"),
    )

    dump_str = str(creds)
    assert "token" not in dump_str
    assert "**********" in repr(creds.repo_url_segura)


@pytest.mark.asyncio
async def test_resolvedor_boveda_fallback() -> None:
    resolvedor = ResolvedorBovedaCliente()
    resultado = await resolvedor.resolver_conexion("org_test", "conn_test")

    assert resultado.tenant_id == "org_test"
    assert resultado.repo_url_segura.get_secret_value().startswith("https://")
