"""Verificación de cierre GA para KC-CM-12-5 y KC-DB-12-4."""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]


def test_nota_release_contrato_cm_queries_completa() -> None:
    nota = (RAIZ / "docs" / "release" / "contrato_cm_queries_v1.md").read_text(encoding="utf-8")
    for op in (
        "resolver_nodos",
        "semillas",
        "impacto",
        "dependencias",
        "subgrafo",
        "vecinos",
        "dependencias_ocultas",
        "buscar_hibrido",
        "resolver_nuggets",
        "resolver_incidentes",
    ):
        assert f"`{op}`" in nota, f"Falta la operación {op} en la nota de release"
    assert "Tech Lead de Backend" in nota
    assert "Tech Lead de Testing" in nota
    assert "Límites conocidos" in nota

    expediente = (RAIZ / "docs" / "release" / "expediente.md").read_text(encoding="utf-8")
    assert "contrato_cm_queries_v1.md" in expediente


def test_plan_indices_documenta_linea_base_y_retiros_ga() -> None:
    plan = (RAIZ / "docs" / "plan-indices.md").read_text(encoding="utf-8")
    assert "Línea base GA (KC-DB-12-4)" in plan
    assert "6776 kB" in plan
    assert "071_retiro_indices_no_usados.sql" in plan
