"""Comparador de Regresión Visual Pixel a Pixel para KalliCode (KC-QA-B5-04).

Certifica la estabilidad estética y de maquetación del Portal Cliente y componentes
clave mediante comparación de imágenes baseline vs runtime, generación de mapas de calor
de diferencias (diff.png) y consolidación de reportes inmutables para EvidenceSealer.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
import uuid
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field


class VisualRegressionError(Exception):
    """Excepción de dominio lanzada cuando una o más vistas superan el umbral de regresión."""


class VisualDiffMetricDTO(BaseModel):
    """Métrica individual inmutable de comparación visual por vista."""

    model_config = ConfigDict(frozen=True)

    view_name: str = Field(description="Nombre canónico de la vista o componente")
    baseline_path: str = Field(description="Ruta de la imagen de referencia (baseline)")
    runtime_path: str = Field(description="Ruta de la instantánea de ejecución (runtime)")
    diff_path: str | None = Field(default=None, description="Ruta de la imagen generada de diff")
    total_pixels: int = Field(ge=0, description="Cantidad total de píxeles analizados")
    mismatch_pixels: int = Field(ge=0, description="Píxeles que difieren más allá de la tolerancia")
    diff_percentage: float = Field(ge=0.0, le=100.0, description="Porcentaje de divergencia")
    passed: bool = Field(description="Indica si la divergencia está dentro del umbral permitido")


class VisualDiffReportDTO(BaseModel):
    """Reporte consolidado inmutable de auditoría de regresión visual."""

    model_config = ConfigDict(frozen=True)

    report_id: str = Field(description="Identificador único del reporte")
    created_at: str = Field(description="Timestamp UTC en formato ISO 8601")
    threshold_applied: float = Field(description="Umbral de tolerancia configurado")
    metrics: list[VisualDiffMetricDTO] = Field(
        default_factory=list, description="Lista de métricas por vista"
    )
    has_regressions: bool = Field(
        description="True si alguna de las vistas superó el umbral permitido"
    )


class VisualDiffComparator:
    """Motor de comparación visual pixel a pixel con tolerancia configurable.

    Args:
        threshold: Umbral de tolerancia visual. Si threshold <= 1.0, se interpreta como
            ratio (ej: 0.01 = 1.0%). Si threshold > 1.0, se interpreta como porcentaje directo.
        color_tolerance: Tolerancia euclidiana máxima por píxel en espacio RGBA (0-255).
    """

    def __init__(self, threshold: float = 0.01, color_tolerance: int = 16) -> None:
        self.threshold = threshold
        # Normalizar a porcentaje (0.01 -> 1.0%, 1.0 -> 1.0%, 5.0 -> 5.0%)
        self.max_diff_percentage = threshold * 100.0 if threshold <= 1.0 else threshold
        self.color_tolerance = color_tolerance

    def compare_images(
        self,
        baseline: Path | str,
        runtime: Path | str,
        output_diff: Path | str | None = None,
        view_name: str = "default",
    ) -> VisualDiffMetricDTO:
        """Compara dos imágenes pixel a pixel y genera mapa de diferencias si hay divergencia.

        Args:
            baseline: Ruta a la imagen de referencia.
            runtime: Ruta a la imagen generada en ejecución.
            output_diff: Ruta opcional donde guardar la imagen diff.png.
            view_name: Nombre identificador de la vista analizada.

        Returns:
            VisualDiffMetricDTO con el resultado de la comparación.

        Raises:
            FileNotFoundError: Si baseline o runtime no existen en disco.
        """
        baseline_path = Path(baseline).resolve()
        runtime_path = Path(runtime).resolve()

        if not baseline_path.exists():
            raise FileNotFoundError(f"Baseline image not found: {baseline_path}")
        if not runtime_path.exists():
            raise FileNotFoundError(f"Runtime image not found: {runtime_path}")

        img1 = Image.open(baseline_path).convert("RGBA")
        img2 = Image.open(runtime_path).convert("RGBA")

        w1, h1 = img1.size
        w2, h2 = img2.size

        max_w = max(w1, w2)
        max_h = max(h1, h2)
        total_pixels = max_w * max_h

        # Si las dimensiones difieren, creamos lienzos unificados para permitir el escaneo
        if (w1, h1) != (max_w, max_h):
            canvas1 = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
            canvas1.paste(img1, (0, 0))
            img1 = canvas1

        if (w2, h2) != (max_w, max_h):
            canvas2 = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
            canvas2.paste(img2, (0, 0))
            img2 = canvas2

        pixels1 = img1.load()
        pixels2 = img2.load()

        # Imagen de diff: fondo atenuado y diferencias resaltadas en magenta (#FF00FF)
        diff_img = Image.new("RGBA", (max_w, max_h), (255, 255, 255, 255))
        diff_pixels = diff_img.load()

        mismatch_pixels = 0
        diff_color = (255, 0, 255, 255)  # Magenta brillante para mapa de calor

        for y in range(max_h):
            for x in range(max_w):
                r1, g1, b1, a1 = pixels1[x, y]
                r2, g2, b2, a2 = pixels2[x, y]

                # Distancia euclidiana en canales RGBA
                delta = math.sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2 + (a1 - a2) ** 2)

                if delta > self.color_tolerance:
                    mismatch_pixels += 1
                    diff_pixels[x, y] = diff_color
                else:
                    # Fondo atenuado (grayscale con tinte suave)
                    gray = int(0.299 * r1 + 0.587 * g1 + 0.114 * b1)
                    dimmed = int(gray * 0.3 + 200 * 0.7)
                    diff_pixels[x, y] = (dimmed, dimmed, dimmed, 255)

        diff_percentage = (
            round((mismatch_pixels / total_pixels) * 100.0, 4) if total_pixels > 0 else 0.0
        )
        passed = diff_percentage <= self.max_diff_percentage

        saved_diff_path: str | None = None
        if output_diff is not None:
            diff_file_path = Path(output_diff).resolve()
            diff_file_path.parent.mkdir(parents=True, exist_ok=True)
            diff_img.save(diff_file_path, "PNG")
            saved_diff_path = str(diff_file_path)

        return VisualDiffMetricDTO(
            view_name=view_name,
            baseline_path=str(baseline_path),
            runtime_path=str(runtime_path),
            diff_path=saved_diff_path,
            total_pixels=total_pixels,
            mismatch_pixels=mismatch_pixels,
            diff_percentage=diff_percentage,
            passed=passed,
        )

    def build_report(
        self,
        metrics: list[VisualDiffMetricDTO],
        report_id: str | None = None,
    ) -> VisualDiffReportDTO:
        """Construye un reporte consolidado inmutable a partir de una lista de métricas."""
        rep_id = report_id or f"vdiff-{uuid.uuid4().hex[:12]}"
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
        has_regressions = any(not m.passed for m in metrics)

        return VisualDiffReportDTO(
            report_id=rep_id,
            created_at=created_at,
            threshold_applied=self.threshold,
            metrics=metrics,
            has_regressions=has_regressions,
        )

    def assert_no_visual_regressions(self, report: VisualDiffReportDTO) -> None:
        """Verifica que el reporte no contenga regresiones visuales.

        Raises:
            VisualRegressionError: Si alguna vista superó el umbral configurado.
        """
        if report.has_regressions:
            fallos = [
                (
                    f"'{m.view_name}': {m.diff_percentage:.4f}% > "
                    f"{self.max_diff_percentage:.2f}% (mismatch: {m.mismatch_pixels} px, "
                    f"diff: {m.diff_path or 'none'})"
                )
                for m in report.metrics
                if not m.passed
            ]
            raise VisualRegressionError(
                f"Visual regression detected in {len(fallos)} view(s): {'; '.join(fallos)}"
            )

    def export_report_json(self, report: VisualDiffReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte estructurado en formato JSON listo para EvidenceSealer."""
        dest_path = Path(output_path).resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return dest_path

    def load_report_json(self, file_path: Path | str) -> VisualDiffReportDTO:
        """Carga y valida un reporte de regresión visual desde un archivo JSON."""
        src_path = Path(file_path).resolve()
        if not src_path.exists():
            raise FileNotFoundError(f"Report file not found: {src_path}")
        content = src_path.read_text(encoding="utf-8")
        try:
            raw_dict = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido en reporte: {src_path}") from exc
        if not isinstance(raw_dict, dict):
            raise TypeError(f"Formato JSON inválido en reporte: {src_path}")
        return VisualDiffReportDTO.model_validate(raw_dict)


