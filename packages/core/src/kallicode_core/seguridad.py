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
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import zxcvbn
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from pydantic import BaseModel, ValidationError

from .config import get_settings
from .errors import AppError, no_autenticado, permiso_denegado

ROLES = ("owner", "admin", "architect", "approver", "member", "viewer")
ROLES_FIRMA_G12 = ("owner", "admin", "architect", "approver")
ROLES_FIRMA_G3 = ("architect",)

ISS_PORTAL = "kc-portal"
AUD_PORTAL = "kc-portal-api"
ISS_INTERNAL = "kc-internal"
AUD_INTERNAL = "kc-internal-api"

_CLAIMS_REQUERIDOS = ["exp", "iat", "sub", "typ", "org", "jti"]
_CONFIG_POR_TIPO = {
    "user": {"iss": ISS_PORTAL, "aud": AUD_PORTAL},
    "svc": {"iss": ISS_INTERNAL, "aud": AUD_INTERNAL},
}

_ph = PasswordHasher()
_par_efimero: tuple[str, str] | None = None
_par_efimero_svc: tuple[str, str] | None = None


def _generar_par_efimero() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

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


def _normalizar_pem(clave: str) -> str:
    """Normaliza y limpia una clave PEM proveniente de variables de entorno (Vercel, .env)."""
    if not clave:
        return clave
    c = clave.strip()
    c = c.replace(r"\\n", "\n").replace(r"\n", "\n")
    if (c.startswith('"') and c.endswith('"')) or (c.startswith("'") and c.endswith("'")):
        c = c[1:-1].strip()
    m = re.search(r"(-----BEGIN [A-Z0-9 ]+-----[A-Za-z0-9+/=\s\r\n]+-----END [A-Z0-9 ]+-----)", c)
    if m:
        c = m.group(1).strip()
    return c


def _claves(tipo: str = "user") -> tuple[str, str]:
    """Par (privada, pública) PEM para 'user' o 'svc'. Sin configuración
    genera un par efímero por tipo (solo local/test) — nunca comparte clave
    entre superficies."""
    global _par_efimero, _par_efimero_svc
    s = get_settings()

    if tipo == "svc":
        if s.jwt_private_key_svc and s.jwt_public_key_svc:
            return _normalizar_pem(s.jwt_private_key_svc), _normalizar_pem(s.jwt_public_key_svc)
        if s.env not in ("local", "test"):
            raise RuntimeError(
                "KC_JWT_PRIVATE_KEY_SVC/PUBLIC_KEY_SVC son obligatorias fuera de local"
            )
        if _par_efimero_svc is None:
            _par_efimero_svc = _generar_par_efimero()
        return _par_efimero_svc

    if s.jwt_private_key and s.jwt_public_key:
        return _normalizar_pem(s.jwt_private_key), _normalizar_pem(s.jwt_public_key)
    if s.env not in ("local", "test"):
        raise RuntimeError("KC_JWT_PRIVATE_KEY/PUBLIC_KEY son obligatorias fuera de local")
    if _par_efimero is None:
        _par_efimero = _generar_par_efimero()
    return _par_efimero


# --------------------------------------------------------------------------
# Contraseñas
# --------------------------------------------------------------------------


def hashear_password(password: str) -> str:
    return _ph.hash(password)


def verificar_password(password: str, hash_: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except (VerifyMismatchError, InvalidHashError):
        # InvalidHashError atrapa hashes antiguos o corruptos sin romper el servidor
        return False


def requiere_rehash(hash_: str) -> bool:
    """Verifica si el hash requiere actualizarse a los parámetros Argon2id actuales."""
    return _ph.check_needs_rehash(hash_)


def validar_politica_password(
    password: str, email: str | None = None, org_nombre: str | None = None
) -> None:
    """Lanza AUTH_PASSWORD_DEBIL (422) si la contraseña incumple la política."""
    fallos = []

    # 1. Validación de reglas estáticas (Longitud y complejidad básica)
    if len(password) < 12:
        fallos.append("mínimo 12 caracteres")
    if len(password) > 128:
        fallos.append("máximo 128 caracteres")
    if not any(c.isupper() for c in password):
        fallos.append("al menos una mayúscula")
    if not any(c.islower() for c in password):
        fallos.append("al menos una minúscula")
    if not any(c.isdigit() for c in password):
        fallos.append("al menos un dígito")

    if fallos:
        raise AppError(
            "AUTH_PASSWORD_DEBIL",
            422,
            "La contraseña no cumple la política de seguridad básica.",
            {"requisitos": fallos},
        )

    # 2. Análisis de Entropía Avanzado (zxcvbn)
    # Pasamos datos del usuario para que zxcvbn penalice si la clave se basa en ellos
    datos_usuario = [d for d in (email, org_nombre, "kallicode") if d]

    resultado = zxcvbn.zxcvbn(password, user_inputs=datos_usuario)

    # zxcvbn devuelve un score de 0 a 4.
    # < 3 significa que puede ser crackeada fácilmente por ataques de diccionario.
    if resultado["score"] < 3:
        sugerencia = (
            resultado["feedback"]["warning"] or "La contraseña es demasiado común o predecible."
        )
        raise AppError(
            "AUTH_PASSWORD_DEBIL",
            422,
            "La contraseña es demasiado débil según el análisis de entropía.",
            {"sugerencia": sugerencia},
        )


# --------------------------------------------------------------------------
# JWT de usuario y de servicio
# --------------------------------------------------------------------------
def emitir_access_token(user_id: str, tenant_id: str, rol: str, restringido: bool = False) -> str:
    s = get_settings()
    priv, _ = _claves("user")
    ahora = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "org": tenant_id,
            "rol": rol,
            "typ": "user",
            "restringido": restringido,
            "jti": uuid.uuid4().hex,
            "iss": ISS_PORTAL,
            "aud": AUD_PORTAL,
            "iat": int(ahora.timestamp()),
            "exp": int((ahora + timedelta(minutes=s.access_token_ttl_min)).timestamp()),
        },
        priv,
        algorithm="RS256",
    )


