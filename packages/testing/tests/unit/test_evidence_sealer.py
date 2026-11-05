"""
Ticket: KC-QA-B4-02
Suite de pruebas unitarias y de resiliencia para EvidenceSealer (evidence_sealer.py).
Valida:
1. test_calculo_sha256_y_sellado_archivo_independiente: Digest exacto de 64 chars vs hashlib.
2. test_empaquetado_completo_evidencias_suite: Empaquetado por criterio BDD con overall_hash y firma HMAC-SHA256.
3. test_prevencion_sobrescritura_worm_inmutabilidad: Bloqueo estricto WORM ante re-sellado (QA-F09, QA-F13, QA-F14).
4. test_prevencion_path_traversal_al_sellar_archivos: Confinamiento seguro con resolve_safe_path (QA-F03).
5. test_procesamiento_en_bloques_archivos_grandes: Lectura en chunks de 64 KB sin desbordar memoria.
6. test_verificacion_integridad_hmac_tamper_detection: Detección de manipulaciones en el paquete sellado.
7. test_upload_evidence_worm_almacenamiento: Rechazo de sobrescritura en capa de almacenamiento.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from kallicode_testing.evidence_sealer import (
    EvidenceSealer,
    WORMImmutabilityViolationError,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Prueba 1: test_calculo_sha256_y_sellado_archivo_independiente
# ---------------------------------------------------------------------------
async def test_calculo_sha256_y_sellado_archivo_independiente(tmp_path: Path) -> None:
    """
    Prueba 1: Crear archivo sintético y verificar que compute_sha256
    produzca un hash idéntico al cálculo directo de hashlib.sha256.
    Validaciones:
    - Longitud exacta de 64 caracteres hexadecimales.
    - Coincidencia bit a bit.
    - Generación correcta del DTO EvidenceFileDTO.
    """
    file_path = tmp_path / "screenshot_test.png"
    binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"random_data_pixels" * 50)
    file_path.write_bytes(binary_content)

    expected_hash = hashlib.sha256(binary_content).hexdigest()

    sealer = EvidenceSealer()
    computed_hash = sealer.compute_sha256(file_path)

    assert computed_hash == expected_hash
    assert len(computed_hash) == 64
    assert computed_hash.isalnum()

    # Sellado individual
    evidence_dto = sealer.seal_file(
        file_path=file_path,
        criterion_id="AC-01",
        file_type="screenshot",
        tenant_id="tenant_sealer_01",
        job_id="job_sealer_01",
        base_dir=tmp_path,
    )

    assert evidence_dto.criterion_id == "AC-01"
    assert evidence_dto.file_type == "screenshot"
    assert evidence_dto.sha256_hash == expected_hash
    assert (
        evidence_dto.blob_ref == "evidence/tenant_sealer_01/job_sealer_01/AC-01/screenshot_test.png"
    )
    assert "T" in evidence_dto.created_at


# ---------------------------------------------------------------------------
# Prueba 2: test_empaquetado_completo_evidencias_suite
# ---------------------------------------------------------------------------
async def test_empaquetado_completo_evidencias_suite(tmp_path: Path) -> None:
    """
    Prueba 2: Simular un directorio con múltiples artefactos asociados
    a criterios BDD (AC-01 y AC-02).
    Validaciones:
    - Retorna SealedEvidencePackageDTO con lista completa de evidencias.
    - Inferencia correcta de tipos (screenshot, video, trace, log, report).
    - overall_hash determinista y no vacío.
    - Firma HMAC-SHA256 verificable con la clave secreta.
    """
    evidence_dir = tmp_path / "suite_evidence"
    evidence_dir.mkdir()

    # AC-01: Screenshot y Log
    ac1_img = evidence_dir / "login_success.png"
    ac1_img.write_bytes(b"PNG_PIXELS_SUCCESS")

    ac1_log = evidence_dir / "httpx_network.log"
    ac1_log.write_text("POST /api/v1/auth/login 200 OK", encoding="utf-8")

    # AC-02: Video Playwright y Trace ZIP
    ac2_video = evidence_dir / "checkout_flow.webm"
    ac2_video.write_bytes(b"WEBM_VIDEO_FRAMES_DATA")

    ac2_trace = evidence_dir / "playwright_trace.zip"
    ac2_trace.write_bytes(b"PK\x03\x04ZIP_TRACE_ARCHIVE")

    criteria_mapping = {
        "AC-01": [ac1_img, ac1_log],
        "AC-02": [ac2_video, ac2_trace],
    }

    custom_key = "secret_tenant_hmac_key_xyz"
    sealer = EvidenceSealer(hmac_key=custom_key)

    package = sealer.package_and_seal_suite_evidence(
        job_id="job-suite-402",
        tenant_id="tenant-acme",
        spec_id="SPEC-AUTH-CHECKOUT",
        evidence_dir=evidence_dir,
        criteria_mapping=criteria_mapping,
    )

    assert package.job_id == "job-suite-402"
    assert package.tenant_id == "tenant-acme"
    assert package.spec_id == "SPEC-AUTH-CHECKOUT"
    assert len(package.evidence_files) == 4
    assert len(package.overall_hash) == 64
    assert len(package.hmac_signature) == 64

    # Verificar inferencia de tipos
    types_by_name = {Path(f.file_path).name: f.file_type for f in package.evidence_files}
    assert types_by_name["login_success.png"] == "screenshot"
    assert types_by_name["httpx_network.log"] == "log"
    assert types_by_name["checkout_flow.webm"] == "video"
    assert types_by_name["playwright_trace.zip"] == "trace"

    # Verificar validez de la firma HMAC
    assert (
        sealer.verify_hmac_signature(package.overall_hash, package.hmac_signature, custom_key)
        is True
    )


# ---------------------------------------------------------------------------
# Prueba 3: test_prevencion_sobrescritura_worm_inmutabilidad
# ---------------------------------------------------------------------------
async def test_prevencion_sobrescritura_worm_inmutabilidad(tmp_path: Path) -> None:
    """
    Prueba 3: Intentar sellar o re-subir una evidencia bajo el mismo blob_ref.
    Validaciones:
    - La primera operación es exitosa.
    - La segunda operación lanza WORMImmutabilityViolationError (409 Conflict).
    - Cierra definitivamente QA-F09, QA-F13 y QA-F14.
    """
    file_path = tmp_path / "audit_report.xml"
    file_path.write_text("<junit>all passed</junit>", encoding="utf-8")

    sealer = EvidenceSealer()

    # 1. Primer sellado exitoso
    dto1 = sealer.seal_file(
        file_path=file_path,
        criterion_id="AC-01",
        file_type="report",
        tenant_id="tenant_worm",
        job_id="job_worm_01",
        base_dir=tmp_path,
    )
    assert dto1.blob_ref == "evidence/tenant_worm/job_worm_01/AC-01/audit_report.xml"

    # 2. Intento de re-sellado / modificación del mismo artefacto
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict.*política WORM"):
        sealer.seal_file(
            file_path=file_path,
            criterion_id="AC-01",
            file_type="report",
            tenant_id="tenant_worm",
            job_id="job_worm_01",
            base_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# Prueba 4: test_prevencion_path_traversal_al_sellar_archivos
# ---------------------------------------------------------------------------
async def test_prevencion_path_traversal_al_sellar_archivos(tmp_path: Path) -> None:
    """
    Prueba 4: Intentar procesar rutas de archivo fuera de base_dir
    o con retrocesos ('../../etc/passwd').
    Validaciones:
    - resolve_safe_path intercepta y lanza ValueError.
    - Ninguna evidencia fuera de la base es sellada.
    """
    safe_base = tmp_path / "allowed_dir"
    safe_base.mkdir()

    outside_file = tmp_path / "sensitive.env"
    outside_file.write_text("SUPER_SECRET_KEY=123", encoding="utf-8")

    sealer = EvidenceSealer()

    # Intento 1: Ruta con retroceso relativo
    with pytest.raises(ValueError, match="Path-Traversal.*detectado"):
        sealer.seal_file(
            file_path=Path("../sensitive.env"),
            criterion_id="AC-01",
            file_type="log",
            tenant_id="tenant_traversal",
            job_id="job_traversal",
            base_dir=safe_base,
        )

    # Intento 2: Ruta absoluta fuera del directorio base permitido
    with pytest.raises(ValueError, match="Path-Traversal.*detectado"):
        sealer.seal_file(
            file_path=outside_file,
            criterion_id="AC-01",
            file_type="log",
            tenant_id="tenant_traversal",
            job_id="job_traversal",
            base_dir=safe_base,
        )


# ---------------------------------------------------------------------------
# Prueba 5: test_procesamiento_en_bloques_archivos_grandes
# ---------------------------------------------------------------------------
async def test_procesamiento_en_bloques_archivos_grandes(tmp_path: Path) -> None:
    """
    Prueba 5: Validar que un archivo mayor al buffer de 64 KB (ej. 256 KB)
    sea leído secuencialmente en múltiples chunks sin discrepancias en el hash.
    """
    large_file = tmp_path / "heavy_video.webm"
    # 256 KB = 4 bloques completos de 64 KB
    chunk = b"A" * 65536
    large_file.write_bytes(chunk * 4)

    expected_hash = hashlib.sha256(chunk * 4).hexdigest()

    sealer = EvidenceSealer()
    computed_hash = sealer.compute_sha256(large_file)

    assert computed_hash == expected_hash


# ---------------------------------------------------------------------------
# Prueba 6: test_verificacion_integridad_hmac_tamper_detection
# ---------------------------------------------------------------------------
async def test_verificacion_integridad_hmac_tamper_detection(tmp_path: Path) -> None:
    """
    Prueba 6: Validar que cualquier alteración en el hash global o la clave
    provoque el rechazo inmediato de la firma HMAC.
    """
    sealer = EvidenceSealer(hmac_key="correct_key_123")
    valid_message = "sha256_package_digest_abc"
    sig = sealer.compute_hmac_signature(valid_message)

    # Firma con mensaje correcto
    assert sealer.verify_hmac_signature(valid_message, sig) is True

    # Firma con mensaje manipulado (tampered)
    tampered_message = "sha256_package_digest_abX"
    assert sealer.verify_hmac_signature(tampered_message, sig) is False

    # Firma con clave incorrecta
    assert sealer.verify_hmac_signature(valid_message, sig, key="wrong_key_999") is False


# ---------------------------------------------------------------------------
# Prueba 7: test_upload_evidence_worm_almacenamiento
# ---------------------------------------------------------------------------
async def test_upload_evidence_worm_almacenamiento() -> None:
    """
    Prueba 7: Simular upload a Azurite/Blob con política WORM.
    Validaciones:
    - Invoca upload_blob con immutability_policy_mode='Locked'.
    - Lanza WORMImmutabilityViolationError si el blob ya existe.
    """
    # Mock de storage client con cliente de Azure Blob
    mock_blob_client = MagicMock()
    mock_blob_client.url = (
        "https://127.0.0.1:10000/devstoreaccount1/evidencias-qa/evidence/test.png"
    )
    # Primera vez: No existe
    mock_blob_client.exists = AsyncMock(return_value=False)
    mock_blob_client.upload_blob = AsyncMock(return_value=None)

    mock_container_client = MagicMock()
    mock_container_client.get_blob_client.return_value = mock_blob_client

    mock_storage = MagicMock()
    mock_storage.client.get_blob_client.return_value = mock_blob_client

    sealer = EvidenceSealer(storage_client=mock_storage)

    # Subida exitosa inicial
    url = await sealer.upload_evidence_worm(
        bucket_name="evidencias-qa",
        blob_ref="evidence/t1/j1/AC-01/test.png",
        data=b"DATA_BLOB",
    )
    assert "https://" in url
    mock_blob_client.upload_blob.assert_awaited_once_with(
        b"DATA_BLOB",
        overwrite=False,
        immutability_policy_mode="Locked",
    )

    # Segunda vez: El blob ya existe en almacenamiento
    mock_blob_client.exists = AsyncMock(return_value=True)
    with pytest.raises(WORMImmutabilityViolationError, match="409 Conflict.*ya existe"):
        await sealer.upload_evidence_worm(
            bucket_name="evidencias-qa",
            blob_ref="evidence/t1/j1/AC-01/test.png",
            data=b"NEW_DATA_TAMPERED",
        )


# ---------------------------------------------------------------------------
# Prueba 8: test_seal_file_inexistente_o_tipo_invalido
# ---------------------------------------------------------------------------
async def test_seal_file_inexistente_o_tipo_invalido(tmp_path: Path) -> None:
    """
    Prueba 8: Valida excepciones FileNotFoundError y ValueError ante parámetros inválidos.
    """
    sealer = EvidenceSealer()

    # Archivo que no existe
    with pytest.raises(FileNotFoundError, match="no encontrado en disco"):
        sealer.seal_file(
            file_path=tmp_path / "does_not_exist.png",
            criterion_id="AC-01",
            file_type="screenshot",
            tenant_id="tenant_x",
            job_id="job_x",
            base_dir=tmp_path,
        )

    # Archivo existente pero tipo inválido
    real_file = tmp_path / "sample.bin"
    real_file.write_bytes(b"content")

    with pytest.raises(ValueError, match="Tipo de archivo inválido"):
        sealer.seal_file(
            file_path=real_file,
            criterion_id="AC-01",
            file_type="invalid_extension_type",
            tenant_id="tenant_x",
            job_id="job_x",
            base_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# Prueba 9: test_package_suite_vacia
# ---------------------------------------------------------------------------
async def test_package_suite_vacia(tmp_path: Path) -> None:
    """
    Prueba 9: Empaquetar suite sin evidencias genera un overall_hash determinista.
    """
    sealer = EvidenceSealer()
    package = sealer.package_and_seal_suite_evidence(
        job_id="job-empty",
        tenant_id="tenant-empty",
        spec_id="SPEC-EMPTY",
        evidence_dir=tmp_path,
        criteria_mapping={},
    )
    assert len(package.evidence_files) == 0
    assert len(package.overall_hash) == 64
    assert sealer.verify_hmac_signature(package.overall_hash, package.hmac_signature) is True


# ---------------------------------------------------------------------------
# Prueba 10: test_upload_evidence_worm_variantes
# ---------------------------------------------------------------------------
async def test_upload_evidence_worm_variantes() -> None:
    """
    Prueba 10: Valida upload sin storage_client y con cliente duck-typed upload_file.
    """
    # 1. Sin storage client
    sealer_no_storage = EvidenceSealer(storage_client=None)
    url_no_storage = await sealer_no_storage.upload_evidence_worm(
        bucket_name="bucket-qa",
        blob_ref="ref/file.png",
        data=b"data",
    )
    assert url_no_storage == "blob://bucket-qa/ref/file.png"

    # 2. Con cliente duck-typed que implementa upload_file
    mock_uploader = MagicMock(spec=["upload_file"])
    mock_uploader.upload_file = AsyncMock(return_value="https://custom.storage/file.png")

    sealer_uploader = EvidenceSealer(storage_client=mock_uploader)
    url_uploader = await sealer_uploader.upload_evidence_worm(
        bucket_name="bucket-custom",
        blob_ref="ref/custom.png",
        data=b"data",
    )
    assert url_uploader == "https://custom.storage/file.png"
    mock_uploader.upload_file.assert_awaited_once_with("bucket-custom", "ref/custom.png", b"data")
