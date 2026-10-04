import jwt
from fastapi.testclient import TestClient
from kallicode_api.main import app
from kallicode_core.seguridad import AUD_PORTAL, ISS_PORTAL, _claves

client = TestClient(app)


def generar_token_malformado(tipo="user"):
    priv, _ = _claves(tipo)
    iss = ISS_PORTAL if tipo == "user" else "kc-internal"
    aud = AUD_PORTAL if tipo == "user" else "kc-internal-api"

    return jwt.encode({"iss": iss, "aud": aud, "typ": tipo}, priv, algorithm="RS256")


def test_routers_retornan_401_ante_claims_faltantes():
    token = generar_token_malformado("user")
    headers = {"Authorization": f"Bearer {token}"}

    # Rutas reales sacadas de tu código
    rutas_protegidas = ["/api/v1/users/me", "/api/v1/organization", "/api/v1/usage/summary"]

    for ruta in rutas_protegidas:
        res = client.get(ruta, headers=headers)

        # El core del ticket: debe ser 401, nunca 500 ni 404
        assert res.status_code == 401, (
            f"Fallo en {ruta}: esperaba 401, obtuve {res.status_code}. Body: {res.text}"
        )
