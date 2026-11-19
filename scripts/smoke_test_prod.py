#!/usr/bin/env python3
"""Script ejecutable de Pruebas de Humo post-despliegue (KC-QA-B6-06).

Uso:
  python3 scripts/smoke_test_prod.py --base-url http://localhost:8000
  uv run python scripts/smoke_test_prod.py --base-url http://localhost:8000 --json-report smoke.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser CLI usando únicamente librería estándar."""
    parser = argparse.ArgumentParser(
        description="Ejecuta smoke tests ultrarrápidos (< 15s) post-despliegue en KalliCode.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="URL base del backend objetivo (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Presupuesto máximo global en segundos (default: 15.0).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Número de reintentos rápidos por check ante fallo transitorio (default: 2).",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Ruta opcional para exportar el reporte estructurado SmokeReportDTO en JSON.",
    )
    parser.add_argument(
        "--api-route",
        default="/api/v1/auth/health",
        help="Ruta API representativa a validar (default: /api/v1/auth/health).",
    )
    return parser


def _ensure_testing_package_available() -> None:
    """Asegura disponibilidad de kallicode_testing si se invoca fuera de uv run."""
    repo_root = Path(__file__).resolve().parent.parent
    testing_src = str(repo_root / "packages" / "testing" / "src")
    if testing_src not in sys.path:
        sys.path.insert(0, testing_src)

    try:
        import httpx  # noqa: F401
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        venv_python = repo_root / ".venv" / "bin" / "python3"
        if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
            os.execv(str(venv_python), [str(venv_python), *sys.argv])
        raise


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada CLI del Smoke Test Runner."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        print("Error: --timeout debe ser mayor que 0.", file=sys.stderr)
        return 1
    if args.retries < 0:
        print("Error: --retries debe ser >= 0.", file=sys.stderr)
        return 1

    _ensure_testing_package_available()

    from kallicode_testing.smoke_runner import SmokeRunner

    per_request_timeout = min(3.0, args.timeout)
    runner = SmokeRunner(
        request_timeout_seconds=per_request_timeout,
        max_retries=args.retries,
        api_route=args.api_route,
    )
    report = runner.run_all_checks(args.base_url, timeout_seconds=args.timeout)

    if args.json_report is not None:
        SmokeRunner.export_report_json(report, args.json_report)

    if report.is_conforming:
        summary_lines = [
            (
                f"OK {report.report_id} | {report.base_url} | "
                f"{len(report.checks)}/{len(report.checks)} checks | "
                f"{report.total_duration_seconds:.3f}s"
            ),
            *[
                f"  - {c.check_name} ({c.endpoint}): HTTP {c.status_code} ({c.latency_ms:.1f}ms)"
                for c in report.checks
            ],
        ]
        print("\n".join(summary_lines), file=sys.stdout)
        return 0

    failed = [c for c in report.checks if not c.passed]
    err_lines = [
        (
            f"FAIL {report.report_id} | {report.base_url} | "
            f"duración={report.total_duration_seconds:.3f}s (presupuesto={args.timeout:.1f}s)"
        ),
        *[
            (
                f"  - DEGRADADO {c.check_name} ({c.endpoint}): "
                f"HTTP {c.status_code} | {c.error_message or 'fallo'}"
            )
            for c in failed
        ],
    ]
    if not failed:
        err_lines.append(
            f"  - TIMEOUT GLOBAL: la ejecución ({report.total_duration_seconds:.3f}s) "
            f"superó el límite de {args.timeout:.1f}s."
        )
    print("\n".join(err_lines), file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
