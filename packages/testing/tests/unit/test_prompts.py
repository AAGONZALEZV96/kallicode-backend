"""
Ticket: KC-QA-B3-08
Suite de pruebas unitarias y adversarias para PromptManager y plantillas Jinja2 (prompts/).
Verifica:
1. Carga y renderizado de plantillas v1 ('pytest', 'playwright', 'maestro') sin marcadores {{ }} pendientes.
2. Validación estricta ante la omisión de variables obligatorias (required_vars).
3. Desacoplamiento de prompts hardcodeados en gen_tests.py mediante inspección AST (Cierra QA-F30).
4. Prevención de Path Traversal mediante resolve_safe_path al cargar plantillas.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kallicode_testing.prompts.prompt_manager import PromptManager, PromptTemplateDTO


def test_carga_y_renderizado_plantillas_v1() -> None:
    """
    Prueba 1: Carga 'pytest', 'playwright' y 'maestro' en versión 'v1', las renderiza
    con variables sintéticas y verifica la ausencia de marcadores '{{' o '}}' sin resolver.
    """
    manager = PromptManager()
    available = manager.list_available_templates()
    available_names = {item["template_name"] for item in available if item["version"] == "v1"}
    assert {"pytest", "playwright", "maestro"}.issubset(available_names)

    base_vars = {
        "tenant_id": "tenant_prompts_b308",
        "criterion_id": "AC-01",
        "given": "un usuario autenticado en el portal",
        "when": "aprueba el Gate 1 con comentario técnico",
        "then": "el ticket avanza a estado aprobado y registra auditoría",
        "inventory": ["gates.approval.btn-approve", "gates.approval.input-comment"],
        "context_symbols": [{"name": "aprobar_gate_1", "kind": "function"}],
        "context_tables": [{"esquema": "core", "nombre": "gate_signatures"}],
        "previous_errors": ["SyntaxError: line 1: invalid syntax"],
    }

    for name in ("pytest", "playwright", "maestro"):
        dto = manager.get_template(name=name, version="v1")
        assert isinstance(dto, PromptTemplateDTO)
        assert dto.template_name == name
        assert dto.version == "v1"
        assert len(dto.required_vars) > 0

        sys_rendered, usr_rendered = manager.render_prompt(
            name=name,
            variables=base_vars,
            version="v1",
        )
        assert "tenant_prompts_b308" in sys_rendered
        assert "AC-01" in sys_rendered
        assert "AC-01" in usr_rendered
        assert "un usuario autenticado en el portal" in usr_rendered
        assert "{{" not in sys_rendered and "}}" not in sys_rendered
        assert "{{" not in usr_rendered and "}}" not in usr_rendered


def test_validacion_variables_requeridas_faltantes() -> None:
    """
    Prueba 2: Intenta renderizar plantillas omitiendo variables obligatorias
    (ej. criterion_id, inventory, tenant_id) y verifica que lance ValueError descriptivo.
    """
    manager = PromptManager()

    # Omitir criterion_id e inventory en playwright_v1
    incomplete_vars = {
        "tenant_id": "tenant_b308",
        "given": "dado un estado inicial",
        "when": "ocurre una acción",
        "then": "se verifica el resultado",
    }
    with pytest.raises(ValueError, match="Variables obligatorias faltantes") as exc_info:
        manager.render_prompt("playwright", incomplete_vars, version="v1")

    err_msg = str(exc_info.value)
    assert "criterion_id" in err_msg
    assert "inventory" in err_msg

    # Pasar variable obligatoria vacía o en blanco en pytest_v1
    blank_tenant_vars = {
        "tenant_id": "   ",
        "criterion_id": "AC-02",
        "given": "dado A",
        "when": "cuando B",
        "then": "entonces C",
    }
    with pytest.raises(ValueError, match="tenant_id"):
        manager.render_prompt("pytest", blank_tenant_vars, version="v1")


def test_desacoplamiento_codigo_duro() -> None:
    """
    Prueba 3: Inspecciona gen_tests.py mediante ast.parse para verificar:
    1. Ausencia de constantes monolíticas de prompts hardcodeadas (PLAYWRIGHT_SYSTEM, etc.).
    2. Uso efectivo de PromptManager y render_prompt dentro de gen_tests.py (Cierra QA-F30).
    """
    gen_tests_path = (
        Path(__file__).resolve().parents[2] / "src" / "kallicode_testing" / "gen_tests.py"
    )
    source = gen_tests_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(gen_tests_path))

    banned_prompt_constants = {
        "PLAYWRIGHT_SYSTEM",
        "MAESTRO_SYSTEM",
        "PYTEST_SYSTEM",
        "SYSTEM_PROMPT",
        "USER_PROMPT_TEMPLATE",
    }

    assigned_names: set[str] = set()
    called_attributes: set[str] = set()
    imported_symbols: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_symbols.add(alias.name)

    assert banned_prompt_constants.isdisjoint(assigned_names), (
        f"Constantes de prompt hardcodeadas encontradas en gen_tests.py: "
        f"{banned_prompt_constants & assigned_names}"
    )
    assert "PromptManager" in imported_symbols
    assert "render_prompt" in called_attributes


def test_prevencion_path_traversal_al_cargar_plantilla() -> None:
    """
    Prueba 4: Intenta cargar nombres o versiones maliciosas ('../../etc/passwd', '../secret')
    y verifica el bloqueo inmediato con ValueError mediante resolve_safe_path.
    """
    manager = PromptManager()

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        manager.get_template("../../etc/passwd", version="v1")

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        manager.get_template("../secret", version="v1")

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        manager.get_template("pytest", version="../../etc/passwd")

    with pytest.raises(FileNotFoundError):
        manager.get_template("plantilla_inexistente", version="v99")
