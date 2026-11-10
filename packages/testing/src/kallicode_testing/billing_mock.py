"""Mock del gateway de pagos y de la facturación comercial (KC-QA-B5-08).

Doble de prueba en memoria: simula cuotas de plan, consumo de LOC y upgrades de plan
sin llamar a Stripe ni a ningún gateway real (estrategia cero-nube).

Replica las reglas reales del backend para que las pruebas no inventen comportamiento:

- Planes y límites: ``core.plans`` en ``db/01_schema.sql`` (starter, growth, enterprise).
- Umbral QU-1: trigger de ``db/03_triggers.sql`` -> ``normal`` bajo 80 %, ``aviso_80``
  desde 80 % y ``agotado`` al 100 %. Enterprise no tiene límite (siempre ``normal``).
- Creación de tickets (``routers/tickets.py``): con cuota agotada el ticket NO entra a
  ``triage``; queda en ``en_cola_por_cuota`` y la API responde 202 en vez de 201.
- Concurrencia QU-2: Starter 1 línea, Growth 3 líneas, Enterprise ilimitado.
- Almacenamiento QU-3: Starter 5 GB (5.120 MB), Growth 25 GB (25.600 MB), Enterprise ilimitado.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict

# ==============================================================================
# Tipos y Constantes Canónicas
# ==============================================================================

Umbral = Literal["normal", "aviso_80", "agotado"]
TARJETA_RECHAZADA = "tok_chargeDeclined"
ORDEN_PLANES: list[str] = ["starter", "growth", "enterprise"]

# ==============================================================================
# DTOs Inmutables Pydantic v2 (KC-QA-B5-08)
# ==============================================================================


class PlanTierDTO(BaseModel):
    """Definición inmutable de límites y tarifas por nivel de plan comercial."""

    model_config = ConfigDict(frozen=True)

    plan_code: Literal["starter", "growth", "enterprise"]
    loc_limit: int
    concurrent_lines: int
    storage_gb: int
    price_usd: float


class SubscriptionStatusDTO(BaseModel):
    """Estado inmutable de la suscripción y uso acumulado de un tenant."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    plan_code: str
    is_active: bool
    current_loc_usage: int
    current_storage_mb: float
    active_lines: int
    renews_at: str


