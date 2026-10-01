"""KC-CM-01-2 — Golden tests del analizador AST sobre el repositorio sintético repo_ref.

Qué se fija aquí:
  * parse_file: la lista completa de símbolos por archivo (incluidos los IDs).
  * walk_sources: qué archivos entrega, con qué lenguaje, sha256 y en qué orden.
  * Determinismo de IDs: misma entrada -> mismos IDs, sin importar la ruta absoluta
    del checkout, el orden de parseo ni el sistema operativo (separador de rutas).

Regenerar los golden (solo cuando el cambio de salida es intencional):
    PowerShell:  $env:KC_UPDATE_GOLDEN="1"; uv run pytest tests/test_golden_ast.py
                 Remove-Item Env:KC_UPDATE_GOLDEN
    bash:        KC_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_ast.py
Después revisa el diff de tests/golden/ antes de commitear: el golden es la especificación.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from kallicode_codemapping.config import Settings
from kallicode_codemapping.ingest_repo import SourceFile, walk_sources
from kallicode_codemapping.parser_treesitter import parse_file

TESTS_DIR = Path(__file__).parent
REPO_REF = TESTS_DIR / "fixtures" / "repo_ref"
GOLDEN_DIR = TESTS_DIR / "golden"
UPDATE_GOLDEN = os.environ.get("KC_UPDATE_GOLDEN") == "1"

# Configuración explícita para walk_sources: el test no depende de los defaults de Settings
# ni de variables CM_* que el desarrollador tenga en su entorno o en .env.
INCLUDE_EXT = [".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".sql"]
EXCLUDE_DIRS = ["node_modules", ".git", "dist", "build", "__pycache__"]
MAX_FILE_BYTES = 200_000

# Inventario esperado del fixture: si alguien agrega o borra un archivo, esto falla primero.
FIXTURE_FILES: dict[str, str] = {
    # Casos base (sprint 1)
    "reports.sql": "sql",
    "src/Invoice.java": "java",
    "src/billing.py": "python",
    "src/handlers.js": "javascript",
    "src/user_service.ts": "typescript",
    "src/vendor.min.js": "javascript",
    "src/widget.tsx": "tsx",
    # Casos límite (KC-CM-01-2)
    "db/cte_avanzadas.sql": "sql",
    "src/facturacion/facturacion.py": "python",
    "src/generated/sdk.min.ts": "typescript",
    "src/main/java/com/acme/billing/InvoiceService.java": "java",
    "src/services/user_service.ts": "typescript",
    "src/ui/Widget.tsx": "tsx",
}
PARSEABLE = sorted(p for p, lang in FIXTURE_FILES.items() if lang != "sql")
SQL_FILES = sorted(p for p, lang in FIXTURE_FILES.items() if lang == "sql")


# --------------------------------------------------------------------------- helpers


def _settings(max_file_bytes: int = MAX_FILE_BYTES) -> Settings:
    return Settings(
        tenant_id="t_golden",
        include_ext=INCLUDE_EXT,
        exclude_dirs=EXCLUDE_DIRS,
        max_file_bytes=max_file_bytes,
    )


def _walk(root: Path, max_file_bytes: int = MAX_FILE_BYTES) -> list[SourceFile]:
    return list(walk_sources(str(root), _settings(max_file_bytes)))


def _source_file(rel_path: str) -> SourceFile:
    """SourceFile tal como lo entrega walk_sources (ruta relativa POSIX)."""
    for sf in _walk(REPO_REF):
        if sf.path == rel_path:
            return sf
    raise AssertionError(f"walk_sources no entregó {rel_path}")


def _symbols_as_dicts(sf: SourceFile) -> list[dict[str, Any]]:
    symbols = [asdict(s) for s in parse_file(sf)]
    # Orden canónico para que el golden sea legible y no dependa del recorrido interno.
    return sorted(
        symbols, key=lambda s: (s["start_line"], s["end_line"], s["kind"], s["name"], s["id"])
    )


def _golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def _assert_golden(name: str, actual: Any) -> None:
    path = _golden_path(name)
    if UPDATE_GOLDEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        # encoding y newline explícitos: en Windows el default es cp1252 + CRLF.
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"Falta el golden {path}. Genéralo con KC_UPDATE_GOLDEN=1."
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, (
        f"La salida difiere de {path.relative_to(TESTS_DIR)}. Si el cambio es intencional, "
        "regenera con KC_UPDATE_GOLDEN=1 y revisa el diff."
    )


def _golden_name_for(rel_path: str) -> str:
    return "parse_file/" + rel_path.replace("/", "__")


# --------------------------------------------------------------------------- golden: walk_sources


def test_walk_sources_golden() -> None:
    files = _walk(REPO_REF)
    actual = [{"path": f.path, "language": f.language, "sha256": f.sha256} for f in files]
    _assert_golden("walk_sources", actual)


def test_walk_sources_inventario_del_fixture() -> None:
    """Entrega exactamente los archivos fuente del fixture (README.md queda fuera por extensión)."""
    files = _walk(REPO_REF)
    assert {f.path: f.language for f in files} == FIXTURE_FILES


def test_walk_sources_orden_determinista() -> None:
    """El orden no puede depender del sistema de archivos (os.walk no garantiza orden)."""
    paths = [f.path for f in _walk(REPO_REF)]
    assert paths == sorted(paths)


def test_walk_sources_rutas_relativas_posix() -> None:
    """Las rutas alimentan el ID: deben ser relativas y con '/' también en Windows."""
    for f in _walk(REPO_REF):
        assert "\\" not in f.path, f.path
        assert not Path(f.path).is_absolute(), f.path
        assert not f.path.startswith(("./", "../")), f.path


def test_walk_sources_respeta_exclusiones_y_tamano(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(REPO_REF, repo)
    (repo / "node_modules" / "lib").mkdir(parents=True)
    (repo / "node_modules" / "lib" / "index.ts").write_text(
        "export const x = 1;\n", encoding="utf-8"
    )
    (repo / "src" / "huge.ts").write_text("// x\n" * 1000, encoding="utf-8")  # 5000 bytes

    paths = {f.path for f in _walk(repo, max_file_bytes=4_000)}

    assert "node_modules/lib/index.ts" not in paths
    assert "src/huge.ts" not in paths
    assert paths == set(FIXTURE_FILES)


# --------------------------------------------------------------------------- golden: parse_file


@pytest.mark.parametrize("rel_path", sorted(FIXTURE_FILES))
def test_parse_file_golden(rel_path: str) -> None:
    _assert_golden(_golden_name_for(rel_path), _symbols_as_dicts(_source_file(rel_path)))


@pytest.mark.parametrize("rel_path", SQL_FILES)
def test_parse_file_sql_no_rompe_y_no_extrae_simbolos(rel_path: str) -> None:
    """SQL (CTEs, $$ plpgsql) no está en CAPTURE: se documenta que devuelve [] sin fallar.
    Las tablas/CTEs las cubre extract_sql; si SQL entra a CAPTURE, este test avisa."""
    assert parse_file(_source_file(rel_path)) == []


# Contrato mínimo por caso límite, independiente del golden: si un refactor del parser
# deja de capturar alguno, falla con un mensaje legible en vez de un diff de JSON.
SIMBOLOS_CLAVE: dict[str, set[tuple[str, str]]] = {
    "src/handlers.js": {
        ("function", "/health"),  # function expression como valor de propiedad
        ("function", "/users"),  # arrow como valor de propiedad
    },
    "src/services/user_service.ts": {
        ("class", "Adapter"),  # clase dentro de namespace
        ("class", "LocalCache"),  # clase declarada dentro de una función
        ("function", "normalizeEmail"),  # arrow asignada a const
        ("function", "onRetry"),  # function expression asignada a const
        ("interface", "User"),
        ("enum", "Role"),
        ("type", "UserId"),
    },
    "src/ui/Widget.tsx": {
        ("function", "GenericList"),  # componente arrow genérico <T,>
        ("class", "Counter"),
        ("method", "render"),
        ("function", "Widget"),
    },
    "src/generated/sdk.min.ts": {
        ("class", "S"),
        ("method", "send"),
        ("function", "m"),  # function expression minificada
        ("function", "k"),  # arrow minificada
        ("function", "z"),
    },
    "src/facturacion/facturacion.py": {
        ("class", "Linea"),  # anidada nivel 2
        ("class", "Impuesto"),  # anidada nivel 3
        ("function", "wrapper"),  # closure dentro de closure
        ("function", "calcular_año_fiscal"),  # identificador no ASCII + decorador
        ("function", "enviar_sii"),  # async
    },
    "src/main/java/com/acme/billing/InvoiceService.java": {
        ("class", "AuditTrail"),  # clase interna
        ("class", "Totals"),  # static nested
        ("method", "run"),  # método de clase anónima
        ("method", "isFinal"),  # método dentro de enum
        ("interface", "Notifier"),
    },
}


@pytest.mark.parametrize("rel_path", sorted(SIMBOLOS_CLAVE))
def test_simbolos_clave_de_casos_limite(rel_path: str) -> None:
    encontrados = {(s.kind, s.name) for s in parse_file(_source_file(rel_path))}
    faltantes = SIMBOLOS_CLAVE[rel_path] - encontrados
    assert not faltantes, f"{rel_path}: no se capturaron {sorted(faltantes)}"


# --------------------------------------------------------------------------- determinismo de IDs


@pytest.mark.parametrize("rel_path", PARSEABLE)
def test_ids_estables_entre_ejecuciones(rel_path: str) -> None:
    sf = _source_file(rel_path)
    first = [asdict(s) for s in parse_file(sf)]
    second = [asdict(s) for s in parse_file(sf)]
    assert first == second


@pytest.mark.parametrize("rel_path", PARSEABLE)
def test_ids_unicos_por_archivo(rel_path: str) -> None:
    """Caso crítico: en código minificado todo cae en la línea 1 y las funciones
    anónimas comparten nombre '<anonymous>'. Un ID duplicado choca en la PK de cm.symbols."""
    ids = [s.id for s in parse_file(_source_file(rel_path))]
    duplicados = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicados, f"IDs duplicados en {rel_path}: {duplicados}"


def test_ids_unicos_en_todo_el_repo() -> None:
    ids = [s.id for sf in _walk(REPO_REF) for s in parse_file(sf)]
    assert len(ids) == len(set(ids))


def test_ids_independientes_de_la_ubicacion_del_checkout(tmp_path: Path) -> None:
    """El mismo repo clonado en dos rutas absolutas distintas produce los mismos IDs."""
    a = tmp_path / "a"
    b = tmp_path / "otra" / "ruta" / "mas" / "profunda"
    shutil.copytree(REPO_REF, a)
    shutil.copytree(REPO_REF, b)

    ids_a = {(s.file, s.name, s.start_line, s.id) for sf in _walk(a) for s in parse_file(sf)}
    ids_b = {(s.file, s.name, s.start_line, s.id) for sf in _walk(b) for s in parse_file(sf)}

    assert ids_a
    assert ids_a == ids_b


def test_ids_independientes_del_orden_de_parseo() -> None:
    files = _walk(REPO_REF)
    directo = {s.id: (s.file, s.name, s.start_line) for sf in files for s in parse_file(sf)}
    inverso = {
        s.id: (s.file, s.name, s.start_line) for sf in reversed(files) for s in parse_file(sf)
    }
    assert directo == inverso


def test_id_no_cambia_si_se_edita_otro_archivo(tmp_path: Path) -> None:
    """Base del refresh incremental: tocar un archivo no reescribe IDs de los demás."""
    repo = tmp_path / "repo"
    shutil.copytree(REPO_REF, repo)
    antes = {s.id for sf in _walk(repo) if sf.path != "src/ui/Widget.tsx" for s in parse_file(sf)}

    widget = repo / "src" / "ui" / "Widget.tsx"
    widget.write_text("// cambio\n" + widget.read_text(encoding="utf-8"), encoding="utf-8")
    despues = {s.id for sf in _walk(repo) if sf.path != "src/ui/Widget.tsx" for s in parse_file(sf)}

    assert antes == despues


# --------------------------------------------------------------------------- integridad de snippets


def _empieza_en_limite_de_token(line: str, fragment: str) -> bool:
    """True si `fragment` aparece en `line` sin partir un identificador por la izquierda."""
    start = line.find(fragment)
    while start != -1:
        prev = line[start - 1] if start > 0 else " "
        if not (prev.isalnum() or prev == "_"):
            return True
        start = line.find(fragment, start + 1)
    return False


@pytest.mark.parametrize("rel_path", PARSEABLE)
def test_snippet_alineado_con_el_nodo(rel_path: str) -> None:
    """tree-sitter entrega offsets en BYTES; cortar el str con ellos desplaza el snippet
    apenas aparece un carácter no ASCII (ñ, á) antes del símbolo ('def enviar_sii' -> '_sii')."""
    sf = _source_file(rel_path)
    lines = sf.content.splitlines()
    for s in parse_file(sf):
        first = s.snippet.splitlines()[0]
        assert _empieza_en_limite_de_token(lines[s.start_line - 1], first), (
            f"{rel_path}:{s.start_line} snippet desalineado para {s.name!r}: {first!r}"
        )


@pytest.mark.parametrize("rel_path", PARSEABLE)
def test_firma_es_prefijo_del_snippet(rel_path: str) -> None:
    """La firma se corta en el inicio del body: debe ser el comienzo del snippet."""
    for s in parse_file(_source_file(rel_path)):
        assert s.snippet.startswith(s.signature), f"{rel_path}:{s.start_line} {s.signature!r}"


@pytest.mark.parametrize("rel_path", [p for p in PARSEABLE if p.endswith(".py")])
def test_firma_python_termina_en_dos_puntos(rel_path: str) -> None:
    """En Python la firma termina justo antes del body: 'def f(...) -> T:' / 'class C:'."""
    for s in parse_file(_source_file(rel_path)):
        assert s.signature.endswith(":"), f"{rel_path}:{s.start_line} {s.signature!r}"
