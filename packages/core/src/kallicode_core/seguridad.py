"""Autenticación y autorización (§3.2 del diseño de endpoints).

- JWT RS256 con claims: sub (user_id), org (tenant), rol, jti, typ (user|svc).
- Refresh tokens rotativos de un solo uso, persistidos como hash SHA-256.
- Contraseñas con Argon2id; política: mín. 12 caracteres, mayúscula,
  minúscula y dígito.
- Roles y qué puede firmar cada uno:
    owner/admin/architect/approver -> gates 1 y 2 · architect -> gate 3.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import get_settings
from .errors import AppError, no_autenticado, permiso_denegado

ROLES = ("owner", "admin", "architect", "approver", "member", "viewer")
ROLES_FIRMA_G12 = ("owner", "admin", "architect", "approver")
ROLES_FIRMA_G3 = ("architect",)

_ph = PasswordHasher()
_par_efimero: tuple[str, str] | None = None  # solo local/tests sin claves configuradas


def _claves() -> tuple[str, str]:
    """Par (privada, pública) PEM. Sin configuración genera uno efímero (solo local)."""
    global _par_efimero
    s = get_settings()
    if s.jwt_private_key and s.jwt_public_key:
        return s.jwt_private_key, s.jwt_public_key
    if s.env not in ("local", "test"):
        raise RuntimeError("KC_JWT_PRIVATE_KEY/PUBLIC_KEY son obligatorias fuera de local")
    if _par_efimero is None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv = k.private_bytes(serialization.Encoding.PEM,
                               serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption()).decode()
        pub = k.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        _par_efimero = (priv, pub)
    return _par_efimero


# --------------------------------------------------------------------------
# Contraseñas
# --------------------------------------------------------------------------
def hashear_password(password: str) -> str:
    return _ph.hash(password)


def verificar_password(password: str, hash_: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except VerifyMismatchError:
        return False


def validar_politica_password(password: str) -> None:
    """Lanza AUTH_PASSWORD_DEBIL (422) si la contraseña incumple la política."""
    fallos = []
    if len(password) < 12:
        fallos.append("mínimo 12 caracteres")
    if not any(c.isupper() for c in password):
        fallos.append("al menos una mayúscula")
    if not any(c.islower() for c in password):
        fallos.append("al menos una minúscula")
    if not any(c.isdigit() for c in password):
        fallos.append("al menos un dígito")
    if fallos:
        raise AppError("AUTH_PASSWORD_DEBIL", 422,
                       "La contraseña no cumple la política de seguridad.",
                       {"requisitos": fallos})


# --------------------------------------------------------------------------
# JWT de usuario y de servicio
# --------------------------------------------------------------------------
def emitir_access_token(user_id: str, tenant_id: str, rol: str,
                        restringido: bool = False) -> str:
    s = get_settings()
    priv, _ = _claves()
    ahora = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": user_id, "org": tenant_id, "rol": rol, "typ": "user",
        "restringido": restringido, "jti": uuid.uuid4().hex,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=s.access_token_ttl_min)).timestamp()),
    }, priv, algorithm="RS256")


def emitir_svc_token(servicio: str, tenant_id: str, linea: int | None = None) -> str:
    """Token de la API interna: typ=svc, claims svc y line (§17)."""
    s = get_settings()
    priv, _ = _claves()
    ahora = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": f"svc:{servicio}", "org": tenant_id, "svc": servicio,
        "line": linea, "typ": "svc", "jti": uuid.uuid4().hex,
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=s.svc_token_ttl_min)).timestamp()),
    }, priv, algorithm="RS256")


def verificar_token(token: str, tipo_esperado: str = "user") -> dict:
    """Valida firma y expiración; devuelve los claims o lanza NO_AUTENTICADO."""
    _, pub = _claves()
    try:
        claims = jwt.decode(token, pub, algorithms=["RS256"])
    except jwt.ExpiredSignatureError:
        raise no_autenticado("El token ha expirado.")
    except jwt.InvalidTokenError:
        raise no_autenticado()
    if claims.get("typ") != tipo_esperado:
        raise no_autenticado("Tipo de token incorrecto para esta API.")
    return claims


# --------------------------------------------------------------------------
# Tokens opacos (refresh, invitación, reset): prefijo + aleatorio; en BD solo hash
# --------------------------------------------------------------------------
def generar_token_opaco(prefijo: str) -> tuple[str, str]:
    """Devuelve (token_en_claro, hash_sha256). El claro solo viaja una vez."""
    claro = f"{prefijo}_{secrets.token_urlsafe(32)}"
    return claro, hashlib.sha256(claro.encode()).hexdigest()


def hash_token(claro: str) -> str:
    return hashlib.sha256(claro.encode()).hexdigest()


# --------------------------------------------------------------------------
# Autorización
# --------------------------------------------------------------------------
def exigir_rol(rol_actual: str, permitidos: tuple[str, ...]) -> None:
    if rol_actual not in permitidos:
        raise permiso_denegado()


def puede_firmar(rol: str, gate: int) -> bool:
    return rol in (ROLES_FIRMA_G3 if gate == 3 else ROLES_FIRMA_G12)
