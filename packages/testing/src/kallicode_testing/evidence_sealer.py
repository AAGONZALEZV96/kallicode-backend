"""
Ticket: KC-QA-B4-02
Generación y Sellado Hash de Evidencias Inmutables (SHA-256 / WORM).
Implementa:
- Cálculo determinista de digest SHA-256 por bloques de 64 KB.
- Firma digital HMAC-SHA256 para integridad de la suite.
- Empaquetamiento determinista por criterio BDD (AC-n).
- Política de almacenamiento inmutable WORM (Write Once Read Many).
- Prevención de Path Traversal mediante resolve_safe_path (QA-F03).
- Cierre definitivo de los hallazgos QA-F09, QA-F13 y QA-F14.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from kallicode_testing.logger import get_logger
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

EvidenceFileType = Literal["screenshot", "video", "trace", "log", "report"]


# ---------------------------------------------------------------------------
# Excepciones de Dominio
# ---------------------------------------------------------------------------
class WORMImmutabilityViolationError(Exception):
    """Lanzada cuando se intenta sobrescribir o alterar una evidencia protegida por WORM."""


# ---------------------------------------------------------------------------
# DTOs Oficiales KC-QA-B4-02
# ---------------------------------------------------------------------------
class EvidenceFileDTO(BaseModel):
    """Representa un archivo de evidencia sellado criptográficamente de forma inmutable."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    criterion_id: str
    file_type: EvidenceFileType
    file_path: str
    sha256_hash: str
    blob_ref: str
    created_at: str