def main() -> int:
    """CLI para ejecución de comparación visual desde scripts y pipelines."""
    parser = argparse.ArgumentParser(description="KalliCode Visual Diff Comparator (KC-QA-B5-04)")
    parser.add_argument("--baseline", required=True, help="Ruta de la imagen baseline")
    parser.add_argument("--runtime", required=True, help="Ruta de la imagen runtime")
    parser.add_argument("--diff", default=None, help="Ruta de salida para diff.png")
    parser.add_argument("--view-name", default="cli_view", help="Nombre de la vista")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.01,
        help="Umbral de tolerancia (default: 0.01 = 1.0%%)",
    )
    parser.add_argument("--report-out", default=None, help="Ruta de salida para reporte JSON")

    args = parser.parse_args()
    comparator = VisualDiffComparator(threshold=args.threshold)

    try:
        metric = comparator.compare_images(
            baseline=args.baseline,
            runtime=args.runtime,
            output_diff=args.diff,
            view_name=args.view_name,
        )
        report = comparator.build_report([metric])

        if args.report_out:
            comparator.export_report_json(report, args.report_out)

        sys.stdout.write(
            f"View '{metric.view_name}': mismatch={metric.mismatch_pixels}px "
            f"({metric.diff_percentage:.4f}%), passed={metric.passed}\n"
        )

        comparator.assert_no_visual_regressions(report)
        return 0
    except VisualRegressionError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
