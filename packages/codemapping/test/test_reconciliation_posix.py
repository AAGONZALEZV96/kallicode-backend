"""Pruebas de unidad para KC-ONB-02-3 (CM-F04: Reconciliación por ID, CM-F16: Rutas POSIX)."""

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

import pytest


def test_reconciliacion_por_id_resiste_desorden_y_omisiones() -> None:
    """CM-F04: Verifica que las descripciones se vinculen por ID exacto y no por orden posicional."""
    # Lista de símbolos originales
    symbols = [
        {"id": "sym_alpha", "file": "auth/service.py", "name": "login"},
        {"id": "sym_beta", "file": "billing/pay.py", "name": "charge"},
        {"id": "sym_gamma", "file": "common/utils.py", "name": "format_date"},
    ]

    # Simulación de respuesta LLM desordenada y con un símbolo faltante
    described_output = [
        {"id": "sym_gamma", "description_en": "Format date helper"},
        {"id": "sym_alpha", "description_en": "Authenticate user credentials"},
    ]

    # Indexar por ID
    described_by_id = {str(d["id"]): d for d in described_output if "id" in d}

    # Reconciliación
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sym in symbols:
        desc = described_by_id.get(sym["id"])
        if desc:
            posix_path = PurePosixPath(sym["file"].replace("\\", "/"))
            modulo = posix_path.parts[0] if posix_path.parts else "root"
            by_module[modulo].append(desc)

    # Validaciones
    assert "sym_beta" not in described_by_id
    assert len(by_module["auth"]) == 1
    assert by_module["auth"][0]["description_en"] == "Authenticate user credentials"
    assert len(by_module["common"]) == 1
    assert by_module["common"][0]["description_en"] == "Format date helper"
    assert "billing" not in by_module


@pytest.mark.parametrize(
    ("raw_path", "expected_module"),
    [
        ("packages/codemapping/src/main.py", "packages"),
        ("packages\\codemapping\\src\\main.py", "packages"),
        ("auth/login.py", "auth"),
        ("auth\\login.py", "auth"),
        ("root_file.py", "root_file.py"),
    ],
)
def test_normalizacion_rutas_posix_independiente_de_os(raw_path: str, expected_module: str) -> None:
    """CM-F16: Valida que rutas estilo Windows y Linux extraigan idéntico módulo raíz."""
    posix_path = PurePosixPath(raw_path.replace("\\", "/"))
    modulo = posix_path.parts[0] if posix_path.parts else "root"
    assert modulo == expected_module
