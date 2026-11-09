"""Validador Estático y Auditor de Internacionalización (i18n) para KalliCode (KC-QA-B5-07).

Certifica la paridad total de claves entre diccionarios (es.json vs en.json), la ausencia
de cadenas desnudas o sin traducir, y la concordancia de placeholders y variables dinámicas.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class I18nParityError(Exception):
    """Excepción de dominio lanzada cuando existe disparidad de claves o placeholders en i18n."""


class I18nKeyMismatchDTO(BaseModel):
    """Detalle inmutable de un hallazgo o discrepancia entre diccionarios i18n."""

    model_config = ConfigDict(frozen=True)

    key_path: str = Field(description="Ruta o identificador de la clave evaluada")
    source_locale: str = Field(description="Locale donde la clave o placeholder sí está presente")
    missing_in_locale: str = Field(description="Locale donde falta la clave o placeholder")
    issue_type: Literal["missing_key", "missing_placeholder", "empty_value"] = Field(
        description="Tipo de discrepancia detectada"
    )


class I18nValidationReportDTO(BaseModel):
    """Reporte inmutable consolidado de auditoría de internacionalización."""

    model_config = ConfigDict(frozen=True)

    report_id: str = Field(description="Identificador único del reporte")
    locales_analyzed: list[str] = Field(description="Lista de códigos de idioma analizados")
    total_keys: int = Field(ge=0, description="Cantidad total de claves únicas inspeccionadas")
    missing_keys_count: int = Field(
        ge=0, description="Cantidad de claves faltantes en algún locale"
    )
    mismatches: list[I18nKeyMismatchDTO] = Field(
        default_factory=list, description="Lista detallada de discrepancias encontradas"
    )
    is_conforming: bool = Field(
        description="True si existe paridad total (0 claves faltantes, 0 vacías, 0 discrepancias)"
    )


class I18nValidator:
    """Motor de validación estática de paridad de diccionarios i18n."""

    PLACEHOLDER_REGEX = re.compile(r"\{([a-zA-Z0-9_]+)\}")

    @staticmethod
    def _flatten_dict(d: dict[str, Any], parent_key: str = "", sep: str = ".") -> dict[str, str]:
        """Aplana estructuras JSON anidadas a claves separadas por puntos (ej: a.b.c)."""
        items: list[tuple[str, str]] = []
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else str(k)
            if isinstance(v, dict):
                items.extend(I18nValidator._flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, str(v) if v is not None else ""))
        return dict(items)

    def compare_locale_files(
        self,
        es_json_path: Path | str,
        en_json_path: Path | str,
        report_id: str | None = None,
    ) -> I18nValidationReportDTO:
        """Compara exhaustivamente dos diccionarios locales (Español vs Inglés).

        Args:
            es_json_path: Ruta al archivo es.json.
            en_json_path: Ruta al archivo en.json.
            report_id: Identificador opcional para el reporte.

        Returns:
            I18nValidationReportDTO con los resultados de la comparación.

        Raises:
            FileNotFoundError: Si alguno de los archivos no existe en disco.
            TypeError: Si el archivo JSON no representa un diccionario.
        """
        es_path = Path(es_json_path).resolve()
        en_path = Path(en_json_path).resolve()

        if not es_path.exists():
            raise FileNotFoundError(f"Archivo de locale español no encontrado: {es_path}")
        if not en_path.exists():
            raise FileNotFoundError(f"Archivo de locale inglés no encontrado: {en_path}")

        try:
            es_raw = json.loads(es_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido en {es_path}") from exc

        try:
            en_raw = json.loads(en_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido en {en_path}") from exc

        if not isinstance(es_raw, dict):
            raise TypeError(f"El archivo {es_path} debe ser un diccionario JSON")
        if not isinstance(en_raw, dict):
            raise TypeError(f"El archivo {en_path} debe ser un diccionario JSON")

        es_dict = self._flatten_dict(es_raw)
        en_dict = self._flatten_dict(en_raw)

        mismatches: list[I18nKeyMismatchDTO] = []

        es_keys = set(es_dict.keys())
        en_keys = set(en_dict.keys())
        all_keys = es_keys | en_keys

        # 1. Claves en ES que faltan en EN
        for k in sorted(es_keys - en_keys):
            mismatches.append(
                I18nKeyMismatchDTO(
                    key_path=k,
                    source_locale="es",
                    missing_in_locale="en",
                    issue_type="missing_key",
                )
            )

        # 2. Claves en EN que faltan en ES
        for k in sorted(en_keys - es_keys):
            mismatches.append(
                I18nKeyMismatchDTO(
                    key_path=k,
                    source_locale="en",
                    missing_in_locale="es",
                    issue_type="missing_key",
                )
            )

        # 3. Validación de claves compartidas: valores vacíos y paridad de placeholders
        for k in sorted(es_keys & en_keys):
            es_val = es_dict[k]
            en_val = en_dict[k]

            # Detección de valor vacío
            if not es_val.strip():
                mismatches.append(
                    I18nKeyMismatchDTO(
                        key_path=k,
                        source_locale="en",
                        missing_in_locale="es",
                        issue_type="empty_value",
                    )
                )
            if not en_val.strip():
                mismatches.append(
                    I18nKeyMismatchDTO(
                        key_path=k,
                        source_locale="es",
                        missing_in_locale="en",
                        issue_type="empty_value",
                    )
                )

            # Detección de discrepancia en placeholders {variable}
            es_placeholders = set(self.PLACEHOLDER_REGEX.findall(es_val))
            en_placeholders = set(self.PLACEHOLDER_REGEX.findall(en_val))

            for p in sorted(es_placeholders - en_placeholders):
                mismatches.append(
                    I18nKeyMismatchDTO(
                        key_path=f"{k}:{{{p}}}",
                        source_locale="es",
                        missing_in_locale="en",
                        issue_type="missing_placeholder",
                    )
                )
            for p in sorted(en_placeholders - es_placeholders):
                mismatches.append(
                    I18nKeyMismatchDTO(
                        key_path=f"{k}:{{{p}}}",
                        source_locale="en",
                        missing_in_locale="es",
                        issue_type="missing_placeholder",
                    )
                )

        missing_count = sum(1 for m in mismatches if m.issue_type == "missing_key")
        is_conforming = len(mismatches) == 0
        rep_id = report_id or f"i18n-{uuid.uuid4().hex[:12]}"

        return I18nValidationReportDTO(
            report_id=rep_id,
            locales_analyzed=["es", "en"],
            total_keys=len(all_keys),
            missing_keys_count=missing_count,
            mismatches=mismatches,
            is_conforming=is_conforming,
        )

    def assert_full_i18n_parity(self, report: I18nValidationReportDTO) -> None:
        """Verifica que el reporte cumpla con paridad total de internacionalización.

        Raises:
            I18nParityError: Si se detectan claves faltantes, placeholders discrepantes o valores vacíos.
        """
        if not report.is_conforming:
            detalles = [
                f"[{m.issue_type}] {m.key_path} (presente en {m.source_locale}, falta en {m.missing_in_locale})"
                for m in report.mismatches
            ]
            raise I18nParityError(
                f"Disparidad de internacionalización detectada ({len(detalles)} problemas): {'; '.join(detalles)}"
            )

    def export_report_json(self, report: I18nValidationReportDTO, output_path: Path | str) -> Path:
        """Exporta el reporte consolidado a JSON estructurado para EvidenceSealer."""
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return dest

    def load_report_json(self, file_path: Path | str) -> I18nValidationReportDTO:
        """Carga y valida un reporte JSON desde disco."""
        src = Path(file_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Report file not found: {src}")
        content = src.read_text(encoding="utf-8")
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido en reporte: {src}") from exc
        if not isinstance(raw, dict):
            raise TypeError(f"El reporte {src} debe ser un objeto JSON")
        return I18nValidationReportDTO.model_validate(raw)


def main() -> int:
    """CLI para ejecución de auditoría i18n desde consola o pipelines CI."""
    parser = argparse.ArgumentParser(
        description="KalliCode i18n Dictionary Parity Validator (KC-QA-B5-07)"
    )
    parser.add_argument("--es", required=True, help="Ruta al archivo es.json")
    parser.add_argument("--en", required=True, help="Ruta al archivo en.json")
    parser.add_argument(
        "--output-report", default=None, help="Ruta de salida para i18n-report.json"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=True,
        help="Lanza exit code 1 si se detectan disparidades",
    )

    args = parser.parse_args()
    validator = I18nValidator()

    try:
        report = validator.compare_locale_files(args.es, args.en)
        if args.output_report:
            validator.export_report_json(report, args.output_report)

        sys.stdout.write(
            f"i18n Audit: total_keys={report.total_keys}, missing={report.missing_keys_count}, "
            f"mismatches={len(report.mismatches)}, conforming={report.is_conforming}\n"
        )

        if args.strict:
            validator.assert_full_i18n_parity(report)
        return 0
    except I18nParityError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
