"""Mock del gateway de pagos y de la facturación comercial (KC-QA-B5-08).

Doble de prueba en memoria: simula cuotas de plan, consumo de LOC y upgrades de plan
sin llamar a Stripe ni a ningún gateway real (estrategia cero-nube).

Replica las reglas reales del backend para que las pruebas no inventen comportamiento:

- Planes y límites: ``core.plans`` en ``db/01_schema.sql`` (starter, growth, enterprise).
- Umbral QU-1: trigger de ``db/03_triggers.sql`` -> ``normal`` bajo 80 %, ``aviso_80``
  desde 80 % y ``agotado`` al 100 %. Enterprise no tiene límite (siempre ``normal``).
- Creación de tickets (``routers/tickets.py``): con cuota agotada el ticket NO entra a
  ``triage``; queda en ``en_cola_por_cuota`` y la API responde 202 en vez de 201.
"""

from dataclasses import dataclass, field
from typing import Literal, TypedDict

Umbral = Literal["normal", "aviso_80", "agotado"]

TARJETA_RECHAZADA = "tok_chargeDeclined"


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
ORDEN_PLANES = ["starter", "growth", "enterprise"]


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
