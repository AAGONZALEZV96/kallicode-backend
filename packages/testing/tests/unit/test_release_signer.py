"""Pruebas unitarias para Firma Digital e Inmutabilidad del Release Candidate (KC-QA-B6-11)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from kallicode_testing.release_signer import (
    FileChecksumDTO,
    ReleaseIntegrityVerificationError,
    ReleaseSigner,
    ReleaseSignoffDTO,
    SignatoryApprovalDTO,
    build_default_ga_signatories,
    sign_release,
    verify_release_integrity,
)
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _seed_release_workspace(root: Path) -> list[str]:
    """Crea una réplica mínima realista del árbol de release para pruebas deterministas."""
    (root / "apps" / "api").mkdir(parents=True, exist_ok=True)
    (root / "packages" / "core").mkdir(parents=True, exist_ok=True)
    (root / "packages" / "testing").mkdir(parents=True, exist_ok=True)
    (root / "db").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "calidad").mkdir(parents=True, exist_ok=True)

    (root / "apps" / "api" / "main.py").write_text("app = 'kallicode'\n", encoding="utf-8")
    (root / "packages" / "core" / "domain.py").write_text("VERSION = '1.1'\n", encoding="utf-8")
    (root / "packages" / "testing" / "smoke.py").write_text("OK = True\n", encoding="utf-8")
    (root / "db" / "01_schema.sql").write_text(
        "CREATE TABLE core_jobs (id INT);\n", encoding="utf-8"
    )
    (root / "docs" / "calidad" / "uat_signoff_matrix.md").write_text("# UAT OK\n", encoding="utf-8")
    # Caches excluidos de manera idéntica en firma y verificación
    cache_dir = root / "apps" / "api" / "__pycache__"
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / "main.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    (root / "coverage.xml").write_text("<coverage/>", encoding="utf-8")

    return [
        "apps/api",
        "packages/core",
        "packages/testing",
        "db",
        # Archivo opcional faltante: debe manejarse de forma segura sin FileNotFoundError
        "docs/calidad/expediente_calidad_v1.1.md",
        "docs/calidad/uat_signoff_matrix.md",
    ]


def test_release_signing_nominal_exitoso(tmp_path: Path) -> None:
    """Genera manifiesto completo, calcula root_hash determinista y valida 4 firmas GA."""
    target_dirs = _seed_release_workspace(tmp_path)
    signer = ReleaseSigner()
    signatories = build_default_ga_signatories()

    signoff_a = signer.build_release_signoff(
        "v1.1",
        "commit-ga-001",
        signatories,
        tmp_path,
        target_dirs=target_dirs,
    )
    signoff_b = signer.build_release_signoff(
        "v1.1",
        "commit-ga-001",
        signatories,
        tmp_path,
        target_dirs=target_dirs,
    )

    signer.assert_release_signed_and_valid(signoff_a, tmp_path)

    assert signoff_a.total_files == 5
    assert len(signoff_a.release_root_hash) == 64
    assert signoff_a.release_root_hash == signoff_b.release_root_hash
    assert signoff_a.is_tamper_free is True
    assert signoff_a.is_fully_signed is True
    assert all(len(sig.signature_hash) == 64 for sig in signoff_a.signatories)

    # Los cachés __pycache__/*.pyc no deben entrar en el manifiesto
    paths = [item.relative_path for item in signoff_a.manifest]
    assert all("__pycache__" not in p and not p.endswith(".pyc") for p in paths)


def test_verification_detecta_archivo_modificado(tmp_path: Path) -> None:
    """Alterar 1 byte en cualquier archivo inventariado debe hacer fallar la verificación."""
    target_dirs = _seed_release_workspace(tmp_path)
    signer = ReleaseSigner()
    signoff = signer.build_release_signoff(
        "v1.1",
        "commit-tamper",
        build_default_ga_signatories(),
        tmp_path,
        target_dirs=target_dirs,
    )

    # Tampering de 1 byte en código fuente
    (tmp_path / "packages" / "core" / "domain.py").write_text(
        "VERSION = '1.2'\n",
        encoding="utf-8",
    )

    is_valid, issues = signer.verify_release(signoff, tmp_path)
    assert is_valid is False
    assert any("packages/core/domain.py" in issue for issue in issues)
    with pytest.raises(ReleaseIntegrityVerificationError, match="packages/core/domain.py"):
        signer.assert_release_signed_and_valid(signoff, tmp_path)


def test_verification_detecta_archivo_eliminado(tmp_path: Path) -> None:
    """Detecta fallo si un archivo inventariado desaparece y si se agrega uno no firmado."""
    target_dirs = _seed_release_workspace(tmp_path)
    signer = ReleaseSigner()
    signoff = signer.build_release_signoff(
        "v1.1",
        "commit-delete",
        build_default_ga_signatories(),
        tmp_path,
        target_dirs=target_dirs,
    )

    # Crear cache excluido no debe romper verificación (mismo EXCLUDED_PATTERNS)
    (tmp_path / "packages" / "core" / "__pycache__").mkdir(exist_ok=True)
    (tmp_path / "packages" / "core" / "__pycache__" / "domain.cpython-312.pyc").write_bytes(b"xx")
    ok_with_cache, _ = signer.verify_release(signoff, tmp_path)
    assert ok_with_cache is True

    # Eliminar archivo inventariado + agregar archivo no excluido sin firmar
    (tmp_path / "db" / "01_schema.sql").unlink()
    (tmp_path / "apps" / "api" / "backdoor.py").write_text("print('rogue')\n", encoding="utf-8")

    is_valid, issues = signer.verify_release(signoff, tmp_path)
    assert is_valid is False
    assert any("eliminado o faltante: db/01_schema.sql" in msg for msg in issues)
    assert any("agregado sin firma en el alcance: apps/api/backdoor.py" in msg for msg in issues)


def test_assert_release_bloquea_si_falta_firma(tmp_path: Path) -> None:
    """Lanza ReleaseIntegrityVerificationError si falta alguna de las 4 firmas o hay REJECTED."""
    target_dirs = _seed_release_workspace(tmp_path)
    signer = ReleaseSigner()

    # Solo 3 de 4 firmas (falta PLT4)
    incomplete_sigs = build_default_ga_signatories()[:3]
    signoff_missing = signer.build_release_signoff(
        "v1.1",
        "commit-3-sigs",
        incomplete_sigs,
        tmp_path,
        target_dirs=target_dirs,
    )
    assert signoff_missing.is_fully_signed is False
    with pytest.raises(ReleaseIntegrityVerificationError, match="PLT4"):
        signer.assert_release_signed_and_valid(signoff_missing, tmp_path)

    # Las 4 firmas presentes pero PLT1 en estado REJECTED
    all_sigs = build_default_ga_signatories()
    rejected_plt1 = SignatoryApprovalDTO(
        signatory_name=all_sigs[1].signatory_name,
        role="PLT1",
        signed_at=all_sigs[1].signed_at,
        decision="REJECTED",
        statement="Carga p95 fuera de SLA",
        signature_hash="",
    )
    signoff_rejected = signer.build_release_signoff(
        "v1.1",
        "commit-rejected",
        [all_sigs[0], rejected_plt1, all_sigs[2], all_sigs[3]],
        tmp_path,
        target_dirs=target_dirs,
    )
    assert signoff_rejected.is_fully_signed is False
    with pytest.raises(ReleaseIntegrityVerificationError, match="PLT1"):
        signer.assert_release_signed_and_valid(signoff_rejected, tmp_path)


def test_inmutabilidad_dtos_signer(tmp_path: Path) -> None:
    """Valida ConfigDict(frozen=True) y __test__ = False en todos los DTOs."""
    for dto_cls in (FileChecksumDTO, SignatoryApprovalDTO, ReleaseSignoffDTO):
        assert dto_cls.__test__ is False

    target_dirs = _seed_release_workspace(tmp_path)
    signoff = ReleaseSigner().build_release_signoff(
        "v1.1",
        "commit-frozen",
        build_default_ga_signatories(),
        tmp_path,
        target_dirs=target_dirs,
    )

    with pytest.raises(ValidationError):
        signoff.release_root_hash = "0000"
    with pytest.raises(ValidationError):
        signoff.manifest[0].sha256_hash = "1111"
    with pytest.raises(ValidationError):
        signoff.signatories[0].decision = "REJECTED"


def test_cli_sign_verify_manifiesto_vacio_y_exportacion_dual(tmp_path: Path) -> None:
    """Cubre CLI --sign/--verify (exit 0/1), prevención de manifiesto vacío y export JSON/MD."""
    from scripts.sign_release import main as cli_main

    target_dirs = _seed_release_workspace(tmp_path)
    json_out = tmp_path / "out" / "release_signoff_v1.1.json"
    md_out = tmp_path / "out" / "release_signoff_v1.1.md"

    # Prevención de manifiesto vacío (target inexistente) -> sign_release debe bloquear
    with pytest.raises(ReleaseIntegrityVerificationError, match="Manifiesto vacío"):
        sign_release(
            version="v1.1",
            commit_sha="sha-empty",
            repo_root=tmp_path,
            json_out=json_out,
            target_dirs=["carpeta_que_no_existe"],
        )

    # CLI --sign exitoso
    cli_args = [
        "--sign",
        "--version",
        "v1.1",
        "--commit-sha",
        "sha-cli-ga",
        "--repo-root",
        str(tmp_path),
        "--json-path",
        str(json_out),
        "--md-path",
        str(md_out),
    ]
    for td in target_dirs:
        cli_args.extend(["--target-dir", td])

    assert cli_main(cli_args) == 0
    assert json_out.exists()
    assert md_out.exists()
    md_content = md_out.read_text(encoding="utf-8")
    assert "APROBADO Y SELLADO (M6 GA READY)" in md_content
    assert "PLT4" in md_content

    # Helper verify_release_integrity y CLI --verify exit 0
    ok_helper, helper_issues = verify_release_integrity(json_out, tmp_path)
    assert ok_helper is True
    assert helper_issues == []
    assert cli_main(["--verify", "--repo-root", str(tmp_path), "--json-path", str(json_out)]) == 0

    # Tampering en JSON (alterar commit o archivo) -> CLI --verify exit 1
    (tmp_path / "apps" / "api" / "main.py").write_text("app = 'hacked'\n", encoding="utf-8")
    assert cli_main(["--verify", "--repo-root", str(tmp_path), "--json-path", str(json_out)]) == 1

    # Certificado inexistente -> exit 1
    assert (
        cli_main(
            [
                "--verify",
                "--repo-root",
                str(tmp_path),
                "--json-path",
                str(tmp_path / "no_existe.json"),
            ]
        )
        == 1
    )

    # Verificar el JSON parseado contiene 5 archivos
    parsed = json.loads(json_out.read_text(encoding="utf-8"))
    assert parsed["total_files"] == 5


def test_bordes_release_signer_firmantes_vacios_y_traversal(tmp_path: Path) -> None:
    """Cubre validaciones de firmantes con campos vacíos, desajuste total_files y path traversal."""
    target_dirs = _seed_release_workspace(tmp_path)
    signer = ReleaseSigner()

    assert signer.compute_root_hash([]) == ""
    ok_missing, issues_missing = verify_release_integrity(tmp_path / "missing.json", tmp_path)
    assert ok_missing is False
    assert "no encontrado" in issues_missing[0]

    # Firmantes con nombre, statement o signature_hash vacíos
    base_sigs = build_default_ga_signatories()
    bad_sigs = [
        SignatoryApprovalDTO(
            signatory_name="",
            role="PO",
            signed_at=base_sigs[0].signed_at,
            decision="APPROVED",
            statement="",
            signature_hash="",
        ),
        *base_sigs[1:],
    ]
    issues_eval = signer._evaluate_signatories(bad_sigs)
    assert any("Nombre de firmante vacío" in i for i in issues_eval)
    assert any("Declaración vacía" in i for i in issues_eval)
    assert any("Checksum de firma vacío" in i for i in issues_eval)

    # Desajuste en total_files, root_hash inválido y path traversal en manifiesto
    valid_signoff = signer.build_release_signoff(
        "v1.1",
        "commit-edge",
        base_sigs,
        tmp_path,
        target_dirs=["", *target_dirs],
    )
    tampered_dto = ReleaseSignoffDTO(
        release_version="v1.1",
        commit_sha="commit-edge",
        generated_at=valid_signoff.generated_at,
        release_root_hash="deadbeef",
        total_files=999,
        manifest=[
            *valid_signoff.manifest,
            FileChecksumDTO(
                relative_path="../escape.py",
                sha256_hash="0" * 64,
                byte_size=10,
            ),
        ],
        signatories=valid_signoff.signatories,
        is_tamper_free=False,
        is_fully_signed=False,
    )
    ok_tampered, issues_tampered = signer.verify_release(tampered_dto, tmp_path)
    assert ok_tampered is False
    assert any("Conteo total_files" in i for i in issues_tampered)
    assert any("Discrepancia criptográfica" in i for i in issues_tampered)
    assert any("Ruta inválida" in i for i in issues_tampered)
    md_bad = signer.export_markdown(tampered_dto, tmp_path / "bad_signoff.md")
    assert "NO CONFORME / PENDIENTE" in md_bad.read_text(encoding="utf-8")

    # Normalización EOL en memoria (CRLF == LF) en generate_manifest y verify_release
    app_file = tmp_path / valid_signoff.manifest[0].relative_path
    lf_bytes = app_file.read_bytes().replace(b"\r\n", b"\n")
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    app_file.write_bytes(crlf_bytes)
    ok_crlf, issues_crlf = signer.verify_release(valid_signoff, tmp_path)
    assert ok_crlf is True
    assert issues_crlf == []
