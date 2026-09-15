# packages/codemapping/scripts/generate_golden.py
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from kallicode_codemapping.parser_treesitter import parse_file

REPO_REF = Path(__file__).parent.parent / "tests" / "fixtures" / "repo_ref"
GOLDEN = Path(__file__).parent.parent / "tests" / "golden"
GOLDEN.mkdir(exist_ok=True)

EXTENSION_TO_LANGUAGE = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".py": "python",
    ".java": "java",
    ".sql": "sql",
}

cases = {
    "user_service": "src/user_service.ts",
    "widget": "src/widget.tsx",
    "handlers": "src/handlers.js",
    "vendor_min": "src/vendor.min.js",
    "billing": "src/billing.py",
    "invoice": "src/Invoice.java",
    "reports_sql": "reports.sql",
}

for name, rel_path in cases.items():
    full_path = REPO_REF / rel_path
    content = full_path.read_text(encoding="utf-8")
    language = EXTENSION_TO_LANGUAGE.get(full_path.suffix, "unknown")
    sf = SimpleNamespace(path=rel_path, language=language, content=content)

    symbols = parse_file(sf)
    result = [asdict(s) for s in symbols]

    (GOLDEN / f"{name}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"✓ {name}.json generado ({len(result)} símbolos)")
