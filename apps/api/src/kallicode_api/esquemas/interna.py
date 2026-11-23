"""Esquemas de entrada para la API interna /internal/v1."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModeloInterno(BaseModel):
    """Configuración común para cuerpos internos de la API."""

    model_config = ConfigDict(extra="forbid")


CriterioId = Annotated[
    str,
    Field(pattern=r"^AC-\d+$"),
]


# Evidencia de QA tal como la sella Testing (kallicode_testing.evidence_sealer, KC-BE-08-5):
# evidence/{tenant_id}/{job_id}/{criterio_id}/{archivo}. Sin «/» ni «..» dentro de un segmento,
# así la ruta no puede salirse del criterio.
PATRON_BLOB_EVIDENCIA = (
    r"^evidence/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/AC-\d+/[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*$"
)
TipoEvidencia = Literal["screenshot", "video", "trace", "log", "report"]


class EvidenciaRefIn(ModeloInterno):
    """Evidencia inmutable de un criterio: dónde está en Blob y su SHA-256 (KC-BE-08-5)."""

    tipo: TipoEvidencia
    blob_ref: str = Field(pattern=PATRON_BLOB_EVIDENCIA, max_length=500)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def tenant_id(self) -> str:
        return self.blob_ref.split("/")[1]

    @property
    def job_id(self) -> str:
        return self.blob_ref.split("/")[2]

    @property
    def criterio_id(self) -> str:
        return self.blob_ref.split("/")[3]


class CriterioResultadoIn(ModeloInterno):
    """Resultado de un criterio individual de la matriz de QA."""

    criterio_id: str = Field(pattern=r"^AC-\d+$")
    resultado: str = Field(pattern=r"^(pasa|falla|flaky)$")
    extracto_fallo: str | None = Field(
        default=None,
        min_length=1,
        max_length=10_000,
    )
    evidencias: list[EvidenciaRefIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validar_extracto_en_fallo(self) -> CriterioResultadoIn:
        """Garantiza que todo criterio fallido tenga una explicación."""
        if self.resultado == "falla" and not self.extracto_fallo:
            raise ValueError("extracto_fallo es obligatorio cuando resultado es 'falla'.")
        return self

    @model_validator(mode="after")
    def validar_evidencias_del_criterio(self) -> CriterioResultadoIn:
        """Cada evidencia es de este criterio (su ruta lo dice) y aparece una sola vez."""
        ajenas = [e.blob_ref for e in self.evidencias if e.criterio_id != self.criterio_id]
        if ajenas:
            raise ValueError(f"evidencias de otro criterio en {self.criterio_id}: {ajenas}.")
        refs = [e.blob_ref for e in self.evidencias]
        if len(refs) != len(set(refs)):
            raise ValueError(f"evidencias repetidas en {self.criterio_id}.")
        return self


class VeredictoVisualIn(ModeloInterno):
    """Resultado de la inspección visual de un criterio de QA."""

    criterio_id: str = Field(pattern=r"^AC-\d+$")
    veredicto: str = Field(pattern=r"^(pasa|falla)$")
    observado: str = Field(min_length=1, max_length=10_000)
    region: str = Field(min_length=1, max_length=500)


class TicketProactivoIn(ModeloInterno):
    """Datos mínimos para crear un ticket proactivo de seguridad."""

    titulo: str = Field(min_length=1, max_length=240)
    cuerpo: str = Field(min_length=1, max_length=20_000)


class PlanTestIn(ModeloInterno):
    """Paso individual del plan de pruebas de una spec."""

    nombre: str = Field(min_length=1, max_length=240)
    tipo: str = Field(pattern=r"^(unitario|integracion|contrato|e2e)$")
    comando: str = Field(min_length=1, max_length=1_000)
    criterios: list[CriterioId] = Field(min_length=1)


class MigracionIn(ModeloInterno):
    """Paso individual de migración asociado a una spec."""

    archivo: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[0-9]{3}_[a-z0-9_]+\.sql$",
    )
    descripcion: str = Field(min_length=1, max_length=2_000)
    rollback: str = Field(min_length=1, max_length=2_000)


UMBRAL_CONFIANZA_SELECTOR_REPAIR = 0.8
STREAM_SELECTOR_REPAIR = "kc:selector_repair"


class SelectorRepairDetalleIn(ModeloInterno):
    """Detalle tipado para escalaciones con motivo selector_repair (KC-BE-09-5 / KC-QA-09-3)."""

    selector_antiguo: str = Field(min_length=1, max_length=500)
    selector_propuesto: str = Field(min_length=1, max_length=500)
    confianza: float = Field(ge=0.0, le=1.0)
    prueba_afectada: str = Field(min_length=1, max_length=500)
    pr_ref: str | None = Field(default=None, min_length=1, max_length=500)
    pr_id: str | None = Field(default=None, min_length=1, max_length=200)
    branch_name: str | None = Field(default=None, min_length=1, max_length=255)
    commit_sha: str | None = Field(default=None, min_length=7, max_length=64)

    @model_validator(mode="after")
    def validar_pr_segun_umbral(self) -> SelectorRepairDetalleIn:
        """Normaliza la referencia al PR y la exige cuando confianza >= 0.8."""
        if not self.pr_ref and self.pr_id:
            self.pr_ref = self.pr_id
        if self.confianza >= UMBRAL_CONFIANZA_SELECTOR_REPAIR and not self.pr_ref:
            raise ValueError("pr_ref es obligatorio cuando confianza >= 0.8 en selector_repair.")
        return self


class EscalationIn(BaseModel):
    """Cuerpo de POST /internal/v1/escalations."""

    job_id: str
    etapa: str
    paso: str | None = None
    motivo: str = Field(
        pattern=r"^(validacion_persistente|baja_confianza|max_iteraciones|"
        r"selector_repair|anomalia_deploy|otro)$"
    )
    severidad: str = Field(pattern=r"^(baja|media|alta|critica)$")
    detalle: dict[str, Any] | SelectorRepairDetalleIn

    @model_validator(mode="after")
    def validar_detalle_por_motivo(self) -> EscalationIn:
        """Valida estrictamente el esquema de detalle cuando motivo == 'selector_repair'."""
        if self.motivo == "selector_repair" and not isinstance(
            self.detalle, SelectorRepairDetalleIn
        ):
            self.detalle = SelectorRepairDetalleIn.model_validate(self.detalle)
        return self


__all__ = [
    "STREAM_SELECTOR_REPAIR",
    "UMBRAL_CONFIANZA_SELECTOR_REPAIR",
    "CriterioId",
    "CriterioResultadoIn",
    "EscalationIn",
    "EvidenciaRefIn",
    "MigracionIn",
    "PlanTestIn",
    "SelectorRepairDetalleIn",
    "TicketProactivoIn",
    "VeredictoVisualIn",
]
