"""Pruebas unitarias para I18nValidator (KC-QA-B5-07).

Valida la auditoría estática de diccionarios, detección de claves faltantes,
inconsistencias en variables dinámicas/placeholders, inmutabilidad de DTOs y CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kallicode_testing.i18n_validator import (
    I18nKeyMismatchDTO,
    I18nParityError,
    I18nValidationReportDTO,
    I18nValidator,
)
from pydantic import ValidationError


@pytest.fixture
def temp_locales_dir(tmp_path: Path) -> Path:
    """Directorio temporal para archivos de idioma."""
    d = tmp_path / "locales"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _escribir_json(path: Path, data: dict[str, object]) -> Path:
    """Escribe un diccionario en archivo JSON."""
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def test_paridad_estatica_diccionarios_json() -> None:
    """Prueba 4: Verifica paridad total 100% entre es.json y en.json del proyecto."""
    fixtures_dir = Path(__file__).resolve().parents[1] / "fixtures" / "locales"
    es_path = fixtures_dir / "es.json"
    en_path = fixtures_dir / "en.json"

    validator = I18nValidator()
    report = validator.compare_locale_files(es_path, en_path, report_id="i18n-official-portal")

    assert report.report_id == "i18n-official-portal"
    assert report.locales_analyzed == ["es", "en"]
    assert report.total_keys >= 200
    assert report.missing_keys_count == 0
    assert len(report.mismatches) == 0
    assert report.is_conforming is True

    # No debe levantar I18nParityError
    validator.assert_full_i18n_parity(report)


def test_inmutabilidad_dtos_i18n() -> None:
    """Prueba 5: Certifica que los DTOs de i18n sean inmutables (frozen=True)."""
    mismatch = I18nKeyMismatchDTO(
        key_path="login.titulo",
        source_locale="es",
        missing_in_locale="en",
        issue_type="missing_key",
    )

    with pytest.raises(ValidationError):
        mismatch.source_locale = "pt"

    with pytest.raises(ValidationError):
        mismatch.issue_type = "empty_value"

    report = I18nValidationReportDTO(
        report_id="i18n-test-01",
        locales_analyzed=["es", "en"],
        total_keys=50,
        missing_keys_count=1,
        mismatches=[mismatch],
        is_conforming=False,
    )

    with pytest.raises(ValidationError):
        report.is_conforming = True

    with pytest.raises(ValidationError):
        report.total_keys = 100


def test_detecta_clave_faltante(temp_locales_dir: Path) -> None:
    """Verifica detección de claves presentes en un idioma y faltantes en otro."""
    es_file = _escribir_json(
        temp_locales_dir / "es.json",
        {"nav.dashboard": "Tablero", "nav.audit": "Auditoría"},
    )
    en_file = _escribir_json(
        temp_locales_dir / "en.json",
        {"nav.dashboard": "Dashboard"},
    )

    validator = I18nValidator()
    report = validator.compare_locale_files(es_file, en_file)

    assert report.is_conforming is False
    assert report.missing_keys_count == 1
    assert len(report.mismatches) == 1

    m = report.mismatches[0]
    assert m.key_path == "nav.audit"
    assert m.source_locale == "es"
    assert m.missing_in_locale == "en"
    assert m.issue_type == "missing_key"

    with pytest.raises(I18nParityError) as exc_info:
        validator.assert_full_i18n_parity(report)

    assert "nav.audit" in str(exc_info.value)
    assert "missing_key" in str(exc_info.value)


def test_detecta_placeholder_faltante_o_discrepante(temp_locales_dir: Path) -> None:
    """Verifica detección de variables dinámicas con nombres diferentes o ausentes."""
    es_file = _escribir_json(
        temp_locales_dir / "es.json",
        {
            "firma.firmado": "GATE {gate} firmado por {user_name}.",
            "tk.count": "{count} tickets encontrados",
        },
    )
    en_file = _escribir_json(
        temp_locales_dir / "en.json",
        {
            "firma.firmado": "GATE {gate} signed by {actor}.",  # user_name vs actor
            "tk.count": "tickets found",  # falta {count}
        },
    )

    validator = I18nValidator()
    report = validator.compare_locale_files(es_file, en_file)

    assert report.is_conforming is False
    placeholders_issues = [m for m in report.mismatches if m.issue_type == "missing_placeholder"]
    assert len(placeholders_issues) >= 3

    keys_affected = {m.key_path for m in placeholders_issues}
    assert "firma.firmado:{user_name}" in keys_affected
    assert "firma.firmado:{actor}" in keys_affected
    assert "tk.count:{count}" in keys_affected


def test_detecta_valor_vacio(temp_locales_dir: Path) -> None:
    """Verifica detección de claves con valor vacío o compuesto solo por espacios."""
    es_file = _escribir_json(
        temp_locales_dir / "es.json",
        {"hdr.title": "Portal", "hdr.sub": ""},
    )
    en_file = _escribir_json(
        temp_locales_dir / "en.json",
        {"hdr.title": "   ", "hdr.sub": "Subtitle"},
    )

    validator = I18nValidator()
    report = validator.compare_locale_files(es_file, en_file)

    assert report.is_conforming is False
    empty_issues = [m for m in report.mismatches if m.issue_type == "empty_value"]
    assert len(empty_issues) == 2

    keys_empty = {m.key_path for m in empty_issues}
    assert "hdr.sub" in keys_empty
    assert "hdr.title" in keys_empty


def test_flatten_nested_keys(temp_locales_dir: Path) -> None:
    """Verifica el soporte para aplanamiento de objetos JSON anidados."""
    es_file = _escribir_json(
        temp_locales_dir / "es.json",
        {"nav": {"deep": {"link": "Enlace"}}},
    )
    en_file = _escribir_json(
        temp_locales_dir / "en.json",
        {"nav": {"deep": {"link": "Link"}}},
    )

    validator = I18nValidator()
    report = validator.compare_locale_files(es_file, en_file)

    assert report.is_conforming is True
    assert report.total_keys == 1


def test_export_and_load_report_json(temp_locales_dir: Path) -> None:
    """Verifica la persistencia y lectura determinista del reporte JSON."""
    es_file = _escribir_json(temp_locales_dir / "es.json", {"ok": "Sí"})
    en_file = _escribir_json(temp_locales_dir / "en.json", {"ok": "Yes"})

    validator = I18nValidator()
    report = validator.compare_locale_files(es_file, en_file, report_id="i18n-rep-01")

    out_file = temp_locales_dir / "reports" / "i18n-report.json"
    exported = validator.export_report_json(report, out_file)
    assert exported.exists()

    loaded = validator.load_report_json(out_file)
    assert loaded.report_id == "i18n-rep-01"
    assert loaded.total_keys == 1
    assert loaded.is_conforming is True


def test_missing_file_raises_filenotfound(temp_locales_dir: Path) -> None:
    """Verifica que rutas no existentes levanten FileNotFoundError."""
    valid_file = _escribir_json(temp_locales_dir / "valid.json", {"k": "v"})
    missing_file = temp_locales_dir / "missing.json"

    validator = I18nValidator()

    with pytest.raises(FileNotFoundError, match="Archivo de locale español"):
        validator.compare_locale_files(missing_file, valid_file)

    with pytest.raises(FileNotFoundError, match="Archivo de locale inglés"):
        validator.compare_locale_files(valid_file, missing_file)

    with pytest.raises(FileNotFoundError, match="Report file not found"):
        validator.load_report_json(missing_file)


def test_invalid_json_structure(temp_locales_dir: Path) -> None:
    """Verifica validación ante JSON malformado o no diccionarios."""
    bad_json = temp_locales_dir / "bad.json"
    bad_json.write_text("{invalid json", encoding="utf-8")

    list_json = temp_locales_dir / "list.json"
    list_json.write_text("[]", encoding="utf-8")

    valid = _escribir_json(temp_locales_dir / "valid.json", {"a": "b"})

    validator = I18nValidator()

    with pytest.raises(ValueError, match="JSON inválido"):
        validator.compare_locale_files(bad_json, valid)

    with pytest.raises(TypeError, match="debe ser un diccionario"):
        validator.compare_locale_files(list_json, valid)


def test_cli_main_success_and_failure(
    temp_locales_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valida la ejecución de la CLI main() tanto para éxito como ante disparidad."""
    from kallicode_testing.i18n_validator import main

    es_file = _escribir_json(temp_locales_dir / "es_cli.json", {"title": "Título"})
    en_file = _escribir_json(temp_locales_dir / "en_cli.json", {"title": "Title"})
    report_out = temp_locales_dir / "cli_report.json"

    # Caso 1: Paridad 100% -> exit 0
    monkeypatch.setattr(
        "sys.argv",
        [
            "i18n_validator.py",
            "--es",
            str(es_file),
            "--en",
            str(en_file),
            "--output-report",
            str(report_out),
        ],
    )
    assert main() == 0
    assert report_out.exists()

    # Caso 2: Clave faltante -> exit 1
    en_bad = _escribir_json(temp_locales_dir / "en_bad.json", {})
    monkeypatch.setattr(
        "sys.argv",
        [
            "i18n_validator.py",
            "--es",
            str(es_file),
            "--en",
            str(en_bad),
        ],
    )
    assert main() == 1
