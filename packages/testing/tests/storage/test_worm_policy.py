"""
Pruebas de certificación de políticas WORM (Write Once Read Many) en Storage Local (Azurite).
Ticket: KC-QA-B4-13.
Certifica:
- Subida de evidencias protegidas con política WORM (x-ms-blob-immutability-policy-mode: Locked).
- Rechazo explícito de intentos de sobrescritura (PUT / overwrite=True) con WORMImmutabilityViolationError (QA-F09, QA-F13).
- Rechazo explícito de intentos de eliminación (DELETE) preservando el contenido íntegro.
- Validación de cadena de custodia criptográfica con hashes SHA-256 (64 hex chars) y HMAC-SHA256 (QA-F14).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from kallicode_testing.evidence_sealer import EvidenceSealer, WORMImmutabilityViolationError
from kallicode_testing.storage_client import QAStorageClient

pytestmark = pytest.mark.asyncio

HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@pytest_asyncio.fixture
async def worm_storage() -> AsyncGenerator[QAStorageClient, None]:
    """Fixture que inicializa QAStorageClient en modo emulador Azurite y garantiza limpieza al cerrar."""
    os.environ["KC_STORAGE_EMULATOR"] = "true"
    storage = QAStorageClient()
    await storage.initialize_buckets()
    try:
        yield storage
    finally:
        await storage.close()


# ---------------------------------------------------------------------------
# Prueba 1: Subida de evidencia con política WORM bloqueada
# ---------------------------------------------------------------------------
async def test_subida_evidencia_con_politica_worm_bloqueada(
    tmp_path: Path,
    worm_storage: QAStorageClient,
) -> None:
    """Valida la subida y sellado exitoso a Azurite con hash SHA-256 de 64 hex chars y modo Locked."""
    sealer = EvidenceSealer(storage_client=worm_storage)

    # Crear evidencia sintética
    evidence_file = tmp_path / "screenshot_failure.png"
    evidence_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR_SYNTHETIC_WORM_EVIDENCE_DATA"
    evidence_file.write_bytes(evidence_content)

    criterion_id = "AC-01"
    tenant_id = "tenant-alpha"
    job_id = f"job-worm1-{uuid.uuid4().hex[:8]}"

    # 1. Sellar la evidencia
    sealed_dto = sealer.seal_file(
        file_path=evidence_file,
        criterion_id=criterion_id,
        file_type="screenshot",
        tenant_id=tenant_id,
        job_id=job_id,
        base_dir=tmp_path,
    )

    # Validar formato canónico del hash SHA-256 (64 caracteres hexadecimales)
    assert len(sealed_dto.sha256_hash) == 64
    assert HEX_SHA256_PATTERN.match(sealed_dto.sha256_hash) is not None
    assert (
        sealed_dto.blob_ref == f"evidence/{tenant_id}/{job_id}/{criterion_id}/{evidence_file.name}"
    )

    # 2. Subir evidencia aplicando política WORM en Azurite
    bucket_name = "evidence-qa"
    blob_url = await sealer.upload_evidence_worm(
        bucket_name=bucket_name,
        blob_ref=sealed_dto.blob_ref,
        data=evidence_content,
    )

    assert blob_url.startswith("http")
    assert bucket_name in blob_url
    assert sealed_dto.blob_ref in blob_url

    # 3. Verificar persistencia física en Azurite
    raw_blob = worm_storage.client.get_blob_client(container=bucket_name, blob=sealed_dto.blob_ref)
    assert await raw_blob.exists() is True

    downloaded = await worm_storage.download_file(bucket_name, sealed_dto.blob_ref)
    assert downloaded == evidence_content


# ---------------------------------------------------------------------------
# Prueba 2: Rechazo categórico de sobrescritura de blob sellado (QA-F09, QA-F13)
# ---------------------------------------------------------------------------
async def test_rechazo_sobrescritura_de_blob_sellado(
    tmp_path: Path,
    worm_storage: QAStorageClient,
) -> None:
    """Valida que cualquier intento de sobrescribir una evidencia sellada sea denegado con 409 Conflict."""
    sealer = EvidenceSealer(storage_client=worm_storage)

    original_file = tmp_path / "original_trace.zip"
    original_data = b"PK\x03\x04_ORIGINAL_IMMUTABLE_PLAYWRIGHT_TRACE_CONTENT"
    original_file.write_bytes(original_data)

    criterion_id = "AC-02"
    tenant_id = "tenant-security"
    job_id = f"job-worm2-{uuid.uuid4().hex[:8]}"

    # Sellado y subida inicial
    sealed_dto = sealer.seal_file(
        file_path=original_file,
        criterion_id=criterion_id,
        file_type="trace",
        tenant_id=tenant_id,
        job_id=job_id,
        base_dir=tmp_path,
    )

    bucket_name = "evidence-qa"
    await sealer.upload_evidence_worm(
        bucket_name=bucket_name,
        blob_ref=sealed_dto.blob_ref,
        data=original_data,
    )

    # Intento 1: Re-sellar el mismo artefacto a nivel de EvidenceSealer (QA-F09, QA-F13)
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict"):
        sealer.seal_file(
            file_path=original_file,
            criterion_id=criterion_id,
            file_type="trace",
            tenant_id=tenant_id,
            job_id=job_id,
            base_dir=tmp_path,
        )

    # Intento 2: Re-subir con upload_evidence_worm al storage
    malicious_data = b"HACKED_CORRUPTED_OR_TAMPERED_DATA"
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict"):
        await sealer.upload_evidence_worm(
            bucket_name=bucket_name,
            blob_ref=sealed_dto.blob_ref,
            data=malicious_data,
        )

    # Intento 3: Intento directo mediante WORMProtectedBlobClient con overwrite=True
    worm_blob_client = worm_storage.get_worm_blob_client(bucket_name, sealed_dto.blob_ref)
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict"):
        await worm_blob_client.upload_blob(malicious_data, overwrite=True)

    # Certificar que el contenido original en Azurite permanece 100% inalterado
    preserved_bytes = await worm_storage.download_file(bucket_name, sealed_dto.blob_ref)
    assert preserved_bytes == original_data
    assert preserved_bytes != malicious_data


# ---------------------------------------------------------------------------
# Prueba 3: Rechazo categórico de eliminación de evidencia WORM (QA-F09, QA-F13)
# ---------------------------------------------------------------------------
async def test_rechazo_eliminacion_de_evidencia_worm(
    tmp_path: Path,
    worm_storage: QAStorageClient,
) -> None:
    """Valida que cualquier intento de eliminar un blob WORM sea rechazado sin alterar el artefacto."""
    sealer = EvidenceSealer(storage_client=worm_storage)

    log_file = tmp_path / "system_exec.log"
    log_bytes = b"2026-11-06T00:00:00Z INFO [kallicode] Execution log for auditing..."
    log_file.write_bytes(log_bytes)

    criterion_id = "AC-03"
    tenant_id = "tenant-audit"
    job_id = f"job-worm3-{uuid.uuid4().hex[:8]}"

    sealed_dto = sealer.seal_file(
        file_path=log_file,
        criterion_id=criterion_id,
        file_type="log",
        tenant_id=tenant_id,
        job_id=job_id,
        base_dir=tmp_path,
    )

    bucket_name = "evidence-qa"
    await sealer.upload_evidence_worm(
        bucket_name=bucket_name,
        blob_ref=sealed_dto.blob_ref,
        data=log_bytes,
    )

    # Intento 1: Eliminación mediante método dedicado de EvidenceSealer
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict.*DELETE"):
        await sealer.delete_evidence_worm(bucket_name, sealed_dto.blob_ref)

    # Intento 2: Eliminación mediante QAStorageClient con enforce_worm=True
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict.*DELETE"):
        await worm_storage.delete_file(bucket_name, sealed_dto.blob_ref)

    # Intento 3: Eliminación mediante WORMProtectedBlobClient.delete_blob()
    worm_blob = worm_storage.get_worm_blob_client(bucket_name, sealed_dto.blob_ref)
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict.*DELETE"):
        await worm_blob.delete_blob()

    # Comprobar que el artefacto persiste intacto en Azurite
    raw_blob = worm_storage.client.get_blob_client(container=bucket_name, blob=sealed_dto.blob_ref)
    assert await raw_blob.exists() is True
    persisted_content = await worm_storage.download_file(bucket_name, sealed_dto.blob_ref)
    assert persisted_content == log_bytes


# ---------------------------------------------------------------------------
# Prueba 4: Verificación de integridad de hashes SHA-256 y HMAC-SHA256 (QA-F14)
# ---------------------------------------------------------------------------
async def test_verificacion_integridad_hash_sha256_y_hmac(
    tmp_path: Path,
    worm_storage: QAStorageClient,
) -> None:
    """Valida la consistencia criptográfica SHA-256 y firma HMAC-SHA256 de SealedEvidencePackageDTO."""
    sealer = EvidenceSealer(storage_client=worm_storage)

    evidence_dir = tmp_path / "evidence_pack"
    evidence_dir.mkdir()

    # Crear múltiples artefactos dentro del paquete
    shot = evidence_dir / "step_1.png"
    shot_bytes = b"\x89PNG\r\n\x1a\n_UI_SCREENSHOT_DATA_CHUNK"
    shot.write_bytes(shot_bytes)

    report = evidence_dir / "summary_report.json"
    report_bytes = b'{"status": "passed", "duration_ms": 1250.0}'
    report.write_bytes(report_bytes)

    job_id = f"job-suite-package-{uuid.uuid4().hex[:8]}"
    tenant_id = "tenant-enterprise"
    spec_id = "checkout.spec.ts"

    # Sellar el paquete completo
    package = sealer.seal_evidence_package(
        job_id=job_id,
        tenant_id=tenant_id,
        spec_id=spec_id,
        evidence_dir=evidence_dir,
    )

    assert package.job_id == job_id
    assert package.tenant_id == tenant_id
    assert len(package.evidence_files) == 2

    # Verificar longitudes de hashes SHA-256 (64 hex chars)
    assert len(package.overall_hash) == 64
    assert HEX_SHA256_PATTERN.match(package.overall_hash) is not None
    assert len(package.hmac_signature) == 64
    assert HEX_SHA256_PATTERN.match(package.hmac_signature) is not None

    bucket_name = "evidence-qa"

    # Subir artefactos a Azurite
    for item in package.evidence_files:
        content = shot_bytes if "step_1.png" in item.blob_ref else report_bytes
        await sealer.upload_evidence_worm(bucket_name, item.blob_ref, content)

    # Descargar cada artefacto de Azurite y recalcular hashes
    for item in package.evidence_files:
        downloaded = await worm_storage.download_file(bucket_name, item.blob_ref)

        # Recalcular SHA-256 por bloques sobre los bytes descargados
        recalculated_hash = sealer.compute_sha256_bytes(downloaded)
        assert recalculated_hash == item.sha256_hash
        assert len(recalculated_hash) == 64

    # Recalcular y validar firma HMAC-SHA256 del paquete completo
    recalculated_signature = sealer.compute_hmac_signature(package.overall_hash)
    assert recalculated_signature == package.hmac_signature
    assert sealer.verify_hmac_signature(package.overall_hash, package.hmac_signature) is True


# ---------------------------------------------------------------------------
# Prueba 5: Detección de manipulación de contenido falla verificación SHA-256
# ---------------------------------------------------------------------------
async def test_deteccion_manipulacion_de_contenido_falla_verificacion_sha256(
    tmp_path: Path,
) -> None:
    """Valida que una alteración de 1 byte sea detectada y rechace la firma HMAC-SHA256."""
    sealer = EvidenceSealer()

    file_a = tmp_path / "valid.txt"
    file_a.write_text("Integrity content check", encoding="utf-8")

    sealed_dto = sealer.seal_file(
        file_path=file_a,
        criterion_id="AC-05",
        file_type="log",
        tenant_id="tenant-sec",
        job_id=f"job-tamper-{uuid.uuid4().hex[:8]}",
        base_dir=tmp_path,
    )

    original_hash = sealed_dto.sha256_hash

    # Bytes alterados
    tampered_bytes = b"Integrity content checK"  # 1 byte cambiado
    tampered_hash = sealer.compute_sha256_bytes(tampered_bytes)

    # El hash debe divergir
    assert tampered_hash != original_hash
    assert len(tampered_hash) == 64

    # La firma HMAC debe rechazar el hash alterado
    signature = sealer.compute_hmac_signature(original_hash)
    assert sealer.verify_hmac_signature(tampered_hash, signature) is False
