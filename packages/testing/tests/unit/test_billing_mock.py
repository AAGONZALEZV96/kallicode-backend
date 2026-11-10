"""Pruebas del mock de gateway de pagos y facturación (KC-QA-B5-08)."""

import pytest
from kallicode_testing.billing_mock import (
    TARJETA_RECHAZADA,
    GatewayPagosMock,
    PagoRechazadoError,
)


@pytest.mark.parametrize(
    ("consumidas", "umbral"),
    [(0, "normal"), (2399, "normal"), (2400, "aviso_80"), (2999, "aviso_80"), (3000, "agotado")],
)
def test_umbrales_qu1_en_plan_starter(consumidas: int, umbral: str) -> None:
    """Starter (3000 LOC): normal bajo 80 %, aviso desde 2400 y agotado en 3000."""
    gateway = GatewayPagosMock(plan="starter", loc_consumidas=consumidas)
    assert gateway.estado_cuota()["umbral"] == umbral


def test_ticket_entra_a_triage_con_cuota_disponible() -> None:
    """Bajo el límite, el ticket se crea normal: 201 y etapa triage."""
    gateway = GatewayPagosMock(plan="starter", loc_consumidas=2999)
    assert gateway.crear_ticket() == {"http": 201, "etapa": "triage"}


def test_ticket_queda_bloqueado_en_cola_al_superar_la_cuota() -> None:
    """Criterio de aceptación: al superar la cuota el ticket NO entra a producción."""
    gateway = GatewayPagosMock(plan="starter")
    gateway.consumir_loc(3500)

    resultado = gateway.crear_ticket()

    assert gateway.estado_cuota()["umbral"] == "agotado"
    assert resultado == {"http": 202, "etapa": "en_cola_por_cuota"}


def test_loc_negativas_no_devuelven_cuota() -> None:
    """Borrar código (netas negativas) no libera cuota, igual que el trigger real."""
    gateway = GatewayPagosMock(plan="starter", loc_consumidas=3000)
    gateway.consumir_loc(-500)
    assert gateway.estado_cuota()["consumidas"] == 3000


def test_enterprise_no_tiene_limite() -> None:
    """Enterprise nunca se agota, sin importar cuánto consuma."""
    gateway = GatewayPagosMock(plan="enterprise", loc_consumidas=1_000_000)
    assert gateway.estado_cuota()["umbral"] == "normal"
    assert gateway.crear_ticket()["etapa"] == "triage"


def test_upgrade_cobra_y_desbloquea_la_creacion_de_tickets() -> None:
    """Pasar de starter a growth sube el límite a 8000 y el ticket vuelve a triage."""
    gateway = GatewayPagosMock(plan="starter", loc_consumidas=3000)
    assert gateway.crear_ticket()["http"] == 202

    cobro = gateway.upgrade("growth")

    assert cobro["estado"] == "succeeded"
    assert cobro["plan"] == "growth"
    assert gateway.plan == "growth"
    assert gateway.crear_ticket() == {"http": 201, "etapa": "triage"}


def test_pago_rechazado_no_cambia_el_plan() -> None:
    """Si el gateway rechaza la tarjeta, el plan y los cobros quedan intactos."""
    gateway = GatewayPagosMock(plan="starter")
    with pytest.raises(PagoRechazadoError):
        gateway.upgrade("growth", token_tarjeta=TARJETA_RECHAZADA)
    assert gateway.plan == "starter"
    assert gateway.cobros == []


def test_no_se_permite_downgrade_ni_plan_desconocido() -> None:
    """upgrade() solo acepta planes superiores y existentes."""
    gateway = GatewayPagosMock(plan="growth")
    with pytest.raises(ValueError):
        gateway.upgrade("starter")
    with pytest.raises(ValueError):
        gateway.upgrade("platinum")


def test_cupo_de_usuarios_por_plan() -> None:
    """Starter permite 5 usuarios: el sexto no se puede invitar."""
    gateway = GatewayPagosMock(plan="starter")
    assert gateway.puede_invitar_usuario(4) is True
    assert gateway.puede_invitar_usuario(5) is False
    assert GatewayPagosMock(plan="enterprise").puede_invitar_usuario(10_000) is True
