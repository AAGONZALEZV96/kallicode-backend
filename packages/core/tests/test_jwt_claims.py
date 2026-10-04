"""BE-F12 — validación estricta de claims JWT por superficie."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from kallicode_core.errors import AppError
from kallicode_core.seguridad import (
    AUD_INTERNAL,
    AUD_PORTAL,
    ISS_INTERNAL,
    ISS_PORTAL,
    verificar_token,
)


def _par_rsa() -> tuple[str, str]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = (
        k.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv, pub


def _claims_base(**overrides) -> dict:
    ahora = datetime.now(UTC)
    base = {
        "sub": "u1",
        "org": "tenant-a",
        "rol": "member",
        "typ": "user",
        "jti": uuid.uuid4().hex,
        "iss": ISS_PORTAL,
        "aud": AUD_PORTAL,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=15)).timestamp()),
    }
    base.update(overrides)
    return base


def _firmar(claims: dict, priv: str) -> str:
    return jwt.encode(claims, priv, algorithm="RS256")


@pytest.fixture
def claves_portal(monkeypatch):
    priv, pub = _par_rsa()
    monkeypatch.setenv("KC_JWT_PRIVATE_KEY", priv)
    monkeypatch.setenv("KC_JWT_PUBLIC_KEY", pub)
    from kallicode_core.config import get_settings

    get_settings.cache_clear()
    return priv, pub


@pytest.fixture
def claves_otra(monkeypatch):
    """Un segundo par RSA, para simular un token firmado con clave ajena."""
    return _par_rsa()


class TestClaimsObligatorios:
    @pytest.mark.parametrize(
        "claim_faltante",
        ["exp", "iat", "sub", "typ", "org", "jti"],
    )
    def test_token_sin_claim_obligatorio_es_rechazado(self, claves_portal, claim_faltante):
        priv, _ = claves_portal
        claims = _claims_base()
        del claims[claim_faltante]
        token = _firmar(claims, priv)
        with pytest.raises(AppError) as exc:
            verificar_token(token, "user")
        assert exc.value.http == 401

    def test_token_sin_rol_no_produce_500(self, claves_portal):
        """BE-F12: ausencia de 'rol' debía dar KeyError->500; ahora 401 explícito."""
        priv, _ = claves_portal
        claims = _claims_base()
        del claims["rol"]
        token = _firmar(claims, priv)
        # verificar_token no exige 'rol' (no está en _CLAIMS_REQUERIDOS de JWT),
        # pero el acceso tipado en deps.py sí debe rechazarlo como 401:
        from kallicode_core.seguridad import validar_claims_usuario

        raw = verificar_token(token, "user")
        with pytest.raises(AppError) as exc:
            validar_claims_usuario(raw)
        assert exc.value.http == 401


class TestSeparacionPorSuperficie:
    def test_typ_cruzado_es_rechazado(self, claves_portal):
        """Token typ=user no debe servir donde se exige typ=svc, ni viceversa."""
        priv, _ = claves_portal
        token_user = _firmar(_claims_base(typ="user"), priv)
        with pytest.raises(AppError) as exc:
            verificar_token(token_user, "svc")
        assert exc.value.http == 401

    def test_audience_ajena_es_rechazada(self, claves_portal):
        priv, _ = claves_portal
        claims = _claims_base(aud="otra-audiencia-cualquiera")
        token = _firmar(claims, priv)
        with pytest.raises(AppError) as exc:
            verificar_token(token, "user")
        assert exc.value.http == 401

    def test_issuer_ajeno_es_rechazado(self, claves_portal):
        priv, _ = claves_portal
        claims = _claims_base(iss="emisor-no-reconocido")
        token = _firmar(claims, priv)
        with pytest.raises(AppError) as exc:
            verificar_token(token, "user")
        assert exc.value.http == 401

    def test_token_interno_no_sirve_en_superficie_publica(self, claves_portal):
        """Aunque alguien forzara typ=user con iss/aud de kc-internal, debe
        rechazarse: la superficie pública exige iss=kc-portal/aud=kc-portal-api."""
        priv, _ = claves_portal
        claims = _claims_base(iss=ISS_INTERNAL, aud=AUD_INTERNAL)
        token = _firmar(claims, priv)
        with pytest.raises(AppError) as exc:
            verificar_token(token, "user")
        assert exc.value.http == 401


class TestClaveFirmante:
    def test_token_firmado_con_otra_clave_es_rechazado(self, claves_portal, claves_otra):
        priv_ajena, _ = claves_otra
        token = _firmar(_claims_base(), priv_ajena)
        with pytest.raises(AppError) as exc:
            verificar_token(token, "user")
        assert exc.value.http == 401

    def test_clave_svc_distinta_de_clave_user(self, monkeypatch):
        """BE-F12: user y svc ya no comparten clave privada."""
        from kallicode_core.seguridad import _claves

        priv_user, pub_user = _claves("user")
        priv_svc, pub_svc = _claves("svc")
        assert priv_user != priv_svc
        assert pub_user != pub_svc