class QuotaVerificationResultDTO(BaseModel):
    """Resultado inmutable de la evaluación de cuota de LOC y umbrales QU-1."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    threshold_status: Literal["normal", "warning_80", "exceeded_100"]
    loc_consumed: int
    loc_limit: int
    warning_message: str | None = None


class InvoiceMockDTO(BaseModel):
    """Factura comercial simulada emitida por el gateway sintético."""

    model_config = ConfigDict(frozen=True)

    invoice_id: str
    tenant_id: str
    amount_usd: float
    status: Literal["paid", "pending", "failed"]
    billing_reason: str
    created_at: str


# ==============================================================================
# Catálogo Canónico de Planes Oficiales (db/01_schema.sql)
# ==============================================================================

PLAN_TIERS: dict[str, PlanTierDTO] = {
    "starter": PlanTierDTO(
        plan_code="starter",
        loc_limit=3000,
        concurrent_lines=1,
        storage_gb=5,
        price_usd=49.0,
    ),
    "growth": PlanTierDTO(
        plan_code="growth",
        loc_limit=8000,
        concurrent_lines=3,
        storage_gb=25,
        price_usd=199.0,
    ),
    "enterprise": PlanTierDTO(
        plan_code="enterprise",
        loc_limit=1_000_000_000,
        concurrent_lines=999,
        storage_gb=100_000,
        price_usd=999.0,
    ),
}

# ==============================================================================
# Cliente Principal de Emulación Comercial: BillingMockClient
# ==============================================================================


class BillingMockClient:
    """Motor de emulación comercial 100% offline para verificación de cuotas y pagos."""

    def __init__(self) -> None:
        self._plans: dict[str, PlanTierDTO] = dict(PLAN_TIERS)
        self._subscriptions: dict[str, SubscriptionStatusDTO] = {}
        self._invoices: list[InvoiceMockDTO] = []
        self._upgrades: dict[str, dict[str, Any]] = {}

    def get_or_create_subscription(
        self,
        tenant_id: str,
        plan_code: Literal["starter", "growth", "enterprise"] = "starter",
    ) -> SubscriptionStatusDTO:
        """Devuelve la suscripción activa del tenant o inicializa una por defecto."""
        if tenant_id not in self._subscriptions:
            now_iso = datetime.now(UTC).isoformat()
            self._subscriptions[tenant_id] = SubscriptionStatusDTO(
                tenant_id=tenant_id,
                plan_code=plan_code,
                is_active=True,
                current_loc_usage=0,
                current_storage_mb=0.0,
                active_lines=0,
                renews_at=now_iso,
            )
        return self._subscriptions[tenant_id]

    def set_loc_usage(self, tenant_id: str, loc_consumed: int) -> None:
        """Actualiza el consumo acumulado de LOC de un tenant."""
        sub = self.get_or_create_subscription(tenant_id)
        self._subscriptions[tenant_id] = SubscriptionStatusDTO(
            tenant_id=sub.tenant_id,
            plan_code=sub.plan_code,
            is_active=sub.is_active,
            current_loc_usage=max(loc_consumed, 0),
            current_storage_mb=sub.current_storage_mb,
            active_lines=sub.active_lines,
            renews_at=sub.renews_at,
        )

    def set_storage_usage(self, tenant_id: str, storage_mb: float) -> None:
        """Actualiza el almacenamiento ocupado en MB de un tenant."""
        sub = self.get_or_create_subscription(tenant_id)
        self._subscriptions[tenant_id] = SubscriptionStatusDTO(
            tenant_id=sub.tenant_id,
            plan_code=sub.plan_code,
            is_active=sub.is_active,
            current_loc_usage=sub.current_loc_usage,
            current_storage_mb=max(storage_mb, 0.0),
            active_lines=sub.active_lines,
            renews_at=sub.renews_at,
        )

    def set_plan(
        self,
        tenant_id: str,
        plan_code: Literal["starter", "growth", "enterprise"],
    ) -> None:
        """Asigna un plan comercial específico al tenant."""
        sub = self.get_or_create_subscription(tenant_id)
        self._subscriptions[tenant_id] = SubscriptionStatusDTO(
            tenant_id=sub.tenant_id,
            plan_code=plan_code,
            is_active=sub.is_active,
            current_loc_usage=sub.current_loc_usage,
            current_storage_mb=sub.current_storage_mb,
            active_lines=sub.active_lines,
            renews_at=sub.renews_at,
        )

    def verify_loc_quota(
        self,
        tenant_id: str,
        new_loc_estimate: int = 0,
    ) -> QuotaVerificationResultDTO:
        """Evalúa la cuota mensual de LOC (QU-1) y determina si el inicio de trabajo es permitido.

        - Consumo < 80%: normal (allowed=True)
        - Consumo 80%..99.9%: warning_80 (allowed=True, aviso en cabecera)
        - Consumo >= 100%: exceeded_100 (allowed=False, encola por cuota con HTTP 202)
        - Enterprise: ilimitado (siempre normal y allowed=True)
        """
        sub = self.get_or_create_subscription(tenant_id)
        plan = self._plans.get(sub.plan_code, self._plans["starter"])

        consumo_total = sub.current_loc_usage + max(new_loc_estimate, 0)

        if sub.plan_code == "enterprise":
            return QuotaVerificationResultDTO(
                allowed=True,
                threshold_status="normal",
                loc_consumed=consumo_total,
                loc_limit=plan.loc_limit,
                warning_message=None,
            )

        limite = plan.loc_limit
        if consumo_total >= limite:
            return QuotaVerificationResultDTO(
                allowed=False,
                threshold_status="exceeded_100",
                loc_consumed=consumo_total,
                loc_limit=limite,
                warning_message="Cuota mensual de LOC agotada (100%). Ticket encolado.",
            )

        if consumo_total >= limite * 0.8:
            return QuotaVerificationResultDTO(
                allowed=True,
                threshold_status="warning_80",
                loc_consumed=consumo_total,
                loc_limit=limite,
                warning_message="Alcanzado el 80% de la cuota mensual de LOC.",
            )

        return QuotaVerificationResultDTO(
            allowed=True,
            threshold_status="normal",
            loc_consumed=consumo_total,
            loc_limit=limite,
            warning_message=None,
        )

    def verify_concurrent_lines(self, tenant_id: str, requested_line: int) -> bool:
        """Verifica si la línea de producción solicitada (QU-2) cabe dentro del límite del plan."""
        sub = self.get_or_create_subscription(tenant_id)
        plan = self._plans.get(sub.plan_code, self._plans["starter"])

        if sub.plan_code == "enterprise":
            return requested_line >= 1

        return 1 <= requested_line <= plan.concurrent_lines

    def verify_storage_quota(self, tenant_id: str, additional_mb: float = 0.0) -> bool:
        """Verifica si el almacenamiento de evidencias (QU-3) cabe en el límite del plan."""
        sub = self.get_or_create_subscription(tenant_id)
        plan = self._plans.get(sub.plan_code, self._plans["starter"])

        if sub.plan_code == "enterprise":
            return True

        limite_mb = plan.storage_gb * 1024.0
        total_mb = sub.current_storage_mb + max(additional_mb, 0.0)
        return total_mb <= limite_mb

    def process_mock_payment(
        self,
        tenant_id: str,
        amount_usd: float,
        payment_token: str = "tok_visa",
    ) -> InvoiceMockDTO:
        """Simula una transacción de cobro con tarjeta sin realizar llamadas externas."""
        status: Literal["paid", "pending", "failed"] = (
            "failed" if payment_token == TARJETA_RECHAZADA or amount_usd <= 0 else "paid"
        )
        invoice = InvoiceMockDTO(
            invoice_id=f"inv_mock_{uuid.uuid4().hex[:10]}",
            tenant_id=tenant_id,
            amount_usd=amount_usd,
            status=status,
            billing_reason="subscription_cycle",
            created_at=datetime.now(UTC).isoformat(),
        )
        self._invoices.append(invoice)
        return invoice

    def request_plan_upgrade(
        self,
        tenant_id: str,
        target_plan: str,
        idempotency_key: str | None = None,
        payment_token: str = "tok_visa",
    ) -> dict[str, Any]:
        """Procesa una solicitud de upgrade comercial de forma estrictamente idempotente."""
        # Verificación de idempotencia
        if idempotency_key and idempotency_key in self._upgrades:
            return self._upgrades[idempotency_key]

        if target_plan not in self._plans:
            raise ValueError(f"Plan desconocido: {target_plan}")

        sub = self.get_or_create_subscription(tenant_id)
        current_plan = sub.plan_code

        if ORDEN_PLANES.index(target_plan) <= ORDEN_PLANES.index(current_plan):
            raise ValueError(
                f"No se permite downgrade ni cambio al mismo plan ({target_plan} desde {current_plan})"
            )

        if payment_token == TARJETA_RECHAZADA:
            self.process_mock_payment(tenant_id, self._plans[target_plan].price_usd, payment_token)
            raise PagoRechazadoError("card_declined")

        invoice = self.process_mock_payment(
            tenant_id, self._plans[target_plan].price_usd, payment_token
        )

        # Aplicar nuevo plan
        self._subscriptions[tenant_id] = SubscriptionStatusDTO(
            tenant_id=sub.tenant_id,
            plan_code=target_plan,
            is_active=True,
            current_loc_usage=sub.current_loc_usage,
            current_storage_mb=sub.current_storage_mb,
            active_lines=sub.active_lines,
            renews_at=sub.renews_at,
        )

        result: dict[str, Any] = {
            "tenant_id": tenant_id,
            "previous_plan": current_plan,
            "new_plan": target_plan,
            "invoice_id": invoice.invoice_id,
            "status": "upgraded",
            "amount_paid": invoice.amount_usd,
        }

        if idempotency_key:
            self._upgrades[idempotency_key] = result

        return result


# ==============================================================================
# Retrocompatibilidad con GatewayPagosMock (usado en tests previos)
# ==============================================================================


@dataclass(frozen=True)
class Plan:
    codigo: str
    loc_mes: int | None  # None = sin límite (Enterprise)
    max_usuarios: int | None


PLANES: dict[str, Plan] = {
    "starter": Plan("starter", loc_mes=3000, max_usuarios=5),
    "growth": Plan("growth", loc_mes=8000, max_usuarios=25),
    "enterprise": Plan("enterprise", loc_mes=None, max_usuarios=None),
}


class EstadoCuota(TypedDict):
    consumidas: int
    limite: int | None
    porcentaje: float
    umbral: Umbral


class ResultadoTicket(TypedDict):
    http: int
    etapa: str


class PagoRechazadoError(Exception):
    """El gateway simulado rechazó el cobro (equivale a un card_declined de Stripe)."""


@dataclass
class GatewayPagosMock:
    """Gateway de pagos falso para un tenant. Todo ocurre en memoria."""

    plan: str = "starter"
    loc_consumidas: int = 0
    cobros: list[dict[str, str]] = field(default_factory=list)

    def estado_cuota(self) -> EstadoCuota:
        """Misma forma que ``kallicode_core.comercial.estado_cuota_loc``."""
        limite = PLANES[self.plan].loc_mes
        if limite is None:
            return {
                "consumidas": self.loc_consumidas,
                "limite": None,
                "porcentaje": 0.0,
                "umbral": "normal",
            }
        porcentaje = round(self.loc_consumidas * 100.0 / limite, 1)
        umbral: Umbral = "normal"
        if self.loc_consumidas >= limite:
            umbral = "agotado"
        elif self.loc_consumidas >= limite * 0.8:
            umbral = "aviso_80"
        return {
            "consumidas": self.loc_consumidas,
            "limite": limite,
            "porcentaje": porcentaje,
            "umbral": umbral,
        }

    def consumir_loc(self, loc: int) -> EstadoCuota:
        """Registra LOC producidas. Las netas negativas no descuentan (``greatest(n, 0)``)."""
        self.loc_consumidas += max(loc, 0)
        return self.estado_cuota()

    def crear_ticket(self) -> ResultadoTicket:
        """Con cuota agotada el ticket se encola en vez de entrar a la línea de producción."""
        if self.estado_cuota()["umbral"] == "agotado":
            return {"http": 202, "etapa": "en_cola_por_cuota"}
        return {"http": 201, "etapa": "triage"}

    def puede_invitar_usuario(self, usuarios_actuales: int) -> bool:
        limite = PLANES[self.plan].max_usuarios
        return limite is None or usuarios_actuales < limite

    def upgrade(self, plan_nuevo: str, token_tarjeta: str = "tok_visa") -> dict[str, str]:
        """Cobra el nuevo plan en el gateway simulado y lo activa si el pago pasa."""
        if plan_nuevo not in PLANES:
            raise ValueError(f"Plan desconocido: {plan_nuevo}")
        if ORDEN_PLANES.index(plan_nuevo) <= ORDEN_PLANES.index(self.plan):
            raise ValueError(f"{plan_nuevo} no es un upgrade desde {self.plan}")
        if token_tarjeta == TARJETA_RECHAZADA:
            raise PagoRechazadoError("card_declined")

        cobro = {
            "id": f"ch_mock_{len(self.cobros) + 1}",
            "plan": plan_nuevo,
            "estado": "succeeded",
        }
        self.cobros.append(cobro)
        self.plan = plan_nuevo
        return cobro
