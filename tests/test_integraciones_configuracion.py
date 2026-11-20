"""KC-BE-07-1: registro de proveedores reales y rechazo 503 solo para no implementados."""

import pytest
from kallicode_api.servicios import integraciones
from kallicode_core.errors import AppError


@pytest.mark.parametrize(
    "proveedor",
    ["github", "gitlab", "jira", "github_actions", "postgresql"],
)
def test_cinco_proveedores_reales_registrados(proveedor: str) -> None:
    assert proveedor in integraciones.PROVEEDORES_SOPORTADOS


@pytest.mark.parametrize(
    ("proveedor", "base"), [("github_issues", "github"), ("gitlab_ci", "gitlab")]
)
def test_issues_y_gitlab_ci_usan_el_verificador_de_su_repositorio(
    proveedor: str, base: str
) -> None:
    soportados = integraciones.PROVEEDORES_SOPORTADOS
    assert soportados[proveedor] is soportados[base]


@pytest.mark.parametrize("proveedor", ["bitbucket", "jenkins", "proveedor_desconocido"])
async def test_proveedor_sin_conector_rechazado_con_503(proveedor: str) -> None:
    with pytest.raises(AppError) as error:
        await integraciones.probar(
            proveedor,
            {},
            {"token": "valido"},
        )

    assert error.value.http == 503
    assert error.value.codigo == "DEPENDENCIA_NO_DISPONIBLE"
    assert proveedor in error.value.mensaje
