#!/usr/bin/env python3
"""Script ejecutable para validación programática UAT (KC-QA-B6-08).

Uso:
  python3 scripts/verify_uat.py --help
  uv run python scripts/verify_uat.py --matrix-path docs/calidad/uat_signoff_matrix.md --enforce-approval
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
            "Valida programáticamente la matriz UAT por rol y genera el reporte "
            "estructurado `uat_signoff_report.json` (KC-QA-B6-08)."
        ),
    )
    parser.add_argument(
        "--matrix-path",
        type=Path,
        default=Path("docs/calidad/uat_signoff_matrix.md"),
        help="Ruta a la matriz UAT en Markdown (default: docs/calidad/uat_signoff_matrix.md).",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("docs/calidad/uat_signoff_report.json"),
        help="Ruta de salida para el reporte JSON (default: docs/calidad/uat_signoff_report.json).",
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=None,
        help="Ruta opcional para re-exportar la matriz Markdown normalizada.",
    )
    parser.add_argument(
        "--enforce-approval",
        action="store_true",
        help="Retorna exit code 1 si no se cumple el 100%% de escenarios UAT en estado PASSED.",
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
    """Punto de entrada CLI del validador UAT."""
    parser = build_parser()
    args = parser.parse_args(argv)

    _ensure_testing_package_available()

    from kallicode_testing.uat_validator import UatComplianceThresholdError, UatValidator

    validator = UatValidator()
    try:
        scenarios = validator.load_scenarios_from_markdown(args.matrix_path)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    report = validator.evaluate_compliance(scenarios)
    validator.export_json_report(report, args.json_out)
    if args.md_out is not None:
        validator.export_markdown_matrix(report, args.md_out)

    if args.enforce_approval:
        try:
            validator.assert_all_scenarios_passed(report)
        except UatComplianceThresholdError as exc:
            print(f"[BLOCKED] {exc}", file=sys.stderr)
            return 1

    print(
        f"[OK] UAT verificado | total={report.total_scenarios} | "
        f"passed={report.passed_scenarios} | "
        f"compliance={report.compliance_pct:.2f}% | "
        f"approved={report.is_fully_approved}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
