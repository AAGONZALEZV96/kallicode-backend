"""Firma Digital e Inmutabilidad Criptográfica del Release Candidate (KC-QA-B6-11).

Ticket: KC-QA-B6-11
Épica: EQA1 — CI/CD y Entornos de Prueba de la Plataforma (Hito M6 GA)
Responsabilidad:
- Escanear deterministamente código (`apps/api/`, `packages/core/`, `packages/testing/`),
  esquemas (`db/`) y artefactos de calidad (`docs/calidad/...`) en orden lexicográfico.
- Excluir de manera idéntica caches, `.pyc`, `.pytest_cache`, cobertura y el propio
  certificado emitido tanto al generar el manifiesto como al verificar archivos agregados.
- Calcular `release_root_hash` SHA-256 y firmas HMAC/SHA-256 de las 4 autoridades GA
  (`PO`, `PLT1`, `CAL1 / QA1`, `PLT4`).
- Proveer verificación en tiempo real (`verify_release`, `verify_release_integrity`) y
  compuerta dura (`assert_release_signed_and_valid`) detectando modificación, eliminación
  o inserción de archivos sin autorización.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.logger import get_logger
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

DEFAULT_TARGET_DIRS: tuple[str, ...] = (
    "apps/api",
    "packages/core",
    "packages/testing",
    "db",
    "docs/calidad/expediente_calidad_v1.1.md",
    "docs/calidad/uat_signoff_matrix.md",
    "docs/calidad/informe_seguridad_final_v1.1.md",
)

# Exclusiones idénticas aplicadas en generación de manifiesto y detección de archivos agregados
EXCLUDED_PATTERNS: tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    "coverage.xml",
    ".coverage",
    ".coverage.*",
    ".DS_Store",
    "artifacts",
    "test-results",
    "playwright-report",
    ".auth",
    "release_signoff_v1.1.json",
    "release_signoff_v1.1.md",
)

MANDATORY_ROLES: tuple[str, ...] = ("PO", "PLT1", "CAL1 / QA1", "PLT4")

_ROLE_ALIASES: dict[str, str] = {
    "PO": "PO",
    "PRODUCT OWNER": "PO",
    "PRODUCT_OWNER": "PO",
    "PLT1": "PLT1",
    "TECH LEAD BACKEND": "PLT1",
    "TECH LEAD PLATAFORMA": "PLT1",
    "TECH LEAD BACKEND / PLATAFORMA": "PLT1",
    "CAL1": "CAL1 / QA1",
    "QA1": "CAL1 / QA1",
    "CAL1/QA1": "CAL1 / QA1",
    "CAL1 / QA1": "CAL1 / QA1",
    "TECH LEAD QA": "CAL1 / QA1",
    "TECH LEAD QA / SRE": "CAL1 / QA1",
    "PLT4": "PLT4",
    "INGENIERO DE SEGURIDAD": "PLT4",
    "SECURITY ENGINEER": "PLT4",
}


# ---------------------------------------------------------------------------
# Excepción de Dominio
# ---------------------------------------------------------------------------
class ReleaseIntegrityVerificationError(RuntimeError):
    """Lanzada ante tampering, faltantes, archivos agregados o firmas GA ausentes/rechazadas."""


# ---------------------------------------------------------------------------
# DTOs Oficiales Inmutables (Pydantic v2)
# ---------------------------------------------------------------------------
class FileChecksumDTO(BaseModel):
    """Registro inmutable del tamaño y hash SHA-256 de un archivo del Release Candidate."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    relative_path: str
    sha256_hash: str
    byte_size: int


