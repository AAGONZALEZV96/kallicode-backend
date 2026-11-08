from pathlib import Path

import yaml
from kallicode_api.contrato_openapi import enriquecer_contrato
from kallicode_api.main import app as app_publica
from kallicode_api.main_interna import app as app_interna

ROOT = Path(__file__).resolve().parents[3]

CONTRATOS = {
    "/api/v1": (
        app_publica,
        ROOT / "contratos/api-v1.yaml",
    ),
    "/internal/v1": (
        app_interna,
        ROOT / "contratos/internal-v1.yaml",
    ),
}


def test_contratos_congelados_coinciden_con_runtime() -> None:
    for prefijo, (aplicacion, ruta) in CONTRATOS.items():
        assert ruta.exists(), f"No existe el contrato: {ruta}"

        with ruta.open(encoding="utf-8") as archivo:
            contrato_publicado = yaml.safe_load(archivo)

        contrato_actual = enriquecer_contrato(
            aplicacion.openapi(),
            prefijo,
        )

        assert contrato_publicado == contrato_actual, (
            f"{ruta} no coincide con el esquema actual. "
            "Regenera los contratos ejecutando "
            "docs/congelar_contrato.py."
        )