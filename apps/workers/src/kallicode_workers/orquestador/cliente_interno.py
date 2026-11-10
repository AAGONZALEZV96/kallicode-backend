"""Cliente HTTP del orquestador hacia /internal/v1.

Firma cada petición con un JWT de servicio (typ=svc) emitido por tenant y
añade Idempotency-Key derivada del job, la etapa y el intento, de modo que
un reintento del orquestador es convergente en el servidor.

Decisión de diseño: el token de servicio (TTL 5 min por defecto) se emite
bajo demanda en cada llamada. Como cada petición HTTP dura segundos, es
imposible que caduque a mitad de una llamada, y evita cachear tokens con
gestión de expiración.
"""

from __future__ import annotations

from typing import Any

import httpx
from kallicode_core.config import get_settings
from kallicode_core.logging import log
from kallicode_core.seguridad import emitir_svc_token


class ClienteInterno:
    """Cliente HTTP del orquestador contra la API interna.

    El token de servicio se emite bajo demanda (TTL 5 min) y está atado al
    tenant del job. El cliente mantiene un único AsyncClient con pool de
    conexiones reutilizado durante toda la vida del proceso.

    La URL base y el nombre del servicio se leen de configuración
    (`internal_api_base_url` y `orquestador_servicio`); el nombre debe
    coincidir con la allowlist de `SERVICIOS_PERMITIDOS` en deps.py.
    """

    def __init__(
        self,
        base_url: str | None = None,
        servicio: str | None = None,
        *,
        timeout_s: float = 30.0,
    ) -> None:
        s = get_settings()
        self._base = (base_url or s.internal_api_base_url).rstrip("/")
        self._servicio = servicio or s.orquestador_servicio
        self._http = httpx.AsyncClient(
            base_url=self._base,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30.0,
            ),
        )

    async def _headers(self, tenant_id: str, clave: str) -> dict[str, str]:
        token = emitir_svc_token(self._servicio, tenant_id)
        return {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": clave,
        }

    # ------------------------------------------------------------------ API
    async def transicionar(
        self,
        *,
        job_id: str,
        tenant_id: str,
        a: str,
        motivo: str,
        intento: int,
        contexto: dict[str, Any] | None = None,
    ) -> dict:
        """PATCH /jobs/{job_id}/state con clave de idempotencia por intento."""
        clave = f"{job_id}:state:{intento}"
        r = await self._http.patch(
            f"/jobs/{job_id}/state",
            json={"a": a, "motivo": motivo, "contexto": contexto},
            headers=await self._headers(tenant_id, clave),
        )
        return self._json(r, "transicionar", job_id=job_id, a=a)

    async def publicar_paso(
        self,
        *,
        job_id: str,
        tenant_id: str,
        paso: dict,
        intento: int,
    ) -> dict:
        """POST /jobs/{job_id}/steps para registrar un paso LLM."""
        clave = f"{job_id}:{paso['paso']}:{intento}"
        r = await self._http.post(
            f"/jobs/{job_id}/steps",
            json=paso,
            headers=await self._headers(tenant_id, clave),
        )
        return self._json(r, "publicar_paso", job_id=job_id, paso=paso.get("paso"))

    async def publicar_triage(
        self,
        *,
        numero: str,
        tenant_id: str,
        resultado: dict,
        intento: int,
    ) -> dict:
        """POST /tickets/{numero}/triage-result con la clasificación agregada."""
        clave = f"{numero}:triage:{intento}"
        r = await self._http.post(
            f"/tickets/{numero}/triage-result",
            json=resultado,
            headers=await self._headers(tenant_id, clave),
        )
        return self._json(r, "publicar_triage", numero=numero)

    async def publicar_spec(
        self,
        *,
        job_id: str,
        numero: str,
        tenant_id: str,
        spec: dict[str, Any],
        intento: int,
    ) -> dict:
        """Publica una única versión de spec por job e intento."""
        clave = f"{job_id}:design.spec:{intento}"
        r = await self._http.post(
            f"/tickets/{numero}/spec-versions",
            json=spec,
            headers=await self._headers(tenant_id, clave),
        )
        return self._json(r, "publicar_spec", job_id=job_id, numero=numero)

    async def publicar_loc(
        self,
        *,
        job_id: str,
        tenant_id: str,
        loc: dict[str, Any],
        intento: int,
    ) -> dict:
        """Registra LOC asociadas a un PR real, con idempotencia por job."""
        clave = f"{job_id}:build.loc:{intento}"
        r = await self._http.post(
            "/usage/loc",
            json=loc,
            headers=await self._headers(tenant_id, clave),
        )
        return self._json(r, "publicar_loc", job_id=job_id)

    async def escalar(
        self,
        *,
        job_id: str,
        tenant_id: str,
        etapa: str,
        motivo: str,
        severidad: str,
        detalle: dict,
        intento: int,
        paso: str | None = None,
    ) -> dict:
        """POST /escalations cuando se agotan los reintentos."""
        clave = f"{job_id}:escalation:{intento}"
        r = await self._http.post(
            "/escalations",
            json={
                "job_id": job_id,
                "etapa": etapa,
                "paso": paso,
                "motivo": motivo,
                "severidad": severidad,
                "detalle": detalle,
            },
            headers=await self._headers(tenant_id, clave),
        )
        return self._json(r, "escalar", job_id=job_id, motivo=motivo)

    # ---------------------------------------------------------------- cierre
    async def cerrar(self) -> None:
        """Cierra el pool de conexiones. Idempotente."""
        await self._http.aclose()

    # --------------------------------------------------------------- interno
    def _json(self, r: httpx.Response, op: str, **ctx: Any) -> dict:
        """Devuelve el JSON o lanza con contexto si la respuesta no es 2xx."""
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error(
                "orquestador.cliente_error",
                op=op,
                status=exc.response.status_code,
                cuerpo=exc.response.text[:500],
                **ctx,
            )
            raise
        return r.json() if r.content else {}
