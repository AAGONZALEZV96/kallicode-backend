"""Configuración común de las suites de CodeMapping (`test/` y `tests/`) -- KC-CM-12-2.

Registro de cuarentena: una prueba que el CI no ejecuta (SKIPPED o XFAIL) tiene que estar en
`tests/cuarentena.yaml` con su motivo, su ticket y una fecha de resolución. Con
`KC_CM_CUARENTENA_ESTRICTA=1` (lo pone el job de codemapping en CI) se marca como fallida:

- una prueba saltada o xfail que no esté en el registro;
- una prueba del registro cuya fecha de resolución ya pasó.

En local, sin la variable, los skips por falta de PostgreSQL siguen siendo skips. El motivo de
esta regla: hasta KC-CM-12-2, 16 pruebas de integración (la suite RLS de `cm` entre ellas) se
saltaban en CI sin que nadie lo viera, porque apuntaban a una base que en CI no existe.
"""

from __future__ import annotations

import os
import re
from collections.abc import Generator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

# test_codemapping_config prueba que CM_TENANT_ID sea obligatorio: por eso solo se fija el repo.
os.environ.setdefault("CM_REPO_URL", "https://github.com/example/repo.git")

REGISTRO_CUARENTENA = Path(__file__).parent / "tests" / "cuarentena.yaml"
VARIABLE_ESTRICTA = "KC_CM_CUARENTENA_ESTRICTA"
PATRON_TICKET = re.compile(r"^KC-[A-Z]{2,4}-\d{2}-\d+$")
CAMPOS = ("prueba", "motivo", "ticket", "resolver_antes")


@dataclass(frozen=True)
class EntradaCuarentena:
    prueba: str
    motivo: str
    ticket: str
    resolver_antes: date

    def vencida(self, hoy: date) -> bool:
        return hoy > self.resolver_antes


def cargar_registro(ruta: Path) -> dict[str, EntradaCuarentena]:
    """Lee y valida el registro. Un registro mal formado detiene la sesión (UsageError)."""
    if not ruta.exists():
        return {}
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    filas = datos.get("cuarentena") or []
    if not isinstance(filas, list):
        raise pytest.UsageError(f"{ruta.name}: 'cuarentena' debe ser una lista")
    registro: dict[str, EntradaCuarentena] = {}
    for i, fila in enumerate(filas, start=1):
        if not isinstance(fila, dict):
            raise pytest.UsageError(f"{ruta.name}: entrada {i} no es un objeto")
        faltan = [c for c in CAMPOS if not str(fila.get(c) or "").strip()]
        if faltan:
            raise pytest.UsageError(f"{ruta.name}: entrada {i} sin {', '.join(faltan)}")
        ticket = str(fila["ticket"]).strip()
        if not PATRON_TICKET.match(ticket):
            raise pytest.UsageError(f"{ruta.name}: entrada {i} con ticket inválido: {ticket!r}")
        fecha = fila["resolver_antes"]
        if not isinstance(fecha, date):
            try:
                fecha = date.fromisoformat(str(fecha))
            except ValueError as exc:
                raise pytest.UsageError(
                    f"{ruta.name}: entrada {i}: resolver_antes no es una fecha AAAA-MM-DD"
                ) from exc
        prueba = str(fila["prueba"]).strip()
        if prueba in registro:
            raise pytest.UsageError(f"{ruta.name}: {prueba} está repetida")
        registro[prueba] = EntradaCuarentena(prueba, str(fila["motivo"]).strip(), ticket, fecha)
    return registro


def veredicto(nodeid: str, registro: dict[str, EntradaCuarentena], hoy: date) -> str | None:
    """None si la prueba no ejecutada está permitida; si no, el motivo por el que debe fallar."""
    entrada = registro.get(nodeid)
    if entrada is None:
        return (
            f"{nodeid} no se ejecutó y no está en {REGISTRO_CUARENTENA.name}: "
            "arregla la causa del skip o regístrala con motivo, ticket y resolver_antes"
        )
    if entrada.vencida(hoy):
        return (
            f"{nodeid} lleva en cuarentena más allá de {entrada.resolver_antes.isoformat()} "
            f"({entrada.ticket}): resuélvela o renueva la fecha con justificación"
        )
    return None


def _nodeid_desde_paquete(item: pytest.Item) -> str:
    """Nodeid relativo a packages/codemapping, igual en local y en CI sea cual sea el rootdir."""
    ruta = Path(str(item.path)).resolve().relative_to(Path(__file__).parent.resolve())
    sufijo = item.nodeid.split("::", 1)[1] if "::" in item.nodeid else ""
    return f"{ruta.as_posix()}::{sufijo}" if sufijo else ruta.as_posix()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_CLAVE_REGISTRO] = cargar_registro(REGISTRO_CUARENTENA)


_CLAVE_REGISTRO = pytest.StashKey[dict[str, EntradaCuarentena]]()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, Any, None]:
    resultado = yield
    if os.environ.get(VARIABLE_ESTRICTA) != "1":
        return
    informe = resultado.get_result()
    if not informe.skipped:
        return
    # Un skip en setup no pasa por la fase call: se evalúa en la fase en que ocurra.
    problema = veredicto(
        _nodeid_desde_paquete(item), item.config.stash[_CLAVE_REGISTRO], date.today()
    )
    if problema is not None:
        informe.outcome = "failed"
        informe.longrepr = f"[cuarentena] {problema}"
        if hasattr(informe, "wasxfail"):
            del informe.wasxfail
