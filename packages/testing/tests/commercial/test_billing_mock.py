"""Suite de pruebas para el motor de facturación comercial y mock de gateway de pagos.

Ticket: KC-QA-B5-08
Épica: EQA4 - Pruebas de Desempeño, Carga y Límites del Sistema
Propósito: Validar que el cliente BillingMockClient simule de forma 100% offline
la verificación de cuotas comerciales (QU-1 LOC, QU-2 Líneas, QU-3 Almacenamiento),
procesamiento de cobros sintéticos, facturas inmutables y upgrades idempotentes.
"""

import pytest
from kallicode_testing.billing_mock import (
    TARJETA_RECHAZADA,
    BillingMockClient,
    InvoiceMockDTO,
    PagoRechazadoError,
    PlanTierDTO,
    QuotaVerificationResultDTO,
    SubscriptionStatusDTO,
)
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# 1. test_verificacion_cuota_loc_y_bloqueo_en_cola_por_cuota()
# ---------------------------------------------------------------------------
def test_verificacion_cuota_loc_y_bloqueo_en_cola_por_cuota() -> None:
    """Valida los umbrales QU-1 de LOC y el bloqueo determinista al llegar al 100%.

    - Consumo < 80%: normal (allowed=True)
    - Consumo >= 80% y < 100%: warning_80 (allowed=True, con warning_message)
    - Consumo >= 100%: exceeded_100 (allowed=False, encolado con HTTP 202)
    - Enterprise: sin límite (siempre normal)
    """
    client = BillingMockClient()
    tenant = "org_test_qu1"

    # Plan Starter: Límite 3000 LOC
    client.get_or_create_subscription(tenant, plan_code="starter")

    # A. Consumo 50% (1500 LOC) -> normal
    client.set_loc_usage(tenant, 1500)
    res_normal = client.verify_loc_quota(tenant)
    assert res_normal.allowed is True
    assert res_normal.threshold_status == "normal"
    assert res_normal.warning_message is None
    assert res_normal.loc_consumed == 1500
    assert res_normal.loc_limit == 3000

    # B. Consumo 80% (2400 LOC) -> warning_80
    client.set_loc_usage(tenant, 2400)
    res_warning = client.verify_loc_quota(tenant)
    assert res_warning.allowed is True
    assert res_warning.threshold_status == "warning_80"
    assert res_warning.warning_message is not None
    assert "80%" in res_warning.warning_message

    # C. Consumo 95% (2850 LOC) -> warning_80
    client.set_loc_usage(tenant, 2850)
    res_warning_95 = client.verify_loc_quota(tenant)
    assert res_warning_95.allowed is True
    assert res_warning_95.threshold_status == "warning_80"

    # D. Consumo 100% (3000 LOC) -> exceeded_100 (Bloqueado)
    client.set_loc_usage(tenant, 3000)
    res_bloqueado = client.verify_loc_quota(tenant)
    assert res_bloqueado.allowed is False
    assert res_bloqueado.threshold_status == "exceeded_100"
    assert res_bloqueado.warning_message is not None
    assert "agotada" in res_bloqueado.warning_message.lower()

    # E. Consumo excedido 120% (3600 LOC) con estimación adicional -> exceeded_100
    client.set_loc_usage(tenant, 3600)
    res_excedido = client.verify_loc_quota(tenant, new_loc_estimate=200)
    assert res_excedido.allowed is False
    assert res_excedido.threshold_status == "exceeded_100"
    assert res_excedido.loc_consumed == 3800

    # F. Enterprise nunca se agota
    client.set_plan(tenant, "enterprise")
    res_ent = client.verify_loc_quota(tenant, new_loc_estimate=1_000_000)
    assert res_ent.allowed is True
    assert res_ent.threshold_status == "normal"


# ---------------------------------------------------------------------------
# 2. test_mock_gateway_pagos_stripe_transacciones_sinteticas()
# ---------------------------------------------------------------------------
def test_mock_gateway_pagos_stripe_transacciones_sinteticas() -> None:
    """Simula transacciones de pago con tarjetas sintéticas sin llamadas externas."""
    client = BillingMockClient()
    tenant = "org_test_payments"

    # A. Cobro exitoso con tarjeta estándar (tok_visa)
    inv_exito = client.process_mock_payment(tenant, amount_usd=199.0, payment_token="tok_visa")
    assert isinstance(inv_exito, InvoiceMockDTO)
    assert inv_exito.tenant_id == tenant
    assert inv_exito.amount_usd == 199.0
    assert inv_exito.status == "paid"
    assert inv_exito.invoice_id.startswith("inv_mock_")
    assert inv_exito.created_at is not None

    # B. Cobro rechazado con tarjeta simulada de declinación (tok_chargeDeclined)
    inv_fallo = client.process_mock_payment(
        tenant, amount_usd=49.0, payment_token=TARJETA_RECHAZADA
    )
    assert inv_fallo.status == "failed"
    assert inv_fallo.amount_usd == 49.0


