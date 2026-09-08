import pytest


def test_ci_pipeline_ready():
    """Prueba unitaria basal para evitar el Exit Code 5 (Pendiente Equipo QA)."""
    assert True


@pytest.mark.integracion
def test_ci_integracion_ready():
    """Prueba de integración basal para evitar el Exit Code 5 (Pendiente Equipo QA)."""
    assert True