class SealedEvidencePackageDTO(BaseModel):
    """Paquete sellado y firmado criptográficamente de todas las evidencias de la suite."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    job_id: str
    tenant_id: str
    spec_id: str
    overall_hash: str
    hmac_signature: str
    evidence_files: list[EvidenceFileDTO]


# ---------------------------------------------------------------------------
# Clase Principal EvidenceSealer
# ---------------------------------------------------------------------------
class EvidenceSealer:
    """
    Sellador criptográfico de evidencias con soporte para WORM,
    procesamiento de archivos por bloques de 64 KB y firmas HMAC-SHA256.
    """

    def __init__(
        self,
        storage_client: Any | None = None,
        hmac_key: str | None = None,
    ) -> None:
        self.storage_client = storage_client
        self.hmac_key = hmac_key or os.getenv(
            "EVIDENCE_HMAC_SECRET", "kallicode_default_hmac_secret_2026"
        )
        self._worm_sealed_refs: set[str] = set()

    def compute_sha256(self, file_path: Path) -> str:
        """
        Calcula el hash SHA-256 de forma determinista leyendo el archivo en bloques
        de 64 KB (65536 bytes) para optimizar memoria en videos y traces grandes.
        """
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def compute_sha256_bytes(self, data: bytes) -> str:
        """Calcula el digest SHA-256 en bloques de 64 KB a partir de bytes en memoria."""
        hasher = hashlib.sha256()
        chunk_size = 65536
        for offset in range(0, len(data), chunk_size):
            hasher.update(data[offset : offset + chunk_size])
        return hasher.hexdigest()

    def infer_file_type(self, path: Path) -> EvidenceFileType:
        """Infiere automáticamente el tipo de archivo de evidencia a partir de su extensión."""
        ext = path.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg"):
            return "screenshot"
        if ext in (".webm", ".mp4"):
            return "video"
        if ext in (".zip",):
            return "trace"
        if ext in (".log", ".txt"):
            return "log"
        return "report"

    def compute_hmac_signature(self, message: str, key: str | None = None) -> str:
        """Calcula una firma HMAC-SHA256 sobre el mensaje proporcionado."""
        secret = key or self.hmac_key
        return hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_hmac_signature(self, message: str, signature: str, key: str | None = None) -> bool:
        """Verifica en tiempo constante que una firma HMAC-SHA256 coincida."""
        expected = self.compute_hmac_signature(message, key)
        return hmac.compare_digest(expected, signature)

    def seal_file(
        self,
        file_path: Path,
        criterion_id: str,
        file_type: str,
        tenant_id: str,
        job_id: str,
        base_dir: Path | None = None,
    ) -> EvidenceFileDTO:
        """
        Sella un archivo individual de evidencia:
        - Valida confinamiento de ruta contra Path Traversal con resolve_safe_path (QA-F03).
        - Genera blob_ref canónico: evidence/{tenant_id}/{job_id}/{criterion_id}/{filename}.
        - Aplica política de inmutabilidad WORM: rechaza re-sellados o sobrescrituras (QA-F09, QA-F13).
        - Calcula hash SHA-256 en bloques de 64 KB.
        """
        anchor = base_dir or file_path.parent
        # resolve_safe_path previene cualquier escape o Path Traversal fuera de anchor
        safe_file = resolve_safe_path(anchor, file_path)

        if not safe_file.exists():
            raise FileNotFoundError(f"Archivo de evidencia no encontrado en disco: {safe_file}")

        # Validación de tipo de archivo
        valid_types: set[str] = {"screenshot", "video", "trace", "log", "report"}
        cleaned_type = file_type.lower().strip()
        if cleaned_type not in valid_types:
            raise ValueError(
                f"Tipo de archivo inválido '{file_type}'. Debe ser uno de {sorted(valid_types)}."
            )
        typed_kind = cast(EvidenceFileType, cleaned_type)

        # Generar blob_ref canónico
        blob_ref = f"evidence/{tenant_id}/{job_id}/{criterion_id}/{safe_file.name}"

        # Control WORM: Rechazo inmediato de sobrescritura
        if blob_ref in self._worm_sealed_refs:
            logger.error(
                "evidence_sealer.worm_violation_detected",
                blob_ref=blob_ref,
                tenant_id=tenant_id,
                criterion_id=criterion_id,
            )
            raise WORMImmutabilityViolationError(
                f"409 Conflict: Operación rechazada por política WORM. "
                f"El artefacto '{blob_ref}' ya fue sellado y es estrictamente inmutable."
            )

        # Cálculo de digest SHA-256
        sha256_hash = self.compute_sha256(safe_file)
        created_at = datetime.now(UTC).isoformat()

        # Registrar referencia en WORM
        self._worm_sealed_refs.add(blob_ref)

        logger.info(
            "evidence_sealer.file_sealed",
            criterion_id=criterion_id,
            file_type=typed_kind,
            blob_ref=blob_ref,
            sha256_prefix=sha256_hash[:8],
        )

        return EvidenceFileDTO(
            criterion_id=criterion_id,
            file_type=typed_kind,
            file_path=str(safe_file),
            sha256_hash=sha256_hash,
            blob_ref=blob_ref,
            created_at=created_at,
        )

    def package_and_seal_suite_evidence(
        self,
        job_id: str,
        tenant_id: str,
        spec_id: str,
        evidence_dir: Path,
        criteria_mapping: dict[str, list[Path]],
    ) -> SealedEvidencePackageDTO:
        """
        Recorre y sella todas las evidencias de una corrida de pruebas por criterio BDD,
        calcula el hash consolidado de la suite y genera la firma HMAC-SHA256 de integridad.
        """
        sealed_files: list[EvidenceFileDTO] = []

        # Ordenar criterios para garantizar determinismo en el hash global
        sorted_criteria = sorted(criteria_mapping.keys())

        for criterion_id in sorted_criteria:
            paths = criteria_mapping[criterion_id]
            # Ordenar archivos dentro de cada criterio
            sorted_paths = sorted(paths, key=lambda p: p.name)

            for file_path in sorted_paths:
                file_type = self.infer_file_type(file_path)
                sealed_dto = self.seal_file(
                    file_path=file_path,
                    criterion_id=criterion_id,
                    file_type=file_type,
                    tenant_id=tenant_id,
                    job_id=job_id,
                    base_dir=evidence_dir,
                )
                sealed_files.append(sealed_dto)

        # overall_hash: SHA-256 calculado sobre la combinación ordenada de hashes individuales
        if sealed_files:
            ordered_hashes = [
                f"{f.criterion_id}:{f.blob_ref}:{f.sha256_hash}" for f in sealed_files
            ]
            combined_string = "|".join(ordered_hashes)
            overall_hash = hashlib.sha256(combined_string.encode("utf-8")).hexdigest()
        else:
            # Hash determinista de conjunto vacío
            overall_hash = hashlib.sha256(b"EMPTY_EVIDENCE_PACKAGE").hexdigest()

        # Firma HMAC-SHA256
        hmac_sig = self.compute_hmac_signature(overall_hash)

        logger.info(
            "evidence_sealer.package_sealed",
            job_id=job_id,
            spec_id=spec_id,
            total_files=len(sealed_files),
            overall_hash_prefix=overall_hash[:8],
        )

        return SealedEvidencePackageDTO(
            job_id=job_id,
            tenant_id=tenant_id,
            spec_id=spec_id,
            overall_hash=overall_hash,
            hmac_signature=hmac_sig,
            evidence_files=sealed_files,
        )

    def seal_evidence_package(
        self,
        job_id: str,
        tenant_id: str,
        spec_id: str,
        evidence_dir: Path,
        criteria_mapping: dict[str, list[Path]] | None = None,
    ) -> SealedEvidencePackageDTO:
        """
        Alias conveniente para empaquetar y sellar todas las evidencias de una suite.
        Si criteria_mapping no es provisto, agrupa los archivos encontrados bajo el criterio default 'AC-01'.
        """
        mapping = criteria_mapping
        if mapping is None:
            files = sorted(
                [p for p in evidence_dir.rglob("*") if p.is_file()], key=lambda p: p.name
            )
            mapping = {"AC-01": files}

        return self.package_and_seal_suite_evidence(
            job_id=job_id,
            tenant_id=tenant_id,
            spec_id=spec_id,
            evidence_dir=evidence_dir,
            criteria_mapping=mapping,
        )

    async def upload_evidence_worm(
        self,
        bucket_name: str,
        blob_ref: str,
        data: bytes,
    ) -> str:
        """
        Sube la evidencia al cliente de almacenamiento emulado (Azurite/Blob)
        aplicando la política WORM (encabezado x-ms-blob-immutability-policy-mode: Locked).
        Rechaza con WORMImmutabilityViolationError ante intentos de sobrescritura.
        """
        if self.storage_client is None:
            return f"blob://{bucket_name}/{blob_ref}"

        # Si el cliente es QAStorageClient o cliente compatible de Azure
        try:
            if hasattr(self.storage_client, "client"):
                blob_client = self.storage_client.client.get_blob_client(
                    container=bucket_name, blob=blob_ref
                )
                if await blob_client.exists():
                    raise WORMImmutabilityViolationError(
                        f"409 Conflict: El blob '{blob_ref}' ya existe en el bucket '{bucket_name}' "
                        f"y está protegido por política WORM."
                    )
                try:
                    await blob_client.upload_blob(
                        data,
                        overwrite=False,
                        immutability_policy_mode="Locked",
                    )
                except TypeError:
                    from azure.storage.blob import ImmutabilityPolicy

                    await blob_client.upload_blob(
                        data,
                        overwrite=False,
                        immutability_policy=ImmutabilityPolicy(policy_mode="Locked"),
                        headers={"x-ms-blob-immutability-policy-mode": "Locked"},
                    )
                url = str(blob_client.url)
            elif hasattr(self.storage_client, "upload_file"):
                # Delegar al cliente de almacenamiento
                url = str(await self.storage_client.upload_file(bucket_name, blob_ref, data))
            else:
                url = f"blob://{bucket_name}/{blob_ref}"
        except Exception as exc:
            if isinstance(exc, WORMImmutabilityViolationError):
                raise
            err_msg = str(exc).lower()
            if (
                "already exists" in err_msg
                or "conflict" in err_msg
                or "409" in err_msg
                or "412" in err_msg
            ):
                raise WORMImmutabilityViolationError(
                    f"409 Conflict: Operación de almacenamiento rechazada por política WORM: {exc}"
                ) from exc
            raise

        return url

    async def delete_evidence_worm(self, bucket_name: str, blob_ref: str) -> None:
        """
        Rechaza categóricamente cualquier intento de eliminación sobre un artefacto WORM.
        Aplica política de inmutabilidad estricta (QA-F09, QA-F13, QA-F14).
        """
        logger.error(
            "evidence_sealer.worm_delete_attempt_rejected",
            bucket_name=bucket_name,
            blob_ref=blob_ref,
        )
        raise WORMImmutabilityViolationError(
            f"409 Conflict: Operación DELETE rechazada por política WORM. "
            f"El artefacto '{blob_ref}' en '{bucket_name}' es estrictamente inmutable y no puede eliminarse."
        )