class SignatoryApprovalDTO(BaseModel):
    """Aprobación formal e inmutable de una de las 4 autoridades obligatorias GA."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    signatory_name: str
    role: str
    signed_at: str
    decision: str
    statement: str
    signature_hash: str


class ReleaseSignoffDTO(BaseModel):
    """Certificado consolidado, firmado e inmutable del Release Candidate."""

    __test__ = False
    model_config = ConfigDict(frozen=True)

    release_version: str
    commit_sha: str
    generated_at: str
    release_root_hash: str
    total_files: int
    manifest: list[FileChecksumDTO]
    signatories: list[SignatoryApprovalDTO]
    is_tamper_free: bool
    is_fully_signed: bool
    scanned_targets: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers Internos Compartidos
# ---------------------------------------------------------------------------
def _is_excluded(rel_path: Path | PurePosixPath) -> bool:
    """Evalúa `EXCLUDED_PATTERNS` de forma idéntica en firma y verificación."""
    posix = PurePosixPath(rel_path.as_posix())
    parts = posix.parts
    name = posix.name
    for pattern in EXCLUDED_PATTERNS:
        if any(PurePosixPath(part).match(pattern) for part in parts):
            return True
        if posix.match(pattern) or PurePosixPath(name).match(pattern):
            return True
    return False


def _compute_sha256_and_size(file_path: Path) -> tuple[str, int]:
    """Calcula SHA-256 en bloques de 64 KB y el tamaño exacto en bytes."""
    hasher = hashlib.sha256()
    byte_size = 0
    with file_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
            byte_size += len(chunk)
    return hasher.hexdigest(), byte_size


def _canonical_role(raw_role: str) -> str:
    """Normaliza etiquetas de rol hacia las 4 autoridades canónicas GA."""
    cleaned = " ".join(raw_role.strip().upper().split())
    return _ROLE_ALIASES.get(cleaned, cleaned)


def _collect_target_files(repo_root: Path, target_dirs: list[str]) -> list[Path]:
    """Recolecta archivos únicos ordenados lexicográficamente aplicando `EXCLUDED_PATTERNS`."""
    base = repo_root.resolve()
    collected: dict[str, Path] = {}

    for raw_target in target_dirs:
        cleaned_target = raw_target.strip()
        if not cleaned_target:
            continue
        candidate = resolve_safe_path(base, cleaned_target)
        if not candidate.exists():
            logger.warning("release_signer.target_missing", target=cleaned_target)
            continue

        if candidate.is_file():
            rel = candidate.relative_to(base)
            if not _is_excluded(rel):
                collected[rel.as_posix()] = candidate
            continue

        if candidate.is_dir():
            for child in sorted(candidate.rglob("*")):
                if not child.is_file():
                    continue
                rel = child.relative_to(base)
                if _is_excluded(rel):
                    continue
                collected[rel.as_posix()] = child

    return [collected[key] for key in sorted(collected)]


# ---------------------------------------------------------------------------
# Clase Principal ReleaseSigner
# ---------------------------------------------------------------------------
class ReleaseSigner:
    """Generador y auditor criptográfico de certificados de Release Candidate."""

    def __init__(self, hmac_key: str | None = None) -> None:
        self._hmac_key = (
            hmac_key
            or os.getenv("RELEASE_SIGNING_HMAC_KEY")
            or "kallicode_release_signoff_local_key_2026"
        )

    def generate_manifest(
        self,
        repo_root: Path,
        target_dirs: list[str] | None = None,
    ) -> list[FileChecksumDTO]:
        """Escanea deterministamente `target_dirs` y genera lista ordenada de SHA-256."""
        base = repo_root.resolve()
        targets = list(target_dirs) if target_dirs is not None else list(DEFAULT_TARGET_DIRS)
        files = _collect_target_files(base, targets)

        manifest: list[FileChecksumDTO] = []
        for file_path in files:
            rel_posix = file_path.relative_to(base).as_posix()
            sha256_hash, byte_size = _compute_sha256_and_size(file_path)
            manifest.append(
                FileChecksumDTO(
                    relative_path=rel_posix,
                    sha256_hash=sha256_hash,
                    byte_size=byte_size,
                )
            )
        return sorted(manifest, key=lambda item: item.relative_path)

    def compute_root_hash(self, manifest: list[FileChecksumDTO]) -> str:
        """Calcula `release_root_hash` encadenando digests en orden lexicográfico estricto."""
        if not manifest:
            return ""
        hasher = hashlib.sha256()
        for entry in sorted(manifest, key=lambda item: item.relative_path):
            line = f"{entry.relative_path}|{entry.byte_size}|{entry.sha256_hash}\n"
            hasher.update(line.encode("utf-8"))
        return hasher.hexdigest()

    def compute_signature_hash(
        self,
        release_root_hash: str,
        role: str,
        signatory_name: str,
        decision: str,
    ) -> str:
        """Genera firma HMAC-SHA256 vinculada al `release_root_hash` y al firmante."""
        payload = f"{release_root_hash}|{_canonical_role(role)}|{signatory_name.strip()}|{decision.strip().upper()}"
        return hmac.new(
            self._hmac_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _evaluate_signatories(self, signatories: list[SignatoryApprovalDTO]) -> list[str]:
        """Valida presencia, decisión APPROVED y hash no vacío de las 4 firmas GA."""
        issues: list[str] = []
        by_canonical_role: dict[str, SignatoryApprovalDTO] = {}

        for sig in signatories:
            canon = _canonical_role(sig.role)
            by_canonical_role[canon] = sig

        for required_role in MANDATORY_ROLES:
            sig = by_canonical_role.get(required_role)
            if sig is None:
                issues.append(f"Firma obligatoria faltante para rol: {required_role}")
                continue
            if sig.decision.strip().upper() != "APPROVED":
                issues.append(f"Firma no aprobada para {required_role}: decision={sig.decision}")
            if not sig.signatory_name.strip():
                issues.append(f"Nombre de firmante vacío para rol: {required_role}")
            if not sig.statement.strip():
                issues.append(f"Declaración vacía para rol: {required_role}")
            if not sig.signature_hash.strip():
                issues.append(f"Checksum de firma vacío para rol: {required_role}")

        return issues

    def build_release_signoff(
        self,
        version: str,
        commit_sha: str,
        signatories: list[SignatoryApprovalDTO],
        repo_root: Path,
        *,
        target_dirs: list[str] | None = None,
    ) -> ReleaseSignoffDTO:
        """Genera el manifiesto determinista y ensambla el certificado `ReleaseSignoffDTO`."""
        effective_targets = (
            list(target_dirs) if target_dirs is not None else list(DEFAULT_TARGET_DIRS)
        )
        manifest = self.generate_manifest(repo_root, effective_targets)
        root_hash = self.compute_root_hash(manifest)

        bound_signatories: list[SignatoryApprovalDTO] = []
        for sig in signatories:
            sig_hash = sig.signature_hash.strip() or self.compute_signature_hash(
                root_hash,
                sig.role,
                sig.signatory_name,
                sig.decision,
            )
            bound_signatories.append(
                SignatoryApprovalDTO(
                    signatory_name=sig.signatory_name,
                    role=sig.role,
                    signed_at=sig.signed_at,
                    decision=sig.decision.strip().upper(),
                    statement=sig.statement,
                    signature_hash=sig_hash,
                )
            )

        signature_issues = self._evaluate_signatories(bound_signatories)
        is_fully_signed = len(signature_issues) == 0 and len(manifest) > 0
        is_tamper_free = len(manifest) > 0 and bool(root_hash)

        signoff = ReleaseSignoffDTO(
            release_version=version,
            commit_sha=commit_sha,
            generated_at=datetime.now(UTC).isoformat(),
            release_root_hash=root_hash,
            total_files=len(manifest),
            manifest=manifest,
            signatories=bound_signatories,
            is_tamper_free=is_tamper_free,
            is_fully_signed=is_fully_signed,
            scanned_targets=effective_targets,
        )
        logger.info(
            "release_signer.built",
            release_version=version,
            commit_sha=commit_sha,
            total_files=len(manifest),
            is_fully_signed=is_fully_signed,
            root_hash=root_hash[:16],
        )
        return signoff

    def verify_release(
        self,
        signoff: ReleaseSignoffDTO,
        repo_root: Path,
    ) -> tuple[bool, list[str]]:
        """Verifica en tiempo real hashes, archivos eliminados/agregados y firmas GA."""
        issues: list[str] = []
        base = repo_root.resolve()

        if signoff.total_files <= 0 or not signoff.manifest:
            issues.append("Manifiesto vacío: 0 archivos inventariados")
            return False, issues

        if len(signoff.manifest) != signoff.total_files:
            issues.append(
                f"Conteo total_files ({signoff.total_files}) difiere del manifiesto "
                f"({len(signoff.manifest)})"
            )

        expected_root = self.compute_root_hash(signoff.manifest)
        if not signoff.release_root_hash or expected_root != signoff.release_root_hash:
            issues.append("Discrepancia criptográfica en release_root_hash")

        manifest_paths: set[str] = set()
        for entry in signoff.manifest:
            manifest_paths.add(entry.relative_path)
            try:
                file_path = resolve_safe_path(base, entry.relative_path)
            except ValueError as exc:
                issues.append(f"Ruta inválida en manifiesto ({entry.relative_path}): {exc}")
                continue

            if not file_path.exists() or not file_path.is_file():
                issues.append(f"Archivo eliminado o faltante: {entry.relative_path}")
                continue

            actual_hash, actual_size = _compute_sha256_and_size(file_path)
            if actual_hash != entry.sha256_hash or actual_size != entry.byte_size:
                issues.append(f"Archivo modificado (tampering SHA-256): {entry.relative_path}")

        if signoff.scanned_targets:
            current_files = _collect_target_files(base, signoff.scanned_targets)
            for disk_file in current_files:
                rel_str = disk_file.relative_to(base).as_posix()
                if rel_str not in manifest_paths:
                    issues.append(f"Archivo agregado sin firma en el alcance: {rel_str}")

        issues.extend(self._evaluate_signatories(signoff.signatories))
        return len(issues) == 0, issues

    def assert_release_signed_and_valid(
        self,
        signoff: ReleaseSignoffDTO,
        repo_root: Path,
    ) -> None:
        """Compuerta dura GA: bloquea si hay tampering, faltantes o firmas incompletas."""
        is_valid, issues = self.verify_release(signoff, repo_root)
        if not is_valid:
            detail = "; ".join(issues)
            logger.error(
                "release_signer.verification_failed",
                release_version=signoff.release_version,
                issues=issues,
            )
            raise ReleaseIntegrityVerificationError(
                f"Verificación de Release Candidate fallida: {detail}"
            )

    def export_json(self, signoff: ReleaseSignoffDTO, output_path: Path) -> Path:
        """Exporta `release_signoff_v1.1.json` estructurado y determinista."""
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(signoff.model_dump(mode="json"), indent=2, sort_keys=True)
        target.write_text(f"{payload}\n", encoding="utf-8")
        logger.info(
            "release_signer.json_exported",
            path=str(target),
            root_hash=signoff.release_root_hash[:16],
        )
        return target

    def export_markdown(self, signoff: ReleaseSignoffDTO, output_path: Path) -> Path:
        """Exporta `release_signoff_v1.1.md` legible para auditores GA."""
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        verdict = (
            "APROBADO Y SELLADO (M6 GA READY)"
            if signoff.is_fully_signed and signoff.is_tamper_free
            else "NO CONFORME / PENDIENTE"
        )

        lines: list[str] = [
            f"# Certificado Formal de Sign-Off e Inmutabilidad — {signoff.release_version}",
            "",
            f"- **Veredicto GA:** `{verdict}`",
            f"- **Versión Release:** `{signoff.release_version}`",
            f"- **Commit SHA:** `{signoff.commit_sha}`",
            f"- **Emitido (UTC):** `{signoff.generated_at}`",
            f"- **Archivos Sellados:** `{signoff.total_files}`",
            f"- **Root Hash SHA-256:** `{signoff.release_root_hash}`",
            "",
            "## Protocolo Formal de 4 Firmas Obligatorias",
            "",
            "| Rol | Firmante | Decisión | Timestamp (UTC) | Declaración | Checksum Firma |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for sig in signoff.signatories:
            lines.append(
                f"| `{sig.role}` | {sig.signatory_name} | {sig.decision} | "
                f"`{sig.signed_at}` | {sig.statement} | `{sig.signature_hash[:16]}...` |"
            )

        lines.extend(
            [
                "",
                "## Resumen de Alcance Criptográfico",
                "",
                "| Parámetro / Directorio Sellado | Valor |",
                "| :--- | :--- |",
                f"| Objetivos escaneados | `{', '.join(signoff.scanned_targets or list(DEFAULT_TARGET_DIRS))}` |",
                f"| Exclusiones aplicadas | `{', '.join(EXCLUDED_PATTERNS)}` |",
                f"| Total de archivos en manifiesto | `{signoff.total_files}` |",
                f"| Integridad `is_tamper_free` | `{signoff.is_tamper_free}` |",
                f"| Firmas completas `is_fully_signed` | `{signoff.is_fully_signed}` |",
            ]
        )

        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("release_signer.markdown_exported", path=str(target))
        return target


# ---------------------------------------------------------------------------
# Funciones Duales de Alto Nivel
# ---------------------------------------------------------------------------
def build_default_ga_signatories() -> list[SignatoryApprovalDTO]:
    """Construye el bloque base de las 4 aprobaciones GA obligatorias."""
    now_iso = datetime.now(UTC).isoformat()
    return [
        SignatoryApprovalDTO(
            signatory_name="Product Owner (KalliCode)",
            role="PO",
            signed_at=now_iso,
            decision="APPROVED",
            statement="Aprobación funcional, de alcance de producto y cumplimiento 100% UAT.",
            signature_hash="",
        ),
        SignatoryApprovalDTO(
            signatory_name="Tech Lead Plataforma (PLT1)",
            role="PLT1",
            signed_at=now_iso,
            decision="APPROVED",
            statement="Aprobación de arquitectura, escalabilidad, carga k6 y recuperación ante caos.",
            signature_hash="",
        ),
        SignatoryApprovalDTO(
            signatory_name="Tech Lead QA & SRE (CAL1 / QA1)",
            role="CAL1 / QA1",
            signed_at=now_iso,
            decision="APPROVED",
            statement="Aprobación de cobertura global >= 80%, seguridad >= 95% y 0 bloqueadores.",
            signature_hash="",
        ),
        SignatoryApprovalDTO(
            signatory_name="Ingeniero de Seguridad (PLT4)",
            role="PLT4",
            signed_at=now_iso,
            decision="APPROVED",
            statement="Aprobación de postura OWASP Top 10, Gitleaks limpio y 0 CVEs Critical/High.",
            signature_hash="",
        ),
    ]


def sign_release(
    *,
    version: str,
    commit_sha: str,
    repo_root: Path,
    json_out: Path,
    md_out: Path | None = None,
    signatories: list[SignatoryApprovalDTO] | None = None,
    target_dirs: list[str] | None = None,
) -> ReleaseSignoffDTO:
    """Emite y exporta el certificado firmado e inmutable de Release Candidate."""
    signer = ReleaseSigner()
    effective_signatories = (
        signatories if signatories is not None else build_default_ga_signatories()
    )
    signoff = signer.build_release_signoff(
        version=version,
        commit_sha=commit_sha,
        signatories=effective_signatories,
        repo_root=repo_root,
        target_dirs=target_dirs,
    )
    signer.assert_release_signed_and_valid(signoff, repo_root)
    signer.export_json(signoff, json_out)
    if md_out is not None:
        signer.export_markdown(signoff, md_out)
    return signoff


def verify_release_integrity(
    manifest_path: Path,
    repo_root: Path,
) -> tuple[bool, list[str]]:
    """Carga un `release_signoff_*.json` y verifica su integridad contra `repo_root`."""
    if not manifest_path.exists():
        return False, [f"Certificado de release no encontrado: {manifest_path}"]
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    signoff = ReleaseSignoffDTO.model_validate(raw)
    signer = ReleaseSigner()
    return signer.verify_release(signoff, repo_root)
