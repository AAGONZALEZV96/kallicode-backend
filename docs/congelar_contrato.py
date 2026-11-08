"""Genera los contratos desde sus aplicaciones correspondientes."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api" / "src"
CORE_SRC = ROOT / "packages" / "core" / "src"
CONTRATOS_DIR = ROOT / "contratos"

sys.path.insert(0, str(API_SRC))
sys.path.insert(0, str(CORE_SRC))

from kallicode_api.contrato_openapi import enriquecer_contrato
from kallicode_api.main import app as app_publica
from kallicode_api.main_interna import app as app_interna

CONTRATOS = {
    "/api/v1": (
        app_publica,
        CONTRATOS_DIR / "api-v1.yaml",
    ),
    "/internal/v1": (
        app_interna,
        CONTRATOS_DIR / "internal-v1.yaml",
    ),
}


def guardar_yaml(
    destino: Path,
    contrato: dict[str, Any],
) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)

    with destino.open("w", encoding="utf-8") as archivo:
        yaml.safe_dump(
            contrato,
            archivo,
            allow_unicode=True,
            sort_keys=True,
            default_flow_style=False,
        )


def congelar() -> None:
    for prefijo, (aplicacion, destino) in CONTRATOS.items():
        contrato = enriquecer_contrato(
            aplicacion.openapi(),
            prefijo,
        )

        if not contrato.get("paths"):
            raise RuntimeError(f"No se encontraron endpoints para {prefijo!r}.")

        guardar_yaml(destino, contrato)
        print(f"Contrato generado: {destino.relative_to(ROOT)}")


if __name__ == "__main__":
    congelar()
