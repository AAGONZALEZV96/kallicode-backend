# tests/test_parser.py
import pytest
from kallicode_codemapping.parser_treesitter import parse_file

CASES = [
    ("src/user_service.ts", "user_service"),
    ("src/widget.tsx", "widget"),
    ("src/handlers.js", "handlers"),
    ("src/vendor.min.js", "vendor_min"),
    ("src/billing.py", "billing"),
    ("src/Invoice.java", "invoice"),
    ("reports.sql", "reports_sql"),
]


@pytest.mark.parametrize("relative_path, golden_name", CASES)
def test_parse_file_matches_golden(relative_path, golden_name, source_file_factory, golden_loader, symbols_to_dicts):
    sf = source_file_factory(relative_path)
    symbols = parse_file(sf)
    result = symbols_to_dicts(symbols)
    expected = golden_loader(golden_name)
    assert result == expected


def test_reports_sql_returns_empty_list(source_file_factory):
    """SQL no está en CAPTURE: no debe romper, y debe devolver lista vacía."""
    sf = source_file_factory("reports.sql")
    result = parse_file(sf)
    assert result == []
