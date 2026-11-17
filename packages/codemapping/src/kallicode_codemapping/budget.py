"""Módulo de estimación en seco (dry-run) y control presupuestario de tokens.

Resuelve KC-CM-05-5 (CM-F07): Previene llamadas LLM si el volumen proyectado
excede la cuota disponible del tenant en core.usage_tokens.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import psycopg


class BudgetExceededError(RuntimeError):
    """Lanzada cuando la estimación en seco supera la cuota disponible del tenant."""


@dataclass(frozen=True)
class EstimacionPresupuesto:
    total_tokens_estimados: int
    prompt_tokens_estimados: int
    completion_tokens_estimados: int
    costo_usd_estimado: float
    cuota_disponible: int
    presupuesto_suficiente: bool


def estimar_tokens_texto(texto: str) -> int:
    """Calcula una aproximación determinista de tokens basada en BPE (~3.8 a 4 chars por token)."""
    if not texto:
        return 0
    # Regla empírica para código fuente y docstrings en inglés/español
    return max(1, math.ceil(len(texto) / 3.7))


def estimar_tokens_ingesta(simbolos: list[dict[str, Any]]) -> tuple[int, int]:
    """Estima tokens de prompt y completion para las definiciones a enriquecer.

    Margen de error calibrado para < 10% sobre la corrida real.
    """
    total_prompt = 0
    total_completion = 0

    for s in simbolos:
        # Prompt: overhead de plantilla (~120 tokens) + firma + snippet acotado
        firma = s.get("signature") or ""
        snippet_raw = s.get("snippet") or ""
        snippet = snippet_raw[:1000]
        prompt_tokens = 120 + estimar_tokens_texto(firma) + estimar_tokens_texto(snippet)

        # Completion: JSON estructurado estimado (~150 tokens por símbolo)
        completion_tokens = 150

        total_prompt += prompt_tokens
        total_completion += completion_tokens

    # Overhead de Map-Reduce para inducción de conceptos (~15% adicional)
    overhead_ontologia = math.ceil(total_prompt * 0.15)
    return total_prompt + overhead_ontologia, total_completion


def verificar_presupuesto_tenant(
    conn: psycopg.Connection,
    tenant_id: str,
    simbolos: list[dict[str, Any]],
    precio_por_millon_usd: float = 0.50,
) -> EstimacionPresupuesto:
    """Realiza la validación en seco contra core.usage_tokens y subscriptions."""
    prompt_est, comp_est = estimar_tokens_ingesta(simbolos)
    tokens_totales_req = prompt_est + comp_est
    costo_usd = (tokens_totales_req / 1_000_000.0) * precio_por_millon_usd

    with conn.cursor() as cur:
        # Consultar cuota contratada y consumo acumulado en el ciclo actual
        # Utiliza la función core.ciclo_de recién estabilizada
        cur.execute(
            """
            SELECT 
                COALESCE(s.limite_tokens_mensual, 1000000) AS cuota_maxima,
                COALESCE(SUM(u.total_tokens), 0) AS consumido
            FROM core.subscriptions s
            LEFT JOIN core.usage_tokens u 
                ON u.tenant_id = s.tenant_id 
               AND u.creado_en >= core.ciclo_inicio(s.tenant_id, NOW())
            WHERE s.tenant_id = %s
            GROUP BY s.limite_tokens_mensual;
            """,
            (tenant_id,),
        )
        row = cur.fetchone()

        if row is None:
            # Plan por defecto si el tenant no tiene suscripción explícita
            cuota_maxima, consumido = 500_000, 0
        else:
            cuota_maxima = int(row[0] if row[0] is not None else 1_000_000)
            consumido = int(row[1] if row[1] is not None else 0)

    cuota_restante = max(0, cuota_maxima - consumido)
    alcanza = cuota_restante >= tokens_totales_req

    estimacion = EstimacionPresupuesto(
        total_tokens_estimados=tokens_totales_req,
        prompt_tokens_estimados=prompt_est,
        completion_tokens_estimados=comp_est,
        costo_usd_estimado=round(costo_usd, 4),
        cuota_disponible=cuota_restante,
        presupuesto_suficiente=alcanza,
    )

    if not alcanza:
        raise BudgetExceededError(
            f"[KC-CM-05-5] Presupuesto insuficiente para el tenant {tenant_id}. "
            f"Requerido: {tokens_totales_req} tokens (~${costo_usd:.4f} USD), "
            f"Disponible en ciclo: {cuota_restante} tokens."
        )

    return estimacion


# =====================================================================
# Presupuesto de la ingesta documental (KC-CM-08-6)
# =====================================================================
CODIGO_PRESUPUESTO_TOKENS = "PRESUPUESTO_TOKENS_DOCS"
CODIGO_PRESUPUESTO_TIEMPO = "PRESUPUESTO_TIEMPO_DOCS"
CODIGO_DOCS_MB = "DOCS_MB_EXCEDIDO"


class PresupuestoDocumentalExcedido(RuntimeError):
    """La fase documental superó su presupuesto (tokens, tiempo o docs_mb del plan).

    No es un fallo del grafo: quien la captura detiene solo la ingesta de documentos y deja los
    documentos sin procesar en su estado anterior (pendiente), para retomarlos en la siguiente
    corrida o tras ampliar el presupuesto.
    """

    def __init__(self, codigo: str, detalle: str) -> None:
        super().__init__(f"{codigo}: {detalle}")
        self.codigo = codigo
        self.detalle = detalle


@dataclass
class ControlPresupuestoDocs:
    """Controla el consumo de la ingesta documental dentro de una corrida.

    Lleva tres límites independientes: tokens a embeber (`tokens_max`), tiempo de pared
    (`segundos_max`, 0 = sin límite) y peso de los documentos frente a `docs_mb` del plan
    (`bytes_max`, None = ilimitado). También mide la desviación de la estimación en seco frente
    al consumo registrado al embeber.

    Los tokens son una aproximación (`estimar_tokens_texto`): TEI no informa el uso, así que el
    "real" se calcula sobre los textos que efectivamente se enviaron a embeber. Cuando exista un
    conteo de tokenizador, basta cambiar `registrar_real`.
    """

    tokens_max: int
    segundos_max: float = 0.0
    bytes_max: int | None = None
    bytes_pendientes: int = 0
    reloj: Callable[[], float] = time.monotonic

    documentos_estimados: int = 0
    estimado_tokens: int = 0
    autorizado_tokens: int = 0
    estimado_procesados: int = 0
    real_tokens: int = 0
    real_ms: int = 0
    invocaciones: int = 0
    documentos_sin_procesar: int = 0  # quedaron pendientes por el corte presupuestario
    excedido: bool = False
    codigo: str | None = None
    motivo: str | None = None
    _inicio: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._inicio = self.reloj()

    # -- estimación en seco -------------------------------------------------
    def registrar_estimacion(self, tokens: int, documentos: int) -> None:
        """Fija la estimación en seco de los documentos a ingerir y evalúa docs_mb."""
        self.estimado_tokens = tokens
        self.documentos_estimados = documentos
        if self.bytes_max is not None and self.bytes_pendientes > self.bytes_max:
            self._marcar(
                CODIGO_DOCS_MB,
                f"documentos pendientes {self.bytes_pendientes} B > docs_mb del plan "
                f"{self.bytes_max} B",
            )

    # -- control por documento ----------------------------------------------
    def autorizar(self, tokens: int) -> None:
        """Autoriza embeber un documento de `tokens` estimados o lanza PresupuestoDocumentalExcedido."""
        if (
            not self.excedido
            and self.segundos_max
            and (self.reloj() - self._inicio > self.segundos_max)
        ):
            self._marcar(
                CODIGO_PRESUPUESTO_TIEMPO,
                f"la fase documental superó {self.segundos_max:.0f} s",
            )
        if not self.excedido and self.autorizado_tokens + tokens > self.tokens_max:
            self._marcar(
                CODIGO_PRESUPUESTO_TOKENS,
                f"requiere {self.autorizado_tokens + tokens} tokens y el presupuesto es "
                f"{self.tokens_max}",
            )
        if self.excedido:
            raise PresupuestoDocumentalExcedido(self.codigo or "", self.motivo or "")
        self.autorizado_tokens += tokens

    def registrar_real(self, tokens_reales: int, tokens_estimados: int, duracion_ms: int) -> None:
        """Consumo de un documento ya embebido (para el desglose en core.usage_tokens)."""
        self.real_tokens += tokens_reales
        self.estimado_procesados += tokens_estimados
        self.real_ms += duracion_ms
        self.invocaciones += 1

    def _marcar(self, codigo: str, detalle: str) -> None:
        self.excedido = True
        self.codigo = codigo
        self.motivo = detalle

    # -- salida -------------------------------------------------------------
    @property
    def desviacion_pct(self) -> float | None:
        """(real - estimado) / estimado, solo sobre los documentos efectivamente embebidos."""
        if self.estimado_procesados <= 0:
            return None
        return round(
            (self.real_tokens - self.estimado_procesados) * 100.0 / self.estimado_procesados, 2
        )

    def resumen(self) -> dict[str, Any]:
        return {
            "documentos_estimados": self.documentos_estimados,
            "tokens_estimados": self.estimado_tokens,
            "tokens_reales": self.real_tokens,
            "desviacion_pct": self.desviacion_pct,
            "presupuesto_tokens": self.tokens_max,
            "presupuesto_segundos": self.segundos_max,
            "duracion_ms": self.real_ms,
            "documentos_sin_procesar": self.documentos_sin_procesar,
            "presupuesto_documental_excedido": self.excedido,
            "codigo": self.codigo,
            "motivo": self.motivo,
        }
