"""
Ticket: KC-QA-B3-03
Validador Estático AST y Guard de Sintaxis para Código Generado (ast_guard.py).
Analiza el árbol de sintaxis abstracta (AST) en Python y la estructura en TypeScript/Playwright,
bloqueando SyntaxErrors e importaciones/llamadas peligrosas (Cierra hallazgo QA-F04).
"""

from __future__ import annotations

import ast
import re

DEFAULT_BANNED_IMPORTS: frozenset[str] = frozenset(
    {
        "subprocess",
        "os",
        "sys",
        "socket",
        "pty",
        "shutil",
        "importlib",
        "ctypes",
    }
)

DEFAULT_BANNED_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
    }
)

_TS_PLAYWRIGHT_PATTERN = re.compile(r"\b(?:test|expect)\s*\(")
_TS_DANGEROUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bchild_process\b"), "Módulo peligroso bloqueado en TS: 'child_process'"),
    (re.compile(r"\bexecSync\s*\("), "Llamada peligrosa bloqueada en TS: 'execSync'"),
    (re.compile(r"\bspawnSync\s*\("), "Llamada peligrosa bloqueada en TS: 'spawnSync'"),
    (re.compile(r"\bprocess\.exit\s*\("), "Llamada peligrosa bloqueada en TS: 'process.exit'"),
    (re.compile(r"\beval\s*\("), "Llamada peligrosa bloqueada en TS: 'eval'"),
)


class _SecurityASTVisitor(ast.NodeVisitor):
    """
    Visitante del árbol AST de Python que inspecciona nodos Import, ImportFrom,
    Call, FunctionDef y AsyncFunctionDef en busca de violaciones de seguridad.
    """

    def __init__(self, banned_imports: set[str], banned_calls: set[str]) -> None:
        self.banned_imports: set[str] = banned_imports
        self.banned_calls: set[str] = banned_calls
        self.errors: list[str] = []
        self.has_test_function: bool = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name.startswith("test_"):
            self.has_test_function = True
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name.startswith("test_"):
            self.has_test_function = True
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_module = alias.name.split(".")[0]
            if alias.name in self.banned_imports or root_module in self.banned_imports:
                self.errors.append(
                    f"Import prohibido detectado en línea {node.lineno}: '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_module = node.module.split(".")[0]
            if node.module in self.banned_imports or root_module in self.banned_imports:
                self.errors.append(
                    f"ImportFrom prohibido detectado en línea {node.lineno}: '{node.module}'"
                )
        for alias in node.names:
            if alias.name in self.banned_imports or alias.name in self.banned_calls:
                self.errors.append(
                    f"Símbolo prohibido importado en línea {node.lineno}: '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.banned_calls:
                self.errors.append(
                    f"Llamada peligrosa bloqueada en línea {node.lineno}: '{func_name}()'"
                )
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in self.banned_calls:
                self.errors.append(
                    f"Método peligroso bloqueado en línea {node.lineno}: '.{attr_name}()'"
                )
            elif isinstance(node.func.value, ast.Name):
                base_obj = node.func.value.id
                if base_obj in self.banned_imports:
                    self.errors.append(
                        f"Invocación sobre módulo prohibido en línea {node.lineno}: "
                        f"'{base_obj}.{attr_name}()'"
                    )
        self.generic_visit(node)


class ASTGuard:
    """
    Validador estático AST y guardia de sintaxis para código de prueba autogenerado
    por modelos de IA (Pytest en Python y Playwright en TypeScript).
    """

    def __init__(
        self,
        banned_imports: set[str] | None = None,
        banned_calls: set[str] | None = None,
    ) -> None:
        self.banned_imports: set[str] = (
            set(banned_imports) if banned_imports is not None else set(DEFAULT_BANNED_IMPORTS)
        )
        self.banned_calls: set[str] = (
            set(banned_calls) if banned_calls is not None else set(DEFAULT_BANNED_CALLS)
        )

    def validate(self, code: str, target_kind: str = "unit") -> tuple[bool, list[str]]:
        """
        Punto de entrada principal de validación estática.
        Enruta según target_kind ('unit' -> Python AST, 'e2e' -> TypeScript/Playwright).
        """
        if not code or not code.strip():
            return False, ["El código generado está vacío."]

        if target_kind == "e2e":
            return self.validate_typescript_structure(code)
        return self.validate_python_ast(code)

    def validate_python_ast(self, code: str) -> tuple[bool, list[str]]:
        """
        Parsea el código Python con ast.parse y recorre el árbol con ast.NodeVisitor
        detectando SyntaxErrors, módulos prohibidos y llamadas bloqueadas.
        """
        if not code or not code.strip():
            return False, ["El código Python está vacío."]

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            line_num = exc.lineno if exc.lineno is not None else 0
            return False, [f"SyntaxError: line {line_num}: {exc.msg}"]

        visitor = _SecurityASTVisitor(
            banned_imports=self.banned_imports,
            banned_calls=self.banned_calls,
        )
        visitor.visit(tree)

        errors = list(visitor.errors)
        if not visitor.has_test_function:
            errors.append("El código Python no define ninguna función 'def test_*'.")

        return len(errors) == 0, errors

    def validate_typescript_structure(self, code: str) -> tuple[bool, list[str]]:
        """
        Valida la estructura sintáctica de un spec TypeScript/Playwright:
        1. Balanceo de llaves {}, corchetes [] y paréntesis ().
        2. Presencia de bloques test(...) o expect(...).
        3. Bloqueo de patrones peligrosos (child_process, execSync, process.exit).
        """
        if not code or not code.strip():
            return False, ["El código TypeScript E2E está vacío."]

        errors: list[str] = []

        if not _TS_PLAYWRIGHT_PATTERN.search(code):
            errors.append(
                "Estructura Playwright inválida: no se encontró 'test(...)' ni 'expect(...)'."
            )

        if not self._check_balanced_delimiters(code):
            errors.append(
                "Desbalanceo sintáctico de llaves '{}', corchetes '[]' o paréntesis '()' en spec E2E."
            )

        for pattern, message in _TS_DANGEROUS_PATTERNS:
            if pattern.search(code):
                errors.append(message)

        return len(errors) == 0, errors

    @staticmethod
    def _check_balanced_delimiters(code: str) -> bool:
        """Verifica el balanceo exacto de (), [] y {} ignorando contenido dentro de strings."""
        pairs = {")": "(", "]": "[", "}": "{"}
        openers = set(pairs.values())
        stack: list[str] = []
        in_quote: str | None = None
        escaped = False

        for ch in code:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if in_quote is not None:
                if ch == in_quote:
                    in_quote = None
                continue
            if ch in ("'", '"', "`"):
                in_quote = ch
                continue
            if ch in openers:
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack[-1] != pairs[ch]:
                    return False
                stack.pop()

        return len(stack) == 0 and in_quote is None
