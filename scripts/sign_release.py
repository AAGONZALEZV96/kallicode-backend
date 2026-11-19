#!/usr/bin/env python3
"""Script ejecutable para firma digital y verificación del Release Candidate (KC-QA-B6-11).

Uso:
  python3 scripts/sign_release.py --help
  uv run python scripts/sign_release.py --sign --version v1.1
  uv run python scripts/sign_release.py --verify --json-path docs/calidad/release_signoff_v1.1.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI usando únicamente librería estándar."""
    parser = argparse.ArgumentParser(
        description=(
            "Genera y verifica criptográficamente el certificado SHA-256 "
            "de Release Candidate KalliCode (KC-QA-B6-11)."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--sign",
        action="store_true",
        help="Escanea el monorepo y emite `release_signoff_v1.1.json` y `.md`.",
    )
    mode_group.add_argument(
        "--verify",
        action="store_true",
        help="Verifica en tiempo real hashes SHA-256, archivos y las 4 firmas GA.",
    )
    parser.add_argument(
        "--version",
        default="v1.1",
        help="Versión del Release Candidate (default: v1.1).",
    )
    parser.add_argument(
        "--commit-sha",
        default=os.getenv("GITHUB_SHA", "local-dev"),
        help="SHA de commit del Release Candidate (default: GITHUB_SHA o 'local-dev').",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Directorio raíz del monorepo (default: directorio actual).",
    )
    parser.add_argument(
        "--json-path",
        type=Path,
        default=Path("docs/calidad/release_signoff_v1.1.json"),
        help="Ruta JSON del certificado de sign-off (default: docs/calidad/release_signoff_v1.1.json).",
    )
    parser.add_argument(
        "--md-path",
        type=Path,
        default=Path("docs/calidad/release_signoff_v1.1.md"),
        help="Ruta Markdown del certificado (default: docs/calidad/release_signoff_v1.1.md).",
    )
    parser.add_argument(
        "--target-dir",
        action="append",
        dest="target_dirs",
        default=None,
        help="Directorio o artefacto objetivo adicional/personalizado (repetible).",
    )
    return parser


def _ensure_testing_package_available() -> None:
    """Asegura disponibilidad de kallicode_testing si se invoca fuera de uv run."""
    repo_root = Path(__file__).resolve().parent.parent
    testing_src = str(repo_root / "packages" / "testing" / "src")
    if testing_src not in sys.path:
        sys.path.insert(0, testing_src)

    try:
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        venv_python = repo_root / ".venv" / "bin" / "python3"
        if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), *sys.argv])
        raise


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI para emisión (--sign) y auditoría (--verify) del release."""
    parser = build_parser()
    args = parser.parse_args(argv)

    _ensure_testing_package_available()

    from kallicode_testing.release_signer import (
        ReleaseIntegrityVerificationError,
        ReleaseSigner,
        ReleaseSignoffDTO,
        sign_release,
    )

    repo_root = Path(args.repo_root).resolve()

    if args.verify:
        if not args.json_path.exists():
            print(f"[BLOCKED] Certificado no encontrado: {args.json_path}", file=sys.stderr)
            return 1
        try:
            raw = args.json_path.read_text(encoding="utf-8")
            signoff = ReleaseSignoffDTO.model_validate_json(raw)
            ReleaseSigner().assert_release_signed_and_valid(signoff, repo_root)
        except (ReleaseIntegrityVerificationError, ValueError) as exc:
            print(f"[BLOCKED] {exc}", file=sys.stderr)
            return 1

        print(
            f"[OK] Release verificado | version={signoff.release_version} | "
            f"files={signoff.total_files} | root_hash={signoff.release_root_hash[:16]}..."
        )
        return 0

    # Modo por defecto o explícito --sign
    try:
        signoff = sign_release(
            version=args.version,
            commit_sha=args.commit_sha,
            repo_root=repo_root,
            json_out=args.json_path,
            md_out=args.md_path,
            target_dirs=args.target_dirs,
        )
    except ReleaseIntegrityVerificationError as exc:
        print(f"[BLOCKED] {exc}", file=sys.stderr)
        return 1

    print(
        f"[OK] Release firmado | version={signoff.release_version} | "
        f"files={signoff.total_files} | root_hash={signoff.release_root_hash[:16]}..."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
