from unittest.mock import AsyncMock, MagicMock

import pytest
from kallicode_core.llm.cliente import _forzar_esquema_estricto
from kallicode_core.llm.seguridad import (
    CuotaExcedidaError,
    InyeccionDetectadaError,
    validar_decisiones_criticas,
    verificar_y_consumir_cuota,
)


def test_esquema_estricto_bloquea_alucinaciones():
    """Prueba que el cliente inyecta correctly additionalProperties: False."""
    esquema_vulnerable = {"type": "object", "properties": {"tier": {"type": "string"}}}

    _forzar_esquema_estricto(esquema_vulnerable)

    assert esquema_vulnerable["additionalProperties"] is False, "El esquema raíz debe ser estricto"


@pytest.mark.asyncio
async def test_cuota_excedida_bloquea_ejecucion():
    """Prueba que Redis corta la ejecución si el atacante gasta tokens."""
    # MagicMock asegura que la llamada a .pipeline() no sea tratada como corutina
    mock_redis = MagicMock()
    mock_pipe = AsyncMock()

    # Configuramos el pipeline para que soporte 'async with' y devuelva nuestros valores
    mock_pipe.__aenter__.return_value = mock_pipe
    mock_pipe.execute.return_value = [2_000_001, 150]
    mock_redis.pipeline.return_value = mock_pipe

    with pytest.raises(CuotaExcedidaError, match="superó la cuota mensual"):
        await verificar_y_consumir_cuota(
            mock_redis, "tenant_hacker", "KC-999", tokens_consumidos=1000
        )


@pytest.mark.asyncio
async def test_inyeccion_tier_invalido():
    """Prueba que un atacante no puede forzar un tier inexistente o superior no permitido."""
    mock_db = AsyncMock()
    salida_maliciosa = {"recommended_tier_downstream": "TIER_GOD_MODE"}

    with pytest.raises(InyeccionDetectadaError, match="Alucinación de billing detectada"):
        await validar_decisiones_criticas(mock_db, "tenant_123", salida_maliciosa)


@pytest.mark.asyncio
async def test_inyeccion_duplicate_of_cross_tenant():
    """Prueba que un atacante no puede cerrar tickets de otros tenants."""
    mock_db = AsyncMock()
    # db.execute devuelve None (ticket no pertenece al tenant)
    mock_resultado = MagicMock()
    mock_resultado.scalar.return_value = False
    mock_db.execute.return_value = mock_resultado

    salida_maliciosa = {"duplicate_of": "TICKET_DE_OTRA_EMPRESA"}

    with pytest.raises(InyeccionDetectadaError, match="Intento de manipulación cruzada"):
        await validar_decisiones_criticas(mock_db, "tenant_123", salida_maliciosa)


@pytest.mark.asyncio
async def test_inyeccion_scope_paths():
    """Prueba que el modelo no puede alterar archivos fuera de la allowlist del tenant."""
    mock_db = AsyncMock()
    # db.execute devuelve las rutas permitidas para el tenant
    mock_resultado = MagicMock()
    mock_resultado.fetchall.return_value = [("/src/frontend",)]
    mock_db.execute.return_value = mock_resultado

    salida_maliciosa = {
        # El modelo intenta modificar el frontend (legal) y archivos del sistema (ilegal)
        "scope_paths": ["/src/frontend/app.tsx", "/etc/shadow"]
    }

    with pytest.raises(InyeccionDetectadaError, match="Escalamiento de privilegios bloqueado"):
        await validar_decisiones_criticas(mock_db, "tenant_123", salida_maliciosa)
