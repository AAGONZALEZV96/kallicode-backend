"""Pruebas unitarias para estimación en seco y presupuesto (KC-CM-05-5)."""

from unittest.mock import MagicMock

import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    estimar_tokens_ingesta,
    verificar_presupuesto_tenant,
)


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
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    # Cuota: 100.000 tokens, Consumido: 10.000 tokens
    cur.fetchone.return_value = (100_000, 10_000)

    simbolos = [{"signature": "def test():", "snippet": "pass"}]
    res = verificar_presupuesto_tenant(conn, "tenant-abc", simbolos)

    assert res.presupuesto_suficiente is True
    assert res.cuota_disponible == 90_000


def test_verificar_presupuesto_insuficiente_lanza_excepcion():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    # Cuota: 500 tokens, Consumido: 450 tokens -> Disponible: 50
    cur.fetchone.return_value = (500, 450)

    simbolos = [
        {"signature": f"def symbol_{i}():", "snippet": "x = 1"} for i in range(20)
    ]

    with pytest.raises(BudgetExceededError) as exc_info:
        verificar_presupuesto_tenant(conn, "tenant-low-balance", simbolos)

    assert "Presupuesto insuficiente" in str(exc_info.value)