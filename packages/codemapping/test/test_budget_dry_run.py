"""Pruebas unitarias para estimación en seco y presupuesto (KC-CM-05-5).

La consulta contra PostgreSQL real está en tests/test_presupuesto_ingesta_pg.py (D2 del ADR-018): aquí
la conexión es un doble y solo se prueba la aritmética de cuota, consumo y estimación.
"""

from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    estimar_tokens_ingesta,
    verificar_presupuesto_tenant,
)


def _conn(consumido: int | None) -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = (consumido,)
    return conn, cur


def test_estimar_tokens_ingesta_margen_esperado():
    simbolos = [
        {"signature": "def calcular_total(items: list) -> float:", "snippet": "return sum(items)"},
        {"signature": "class OrderService:", "snippet": "def execute(self): pass"},
    ]
    prompt_tokens, comp_tokens = estimar_tokens_ingesta(simbolos)

    assert prompt_tokens > 0
    assert comp_tokens > 0
    assert prompt_tokens + comp_tokens < 1000  # Rango coherente para 2 símbolos pequeños


def test_verificar_presupuesto_aprobado():
    conn, _ = _conn(10_000)

    simbolos = [{"signature": "def test():", "snippet": "pass"}]
    res = verificar_presupuesto_tenant(conn, "tenant-abc", simbolos, cuota_tokens=100_000)

    assert res.presupuesto_suficiente is True
    assert res.cuota_disponible == 90_000


def test_verificar_presupuesto_insuficiente_lanza_excepcion():
    conn, _ = _conn(450)

    simbolos = [{"signature": f"def symbol_{i}():", "snippet": "x = 1"} for i in range(20)]

    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(conn, "tenant-low-balance", simbolos, cuota_tokens=500)

    assert "Presupuesto insuficiente" in str(exc_info.value)


def test_la_consulta_fija_el_tenant_de_la_sesion_antes_de_sumar():
    """Sin app.tenant_id, RLS esconde core.usage_tokens y el consumo sale siempre 0."""
    conn, cur = _conn(0)

    verificar_presupuesto_tenant(conn, "org_x", [{"signature": "s", "snippet": "x"}])

    primera, segunda = cur.execute.call_args_list
    assert primera.args == ("SELECT set_config('app.tenant_id', %s, true)", ("org_x",))
    assert "core.obtener_ciclo_cuota_actual" in segunda.args[0]
    assert segunda.args[1] == {"tenant": "org_x"}
    conn.transaction.assert_called_once()