def emitir_svc_token(servicio: str, tenant_id: str, linea: int | None = None) -> str:
    """Token de la API interna: typ=svc, claims svc y line (§17)."""
    s = get_settings()
    priv, _ = _claves("svc")
    ahora = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": f"svc:{servicio}",
            "org": tenant_id,
            "svc": servicio,
            "line": linea,
            "typ": "svc",
            "jti": uuid.uuid4().hex,
            "iss": ISS_INTERNAL,
            "aud": AUD_INTERNAL,
            "iat": int(ahora.timestamp()),
            "exp": int((ahora + timedelta(minutes=s.svc_token_ttl_min)).timestamp()),
        },
        priv,
        algorithm="RS256",
    )


def verificar_token(token: str, tipo_esperado: str = "user") -> dict:
    """Valida firma, expiración, issuer, audience y claims obligatorios."""
    config = _CONFIG_POR_TIPO[tipo_esperado]
    _, pub = _claves(tipo_esperado)
    try:
        claims = jwt.decode(
            token,
            pub,
            algorithms=["RS256"],
            issuer=config["iss"],
            audience=config["aud"],
            leeway=30,
            options={"require": _CLAIMS_REQUERIDOS},
        )
    except jwt.ExpiredSignatureError:
        raise no_autenticado("El token ha expirado.")
    except jwt.InvalidTokenError:
        raise no_autenticado()
    if claims.get("typ") != tipo_esperado:
        raise no_autenticado("Tipo de token incorrecto para esta API.")
    return claims


class ClaimsUsuario(BaseModel):
    sub: str
    org: str
    rol: str
    jti: str
    typ: str
    iat: int
    restringido: bool = False


class ClaimsServicio(BaseModel):
    sub: str
    org: str
    svc: str
    jti: str
    typ: str
    iat: int
    line: int | None = None


def validar_claims_usuario(raw: dict) -> ClaimsUsuario:
    try:
        return ClaimsUsuario.model_validate(raw)
    except ValidationError:
        raise no_autenticado("Claims del token incompletos o inválidos.")


def validar_claims_servicio(raw: dict) -> ClaimsServicio:
    try:
        return ClaimsServicio.model_validate(raw)
    except ValidationError:
        raise no_autenticado("Claims del token incompletos o inválidos.")


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


# --------------------------------------------------------------------------
# Revocacion (denylist en redis)
# --------------------------------------------------------------------------
_TTL_ESTADO_S = 24 * 3600  # 24h, suficiente para que expire el refresh token y no se pueda usar más


def _redis():
    from . import comercial

    return comercial.redis_cliente()


def _k(tenant_id: str, *partes: str) -> str:
    from .redis_keys import clave_tenant

    return clave_tenant(tenant_id, "revoc", *partes)


def _txt(v) -> str | None:
    return v.decode() if isinstance(v, bytes) else v


async def revocar_jti(tenant_id: str, jti: str, ttl_s: int) -> None:
    await _redis().set(_k(tenant_id, "jti", jti), "1", ex=max(int(ttl_s), 1))


async def invalidar_usuario(tenant_id: str, user_id: str) -> None:
    await _redis().set(_k(tenant_id, "sello", user_id), str(int(time.time())), ex=_TTL_ESTADO_S)


async def fijar_rol_vigente(tenant_id: str, user_id: str, rol: str) -> None:
    await _redis().set(_k(tenant_id, "rol", user_id), rol, ex=_TTL_ESTADO_S)


async def estado_revocacion(tenant_id: str, user_id: str, jti: str):
    """Un solo MGET: (jti_revocado, sello, rol_vigente)."""
    r = await _redis().mget(
        _k(tenant_id, "jti", jti),
        _k(tenant_id, "sello", user_id),
        _k(tenant_id, "rol", user_id),
    )
    sello = _txt(r[1])
    return bool(r[0]), int(sello) if sello else None, _txt(r[2])