# ---------------------------------------------------------------------------
# 3. test_verificacion_lineas_concurrentes_y_storage()
# ---------------------------------------------------------------------------
def test_verificacion_lineas_concurrentes_y_storage() -> None:
    """Valida los umbrales QU-2 (líneas concurrentes) y QU-3 (almacenamiento de evidencias)."""
    client = BillingMockClient()
    tenant_starter = "org_starter_limits"
    tenant_growth = "org_growth_limits"

    client.get_or_create_subscription(tenant_starter, plan_code="starter")
    client.get_or_create_subscription(tenant_growth, plan_code="growth")

    # A. QU-2 Líneas Concurrentes: Starter = 1 línea; Growth = 3 líneas
    assert client.verify_concurrent_lines(tenant_starter, requested_line=1) is True
    assert client.verify_concurrent_lines(tenant_starter, requested_line=2) is False

    assert client.verify_concurrent_lines(tenant_growth, requested_line=1) is True
    assert client.verify_concurrent_lines(tenant_growth, requested_line=2) is True
    assert client.verify_concurrent_lines(tenant_growth, requested_line=3) is True
    assert client.verify_concurrent_lines(tenant_growth, requested_line=4) is False

    # B. QU-3 Almacenamiento: Starter = 5 GB (5.120 MB); Growth = 25 GB (25.600 MB)
    client.set_storage_usage(tenant_starter, storage_mb=5000.0)
    # 5000 + 100 = 5100 MB <= 5120 MB -> permitido
    assert client.verify_storage_quota(tenant_starter, additional_mb=100.0) is True
    # 5000 + 150 = 5150 MB > 5120 MB -> bloqueado
    assert client.verify_storage_quota(tenant_starter, additional_mb=150.0) is False

    client.set_storage_usage(tenant_growth, storage_mb=25000.0)
    # 25000 + 500 = 25500 MB <= 25600 MB -> permitido
    assert client.verify_storage_quota(tenant_growth, additional_mb=500.0) is True
    # 25000 + 700 = 25700 MB > 25600 MB -> bloqueado
    assert client.verify_storage_quota(tenant_growth, additional_mb=700.0) is False

    # C. Enterprise tiene líneas y almacenamiento ilimitados
    tenant_ent = "org_ent_limits"
    client.get_or_create_subscription(tenant_ent, plan_code="enterprise")
    assert client.verify_concurrent_lines(tenant_ent, requested_line=50) is True
    assert client.verify_storage_quota(tenant_ent, additional_mb=500_000.0) is True


# ---------------------------------------------------------------------------
# 4. test_solicitud_upgrade_plan_e_idempotencia()
# ---------------------------------------------------------------------------
def test_solicitud_upgrade_plan_e_idempotencia() -> None:
    """Valida el flujo de upgrade de plan y su comportamiento estrictamente idempotente."""
    client = BillingMockClient()
    tenant = "org_upgrade_test"
    client.get_or_create_subscription(tenant, plan_code="starter")

    ik = "ik_upgrade_uuid_12345"

    # A. Primera llamada de upgrade (starter -> growth)
    res1 = client.request_plan_upgrade(tenant, target_plan="growth", idempotency_key=ik)
    assert res1["status"] == "upgraded"
    assert res1["previous_plan"] == "starter"
    assert res1["new_plan"] == "growth"
    assert "invoice_id" in res1
    assert client.get_or_create_subscription(tenant).plan_code == "growth"

    # B. Reintento con la misma idempotency_key devuelve el mismo resultado
    res2 = client.request_plan_upgrade(tenant, target_plan="growth", idempotency_key=ik)
    assert res1 == res2
    assert res2["invoice_id"] == res1["invoice_id"]

    # B2. Upgrade sin idempotency_key
    res_no_ik = client.request_plan_upgrade(tenant, target_plan="enterprise", idempotency_key=None)
    assert res_no_ik["new_plan"] == "enterprise"

    # C. Rechazo de downgrade (growth -> starter)
    with pytest.raises(ValueError, match="No se permite downgrade"):
        client.request_plan_upgrade(tenant, target_plan="starter")

    # D. Rechazo de plan desconocido
    with pytest.raises(ValueError, match="Plan desconocido"):
        client.request_plan_upgrade(tenant, target_plan="plan_inexistente")

    # E. Rechazo si la tarjeta es declinada
    tenant_declined = "org_declined_test"
    client.get_or_create_subscription(tenant_declined, plan_code="starter")
    with pytest.raises(PagoRechazadoError):
        client.request_plan_upgrade(
            tenant_declined, target_plan="growth", payment_token=TARJETA_RECHAZADA
        )
    # El plan no cambia tras el fallo
    assert client.get_or_create_subscription(tenant_declined).plan_code == "starter"


# ---------------------------------------------------------------------------
# 5. test_inmutabilidad_dtos_comerciales()
# ---------------------------------------------------------------------------
def test_inmutabilidad_dtos_comerciales() -> None:
    """Garantiza que todos los DTOs comerciales mantengan inmutabilidad estricta (frozen=True)."""
    # 1. PlanTierDTO
    plan = PlanTierDTO(
        plan_code="starter",
        loc_limit=3000,
        concurrent_lines=1,
        storage_gb=5,
        price_usd=49.0,
    )
    with pytest.raises(ValidationError):
        # Mutación prohibida por frozen=True
        plan.loc_limit = 5000

    # 2. SubscriptionStatusDTO
    sub = SubscriptionStatusDTO(
        tenant_id="org_demo",
        plan_code="growth",
        is_active=True,
        current_loc_usage=1000,
        current_storage_mb=500.0,
        active_lines=2,
        renews_at="2026-12-01T00:00:00Z",
    )
    with pytest.raises(ValidationError):
        sub.is_active = False

    # 3. QuotaVerificationResultDTO
    quota = QuotaVerificationResultDTO(
        allowed=True,
        threshold_status="normal",
        loc_consumed=100,
        loc_limit=3000,
        warning_message=None,
    )
    with pytest.raises(ValidationError):
        quota.allowed = False

    # 4. InvoiceMockDTO
    invoice = InvoiceMockDTO(
        invoice_id="inv_123",
        tenant_id="org_demo",
        amount_usd=99.0,
        status="paid",
        billing_reason="cycle",
        created_at="2026-11-08T00:00:00Z",
    )
    with pytest.raises(ValidationError):
        invoice.status = "failed"
