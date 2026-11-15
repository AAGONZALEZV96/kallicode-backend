"""Contrato cm-escalacion-v1 (KC-CM-10-6) sin base de datos.

- Prueba de contrato: el JSON Schema congelado en contratos/cm-escalacion-v1.schema.json es
  exactamente el que generan los modelos. Si falla, el contrato con Backend cambió: regenera con
  `python -m kallicode_core.cm_escalacion`, regístralo en contratos/cambios-anunciados.yaml y pide
  la aprobación de Backend.
- Validación estricta de la solicitud y compatibilidad hacia delante de la respuesta.

La semántica del cliente (respx) está en packages/codemapping/test/test_escalacion.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from kallicode_core import cm_escalacion as ce
from kallicode_core.cm_escalacion import EscalacionIngestaIn, EscalacionIngestaOut
from pydantic import ValidationError

SHA = "0123abcd" * 5


def _solicitud(**cambios: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": "org_a",
        "repo_id": "repo_1",
        "commit_sha": SHA,
        "snapshot_id": "snap_1",
        "trace_id": "tr_1",
        "motivo": "credenciales_expiradas",
        "severidad": "alta",
        "paso": "ingesta",
        "tier": None,
        "lote": None,
        "entrada_ref": f"repo_1@{SHA}",
        "diagnostico": {
            "tipo_error": "GitSyncError",
            "mensaje": "fatal: Authentication failed",
            "intentos": 1,
            "frames": [{"archivo": "ingest_repo.py", "linea": 173, "funcion": "sync_repo"}],
            "tiers_probados": [],
        },
    }
    base.update(cambios)
    return base


def test_contrato_congelado_coincide_con_los_modelos() -> None:
    congelado = json.loads(ce.RUTA_CONTRATO.read_text(encoding="utf-8"))
    assert congelado == ce.generar_contrato(), (
        "El contrato cm-escalacion-v1 cambió: regenera contratos/cm-escalacion-v1.schema.json con "
        "`python -m kallicode_core.cm_escalacion` y anúncialo en contratos/cambios-anunciados.yaml"
    )
    assert ce.RUTA_CONTRATO.read_text(encoding="utf-8") == ce.serializar_contrato()


def test_contrato_es_estricto_y_declara_el_endpoint() -> None:
    contrato = ce.generar_contrato()
    assert contrato["entrada"]["additionalProperties"] is False
    assert contrato["entrada"]["$defs"]["Diagnostico"]["additionalProperties"] is False
    assert contrato["x-endpoint"]["ruta"] == "/internal/v1/codemapping/escalations"
    assert contrato["x-endpoint"]["servicio_jwt"] == "ingesta"
    obligatorios = set(contrato["entrada"]["required"])
    assert {"trace_id", "motivo", "paso", "entrada_ref", "diagnostico"} <= obligatorios


def test_el_cambio_esta_anunciado_para_backend() -> None:
    anunciados = (ce.RUTA_CONTRATO.parent / "cambios-anunciados.yaml").read_text(encoding="utf-8")
    bloque = anunciados.split("- id: KC-CM-10-6-cm-escalacion-v1", 1)[1].split("- id:", 1)[0]
    assert "cm-escalacion-v1" in bloque
    assert "backend: pending" in bloque or "backend: approved" in bloque


def test_el_servicio_del_jwt_esta_permitido_por_la_api() -> None:
    from kallicode_api.deps import SERVICIOS_PERMITIDOS

    assert ce.SERVICIO in SERVICIOS_PERMITIDOS


def test_todo_motivo_tiene_severidad() -> None:
    from typing import get_args

    assert set(get_args(ce.MotivoEscalacion)) == set(ce.SEVERIDAD_POR_MOTIVO)


def test_solicitud_valida_y_clave_de_idempotencia() -> None:
    s = EscalacionIngestaIn.model_validate(_solicitud())
    assert ce.clave_idempotencia(s) == f"cm-esc:org_a:repo_1:{SHA}:credenciales_expiradas"


@pytest.mark.parametrize(
    "cambio",
    [
        {"commit_sha": "NO-ES-SHA"},
        {"commit_sha": "ABCDEF1"},  # el worker lo normaliza a minúsculas antes
        {"motivo": "aburrimiento"},
        {"severidad": "urgente"},
        {"trace_id": ""},
        {"lote": -1},
        {"codigo": "def f(): ..."},  # campos no declarados: nada de código colado
        {"diagnostico": {"tipo_error": "X", "mensaje": "m" * 501, "intentos": 1}},
        {"diagnostico": {"tipo_error": "X", "mensaje": "m", "intentos": 1, "snippet": "x"}},
    ],
)
def test_solicitud_invalida(cambio: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EscalacionIngestaIn.model_validate(_solicitud(**cambio))


def test_respuesta_ignora_campos_nuevos_y_valida_el_estado() -> None:
    r = EscalacionIngestaOut.model_validate(
        {"escalation_id": "esc_1", "estado": "abierta", "asignada_a": "usr_1"}
    )
    assert r.escalation_id == "esc_1"
    with pytest.raises(ValidationError):
        EscalacionIngestaOut.model_validate({"escalation_id": "esc_1", "estado": "perdida"})
