"""Cache local de fases costosas del pipeline (KC-ONB-03-2).

`cm.repo_snapshots.fase_actual` en Postgres es el registro auditable de
hasta dónde llegó una corrida, pero por sí solo no permite *reanudar* nada:
para saltarse el parseo o las llamadas al LLM en el reintento hace falta el
resultado de esas fases, no solo saber que "ya pasaron". Ese resultado vive
en memoria durante la corrida, así que se cachea en disco junto al `workdir`
del repo clonado, bajo el id del snapshot.

Si el cache no existe (se borró, se movió de máquina, primera corrida), la
fase simplemente se recalcula -- nunca es un error, solo pierde el ahorro.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast


def _directorio(workdir: str, tenant_id: str, snapshot_id: str) -> Path:
    return Path(workdir) / tenant_id / "checkpoints" / snapshot_id


def guardar(
    workdir: str, tenant_id: str, snapshot_id: str, fase: str, datos: dict[str, Any]
) -> None:
    d = _directorio(workdir, tenant_id, snapshot_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fase}.json").write_text(json.dumps(datos), encoding="utf-8")


def cargar(workdir: str, tenant_id: str, snapshot_id: str, fase: str) -> dict[str, Any] | None:
    ruta = _directorio(workdir, tenant_id, snapshot_id) / f"{fase}.json"
    if not ruta.exists():
        return None
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    return datos if isinstance(datos, dict) else None


def limpiar(workdir: str, tenant_id: str, snapshot_id: str) -> None:
    """Se llama al completar el snapshot con éxito: el cache ya no hace falta."""
    shutil.rmtree(_directorio(workdir, tenant_id, snapshot_id), ignore_errors=True)
