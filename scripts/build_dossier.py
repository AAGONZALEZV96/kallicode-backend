#!/usr/bin/env python3
"""Script ejecutable para consolidar el Expediente de Calidad GA (KC-QA-B6-07).

Uso:
  python3 scripts/build_dossier.py --help
  uv run python scripts/build_dossier.py --version v1.1 --artifacts-dir artifacts --enforce-gate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI usando únicamente librería estándar."""
    parser = argparse.ArgumentParser(
        description=(
            "Consolida métricas, trazabilidad AC-n y sellos SHA-256 WORM "
            "en el Expediente de Calidad KalliCode (KC-QA-B6-07)."
        ),
    )
    parser.add_argument(
        "--version",
        default="v1.1",
        help="Versión del Release Candidate (default: v1.1).",
    )
    parser.add_argument(
        "--commit-sha",
        default=os.getenv("GITHUB_SHA", "local-dev"),
        help="SHA de commit asociado al release (default: GITHUB_SHA o 'local-dev').",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts"),
        help="Directorio raíz de artefactos y reportes de prueba (default: artifacts).",
    )
    parser.add_argument(
        "--coverage-file",
        type=Path,
        default=None,
        help="Ruta opcional explícita a coverage.xml.",
    )
    parser.add_argument(
        "--junit-file",
        type=Path,
        default=None,
        help="Ruta opcional explícita a reporte JUnit XML.",
    )
    parser.add_argument(
        "--criteria-file",
        type=Path,
        default=None,
        help="Ruta JSON opcional con lista de criterios AC-n.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("docs/calidad/dossier_v1.1.json"),
        help="Ruta de salida para el JSON determinista (default: docs/calidad/dossier_v1.1.json).",
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=Path("docs/calidad/expediente_calidad_v1.1.md"),
        help=(
            "Ruta de salida para el informe Markdown "
            "(default: docs/calidad/expediente_calidad_v1.1.md)."
        ),
    )
    parser.add_argument(
        "--pdf-out",
        type=Path,
        default=None,
        help="Ruta opcional de salida para el expediente imprimible en PDF.",
    )
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="Retorna exit code 1 si la compuerta dura de release GA bloquea el expediente.",
    )
    return parser


def _ensure_testing_package_available() -> None:
    """Asegura disponibilidad de kallicode_testing si se invoca fuera de uv run."""
    repo_root = Path(__file__).resolve().parent.parent
    testing_src = str(repo_root / "packages" / "testing" / "src")
    if testing_src not in sys.path:
        sys.path.insert(0, testing_src)

    try:
        import defusedxml  # noqa: F401
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        venv_python = repo_root / ".venv" / "bin" / "python3"
        if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), *sys.argv])
        raise


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI del generador de Expediente de Calidad."""
    parser = build_parser()
    args = parser.parse_args(argv)

    _ensure_testing_package_available()

    from kallicode_testing.dossier_builder import DossierBuilder, ReleaseGateBlockedError

    spec_criteria: list[Any] | None = None
    if args.criteria_file is not None:
        if not args.criteria_file.exists():
            print(
                f"Error: archivo de criterios no encontrado: {args.criteria_file}",
                file=sys.stderr,
            )
            return 1
        raw_criteria = json.loads(args.criteria_file.read_text(encoding="utf-8"))
        if isinstance(raw_criteria, dict) and isinstance(raw_criteria.get("criteria"), list):
            spec_criteria = list(raw_criteria["criteria"])
        elif isinstance(raw_criteria, list):
            spec_criteria = raw_criteria
        else:
            print(
                "Error: el archivo de criterios debe contener una lista o clave 'criteria'.",
                file=sys.stderr,
            )
            return 1

    builder = DossierBuilder()
    try:
        dossier = builder.build_dossier(
            version=args.version,
            commit_sha=args.commit_sha,
            artifacts_dir=args.artifacts_dir,
            coverage_file=args.coverage_file,
            junit_file=args.junit_file,
            spec_criteria=spec_criteria,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    builder.export_json(dossier, args.json_out)
    builder.export_markdown(dossier, args.md_out)
    if args.pdf_out is not None:
        builder.export_pdf(dossier, args.pdf_out)

    if args.enforce_gate:
        try:
            builder.assert_release_ready(dossier)
        except ReleaseGateBlockedError as exc:
            print(f"[BLOCKED] {exc}", file=sys.stderr)
            return 1

    print(
        f"[OK] Expediente generado | version={dossier.release_version} | "
        f"ready={dossier.compliance_summary.is_ready} | "
        f"root_hash={dossier.root_hash[:16]}..."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
