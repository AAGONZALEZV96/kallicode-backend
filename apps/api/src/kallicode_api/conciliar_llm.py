"""Compara el coste del pipeline con un CSV de facturación del proveedor."""

from __future__ import annotations

import argparse
import asyncio
import csv
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from kallicode_core.llm.tiers import Tier

from .metricas import consultar_consumo_pipeline

_UMBRAL = Decimal("0.10")


def desviacion(estimado: Decimal, proveedor: Decimal) -> Decimal:
    if proveedor == 0:
        return Decimal(0) if estimado == 0 else Decimal("Infinity")
    return abs(estimado - proveedor) / proveedor


def conciliar(filas: Iterable[dict], proveedor: dict[Tier, Decimal]) -> bool:
    costes = dict.fromkeys(Tier, Decimal(0))
    for fila in filas:
        if fila["coste"] is None:
            raise ValueError(f"Sin tarifa para el tier {fila['tier']}.")
        costes[Tier(fila["tier"])] += fila["coste"]

    # Compara cada tier y el total. Un exceso y un defecto de tiers distintos
    # pueden cancelarse en el total y ocultar una tarifa o atribución incorrecta.
    montos = {tier.value: (costes[tier], proveedor[tier]) for tier in Tier}
    montos["total"] = (sum(costes.values()), sum(proveedor.values()))
    valido = True
    for tier, (estimado, real) in montos.items():
        diferencia = desviacion(estimado, real)
        print(
            f"{tier}: estimado USD {estimado:.6f} | proveedor USD {real:.6f} | desviación {diferencia:.2%}"
        )
        valido &= diferencia < _UMBRAL
    return valido


def _leer_csv(ruta: Path) -> dict[Tier, Decimal]:
    resultado = {}
    with ruta.open(encoding="utf-8", newline="") as archivo:
        for fila in csv.DictReader(archivo):
            tier = Tier(fila["tier"])
            importe = Decimal(fila["coste_usd"])
            if tier in resultado or not importe.is_finite() or importe < 0:
                raise ValueError(f"Coste duplicado o inválido para {tier}.")
            resultado[tier] = importe
    if resultado.keys() != set(Tier):
        raise ValueError("El CSV debe incluir flash, pro y fable, con cero si no hubo consumo.")
    return resultado


def _instante(valor: str) -> datetime:
    instante = datetime.fromisoformat(valor)
    if instante.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "Incluye la zona horaria, por ejemplo 2026-11-01T00:00:00Z."
        )
    return instante.astimezone(UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Informe normalizado con columnas tier,coste_usd")
    parser.add_argument("--desde", required=True, type=_instante)
    parser.add_argument("--hasta", required=True, type=_instante, help="Fin exclusivo")
    args = parser.parse_args()
    if args.desde >= args.hasta:
        parser.error("--desde debe ser anterior a --hasta.")
    try:
        proveedor = _leer_csv(args.csv)
        filas = asyncio.run(consultar_consumo_pipeline(args.desde, args.hasta))
        return int(not conciliar(filas, proveedor))
    except (OSError, KeyError, ValueError, InvalidOperation) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
