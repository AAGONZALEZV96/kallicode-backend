import pytest
from unittest.mock import AsyncMock, MagicMock
from kallicode_core.pipeline.transiciones import transicionar
from kallicode_core.pipeline.estados import EstadoJob
from kallicode_core.errors import AppError


@pytest.mark.anyio
async def test_transicionar_valida_exito():
    db = AsyncMock()
    # Simular que el job existe en estado "creado"
    mock_res = MagicMock()
    mock_res.mappings.return_value.first.return_value = {
        "id": "job_123",
        "estado": EstadoJob.CREADO,
        "tenant_id": "tenant_1",
    }
    db.execute.side_effect = [mock_res, AsyncMock(), AsyncMock()]

    resultado = await transicionar(
        db,
        job_id="job_123",
        tenant_id="tenant_1",
        nuevo_estado=EstadoJob.TRIAGE,
        motivo="inicio_proceso",
        actor="svc:triage",
    )

    assert resultado["job_id"] == "job_123"
    assert resultado["de_estado"] == EstadoJob.CREADO
    assert resultado["a_estado"] == EstadoJob.TRIAGE

    # Validar que el primer query usó FOR UPDATE
    query_lock = str(db.execute.call_args_list[0][0][0])
    assert "FOR UPDATE" in query_lock


@pytest.mark.anyio
async def test_transicionar_invalida_lanza_conflicto():
    db = AsyncMock()
    # Simular salto ilegal (CREADO -> DEPLOY)
    mock_res = MagicMock()
    mock_res.mappings.return_value.first.return_value = {
        "id": "job_123",
        "estado": EstadoJob.CREADO,
        "tenant_id": "tenant_1",
    }
    db.execute.return_value = mock_res

    with pytest.raises(AppError) as exc_info:
        await transicionar(
            db,
            job_id="job_123",
            tenant_id="tenant_1",
            nuevo_estado=EstadoJob.DEPLOY,
        )

    assert exc_info.value.codigo == "TRANSICION_INVALIDA"
    assert exc_info.value.http == 409


@pytest.mark.anyio
async def test_transicionar_job_inexistente_lanza_404():
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.mappings.return_value.first.return_value = None
    db.execute.return_value = mock_res

    with pytest.raises(AppError) as exc_info:
        await transicionar(
            db,
            job_id="job_inexistente",
            tenant_id="tenant_1",
            nuevo_estado=EstadoJob.TRIAGE,
        )

    assert exc_info.value.codigo == "RECURSO_NO_ENCONTRADO"
    assert exc_info.value.http == 404

@pytest.mark.anyio
async def test_transicionar_con_contexto_serializado():
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.mappings.return_value.first.return_value = {
        "id": "job_ctx",
        "estado": EstadoJob.BUILD,
        "tenant_id": "tenant_1",
    }
    db.execute.side_effect = [mock_res, AsyncMock(), AsyncMock()]

    ctx_data = {"archivos_modificados": ["main.py"], "intento": 1}
    res = await transicionar(
        db,
        job_id="job_ctx",
        tenant_id="tenant_1",
        nuevo_estado=EstadoJob.VERIFICACION_INTERNA,
        motivo="build_completado",
        contexto=ctx_data,
        actor="svc:builder",
    )

    assert res["a_estado"] == EstadoJob.VERIFICACION_INTERNA
    # Verificar que los parámetros del INSERT recibieron el JSON de contexto
    params_insert = db.execute.call_args_list[2][0][1]
    assert '"archivos_modificados": ["main.py"]' in params_insert["contexto"]
    assert params_insert["actor"] == "svc:builder"


@pytest.mark.anyio
async def test_transicionar_falla_db_propaga_error():
    db = AsyncMock()
    mock_res = MagicMock()
    mock_res.mappings.return_value.first.return_value = {
        "id": "job_err",
        "estado": EstadoJob.TRIAGE,
        "tenant_id": "tenant_1",
    }
    # Falla en el UPDATE de jobs
    db.execute.side_effect = [mock_res, RuntimeError("Error de I/O en Postgres")]

    with pytest.raises(RuntimeError, match="Error de I/O en Postgres"):
        await transicionar(
            db,
            job_id="job_err",
            tenant_id="tenant_1",
            nuevo_estado=EstadoJob.DESIGN,
        )