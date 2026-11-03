"""
Ticket: KC-QA-B3-09
Evaluador de Calidad de Código de Pruebas Generado (code_evaluator.py).
Analiza estilo, longitud de línea (<= 100), importaciones/variables sin uso, convenciones
Playwright (data-testid, ausencia de console.log y any) y seguridad AST (ASTGuard),
generando retroalimentación estructurada (formatted_feedback) para el bucle de reparación LLM.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.ast_guard import ASTGuard
from kallicode_testing.utils.path_utils import resolve_safe_path

SeverityLevel = Literal["error", "warning"]

_TS_CONSOLE_LOG_RE = re.compile(r"\bconsole\.log\s*\(")
_TS_EXPLICIT_ANY_RE = re.compile(r"(?::\s*any\b|\bas\s+any\b|<any>)")
_TS_LOCATOR_CALL_RE = re.compile(r"\.locator\s*\(\s*([\"'`].*?[\"'`])\s*\)")


class LintIssue(BaseModel):
    """Representa una infracción individual de estilo, linter o calidad estática."""

    model_config = ConfigDict(frozen=True)

    line: int
    column: int
    code: str
    message: str
    severity: SeverityLevel = "error"


class EvaluationResult(BaseModel):
    """Resultado consolidado de la evaluación de calidad de un artefacto de prueba."""

    model_config = ConfigDict(frozen=True)

    is_clean: bool
    issues: list[LintIssue] = Field(default_factory=list)
    formatted_feedback: str = ""


def _suggestion_for_code(code: str) -> str:
    """Devuelve una sugerencia accionable para el modelo según el código de regla."""
    suggestions: dict[str, str] = {
        "F401": "Elimine la sentencia 'import' no utilizada o utilice el símbolo importado.",
        "F841": "Elimine la variable local sin uso o utilícela en una aserción 'assert'.",
        "E501": "Divida la línea en múltiples líneas para no superar 100 caracteres.",
        "E999": "Corrija la sintaxis de Python/TypeScript asegurando bloques y paréntesis válidos.",
        "S101": "Reemplace el módulo o función prohibida por utilidades seguras permitidas.",
        "PT01": "Agregue al menos una aserción explícita ('assert ...') dentro de la función.",
        "TS01": "Asegure balanceo de llaves y presencia de bloques test(...) / expect(...).",
        "TS02": "Elimine cualquier llamada a 'console.log' del archivo de prueba.",
        "TS03": "Reemplace el tipo 'any' explícito por un tipo estricto de TypeScript.",
        "A11Y01": "Utilice exclusivamente selectores con '[data-testid=\"vista.zona.elemento\"]'.",
    }
    return suggestions.get(code, "Corrija la infracción reportada siguiendo el estándar de QA.")


def _format_feedback(issues: list[LintIssue]) -> str:
    """Construye el bloque de retroalimentación estructurada listo para inyectar al LLM."""
    if not issues:
        return ""
    lines: list[str] = ["Se detectaron infracciones de calidad/estilo que deben corregirse:"]
    for issue in issues:
        suggestion = _suggestion_for_code(issue.code)
        lines.append(
            f"- Línea {issue.line}, Col {issue.column} [{issue.code}] ({issue.severity}): "
            f"{issue.message} | Sugerencia: {suggestion}"
        )
    return "\n".join(lines)


class _PythonStyleVisitor(ast.NodeVisitor):
    """
    Visitante AST de Python para detectar:
    - Imports no utilizados (F401)
    - Variables locales asignadas y nunca leídas (F841)
    - Funciones de prueba vacías sin aserciones ni llamadas (PT01)
    """

    def __init__(self) -> None:
        self.imported_symbols: dict[str, tuple[int, int]] = {}
        self.loaded_names: set[str] = set()
        self.issues: list[LintIssue] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname if alias.asname else alias.name.split(".")[0]
            self.imported_symbols.setdefault(
                bound_name, (node.lineno, getattr(node, "col_offset", 0) + 1)
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "__future__":
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            bound_name = alias.asname if alias.asname else alias.name
            self.imported_symbols.setdefault(
                bound_name, (node.lineno, getattr(node, "col_offset", 0) + 1)
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.loaded_names.add(node.id)
        self.generic_visit(node)

    def _check_function_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # 1. Verificar cuerpo vacío (solo pass / ellipsis / docstring) en funciones test_*
        if node.name.startswith("test_"):
            meaningful_stmts = [
                stmt
                for stmt in node.body
                if not isinstance(stmt, ast.Pass)
                and not (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and (isinstance(stmt.value.value, str) or stmt.value.value is Ellipsis)
                )
            ]
            if not meaningful_stmts:
                self.issues.append(
                    LintIssue(
                        line=node.lineno,
                        column=getattr(node, "col_offset", 0) + 1,
                        code="PT01",
                        message=f"La función de prueba '{node.name}' está vacía y carece de aserciones.",
                        severity="error",
                    )
                )

        # 2. Detectar variables locales asignadas pero nunca leídas en el cuerpo (F841)
        arg_names = {arg.arg for arg in node.args.args} | {arg.arg for arg in node.args.kwonlyargs}
        if node.args.vararg:
            arg_names.add(node.args.vararg.arg)
        if node.args.kwarg:
            arg_names.add(node.args.kwarg.arg)

        assigned_locals: dict[str, tuple[int, int]] = {}
        loaded_in_func: set[str] = set()

        for subnode in ast.walk(node):
            if isinstance(subnode, ast.Name):
                if isinstance(subnode.ctx, ast.Store):
                    var_name = subnode.id
                    if not var_name.startswith("_") and var_name not in arg_names:
                        assigned_locals.setdefault(
                            var_name,
                            (subnode.lineno, getattr(subnode, "col_offset", 0) + 1),
                        )
                elif isinstance(subnode.ctx, ast.Load):
                    loaded_in_func.add(subnode.id)

        for var_name, (lineno, col) in assigned_locals.items():
            if var_name not in loaded_in_func:
                self.issues.append(
                    LintIssue(
                        line=lineno,
                        column=col,
                        code="F841",
                        message=f"La variable local '{var_name}' está asignada pero nunca se utiliza.",
                        severity="error",
                    )
                )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_scope(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_scope(node)
        self.generic_visit(node)

    def finalize_unused_imports(self) -> None:
        for name, (lineno, col) in self.imported_symbols.items():
            if name not in self.loaded_names:
                self.issues.append(
                    LintIssue(
                        line=lineno,
                        column=col,
                        code="F401",
                        message=f"'{name}' importado pero no utilizado.",
                        severity="error",
                    )
                )


class CodeEvaluator:
    """
    Evaluador programático de estilo, linter y seguridad estática para artefactos
    de prueba generados en Python (Pytest) y TypeScript (Playwright).
    """

    def __init__(
        self,
        line_length_limit: int = 100,
        ast_guard: ASTGuard | None = None,
        allowed_base_dir: Path | None = None,
    ) -> None:
        self.line_length_limit: int = line_length_limit
        self.ast_guard: ASTGuard = ast_guard if ast_guard is not None else ASTGuard()
        self.allowed_base_dir: Path = (
            allowed_base_dir.resolve() if allowed_base_dir is not None else Path.cwd().resolve()
        )

    def _check_line_lengths(self, code: str) -> list[LintIssue]:
        """Verifica que ninguna línea supere self.line_length_limit (Regla E501)."""
        issues: list[LintIssue] = []
        for idx, line in enumerate(code.splitlines(), start=1):
            if len(line) > self.line_length_limit:
                issues.append(
                    LintIssue(
                        line=idx,
                        column=self.line_length_limit + 1,
                        code="E501",
                        message=(
                            f"Longitud de línea ({len(line)} caracteres) excede el "
                            f"máximo permitido de {self.line_length_limit} caracteres."
                        ),
                        severity="error",
                    )
                )
        return issues

    def evaluate_python_style(self, code: str) -> EvaluationResult:
        """
        Evalúa un artefacto Python detectando líneas > 100 caracteres (E501),
        imports sin uso (F401), variables sin uso (F841), bloques vacíos (PT01)
        y violaciones de sintaxis o seguridad reportadas por ASTGuard (E999 / S101).
        """
        issues: list[LintIssue] = self._check_line_lengths(code)

        # Ejecutar validación de seguridad y sintaxis de ASTGuard
        is_guard_valid, guard_errors = self.ast_guard.validate_python_ast(code)
        if not is_guard_valid:
            for err in guard_errors:
                line_num = 1
                line_match = re.search(r"línea\s+(\d+)|line\s+(\d+)", err, re.IGNORECASE)
                if line_match:
                    line_num = int(line_match.group(1) or line_match.group(2))
                rule_code = "E999" if "SyntaxError" in err else "S101"
                issues.append(
                    LintIssue(
                        line=line_num,
                        column=1,
                        code=rule_code,
                        message=err,
                        severity="error",
                    )
                )

        try:
            tree = ast.parse(code)
        except SyntaxError:
            issues.sort(key=lambda item: (item.line, item.column, item.code))
            return EvaluationResult(
                is_clean=False,
                issues=issues,
                formatted_feedback=_format_feedback(issues),
            )

        visitor = _PythonStyleVisitor()
        visitor.visit(tree)
        visitor.finalize_unused_imports()
        issues.extend(visitor.issues)

        issues.sort(key=lambda item: (item.line, item.column, item.code))
        is_clean = len(issues) == 0
        return EvaluationResult(
            is_clean=is_clean,
            issues=issues,
            formatted_feedback=_format_feedback(issues),
        )

    def evaluate_typescript_style(self, code: str) -> EvaluationResult:
        """
        Evalúa un artefacto TypeScript/Playwright verificando longitud de línea (E501),
        estructura Playwright (TS01), ausencia de console.log (TS02), ausencia de any
        explícito (TS03) y uso obligatorio de selectores data-testid (A11Y01).
        """
        issues: list[LintIssue] = self._check_line_lengths(code)

        is_ts_valid, ts_errors = self.ast_guard.validate_typescript_structure(code)
        if not is_ts_valid:
            for err in ts_errors:
                issues.append(
                    LintIssue(
                        line=1,
                        column=1,
                        code="TS01",
                        message=err,
                        severity="error",
                    )
                )

        for idx, line in enumerate(code.splitlines(), start=1):
            console_match = _TS_CONSOLE_LOG_RE.search(line)
            if console_match:
                issues.append(
                    LintIssue(
                        line=idx,
                        column=console_match.start() + 1,
                        code="TS02",
                        message="Uso prohibido de 'console.log' en pruebas E2E de Playwright.",
                        severity="error",
                    )
                )

            any_match = _TS_EXPLICIT_ANY_RE.search(line)
            if any_match:
                issues.append(
                    LintIssue(
                        line=idx,
                        column=any_match.start() + 1,
                        code="TS03",
                        message="Uso prohibido de tipo 'any' explícito en TypeScript.",
                        severity="error",
                    )
                )

            for loc_match in _TS_LOCATOR_CALL_RE.finditer(line):
                selector_arg = loc_match.group(1)
                if "data-testid" not in selector_arg:
                    issues.append(
                        LintIssue(
                            line=idx,
                            column=loc_match.start() + 1,
                            code="A11Y01",
                            message=(
                                f"Selector frágil en locator({selector_arg}): "
                                f"debe utilizar el atributo 'data-testid'."
                            ),
                            severity="error",
                        )
                    )

        issues.sort(key=lambda item: (item.line, item.column, item.code))
        is_clean = len(issues) == 0
        return EvaluationResult(
            is_clean=is_clean,
            issues=issues,
            formatted_feedback=_format_feedback(issues),
        )

    def evaluate_code(self, code: str, target_kind: str = "unit") -> EvaluationResult:
        """
        Punto de entrada principal para evaluar código en memoria según target_kind
        ('unit' / 'api' -> Python, 'e2e' -> TypeScript).
        """
        normalized_kind = target_kind.strip().lower()
        if normalized_kind in {"unit", "api"}:
            return self.evaluate_python_style(code)
        if normalized_kind == "e2e":
            return self.evaluate_typescript_style(code)
        raise ValueError(f"target_kind no soportado '{target_kind}': use 'unit', 'api' o 'e2e'.")

    def evaluate_file(
        self,
        file_path: Path | str,
        target_kind: str | None = None,
        base_dir: Path | None = None,
    ) -> EvaluationResult:
        """
        Evalúa un archivo de prueba en disco garantizando confinamiento estricto
        mediante resolve_safe_path para prevenir ataques de Path Traversal.
        """
        anchor = base_dir.resolve() if base_dir is not None else self.allowed_base_dir
        raw_str = str(file_path)
        if ".." in Path(raw_str).parts or ".." in raw_str:
            resolve_safe_path(anchor, file_path)

        safe_path = resolve_safe_path(anchor, file_path)
        if not safe_path.is_file():
            raise FileNotFoundError(f"Archivo a evaluar no encontrado: {safe_path}")

        inferred_kind = target_kind
        if inferred_kind is None:
            inferred_kind = "e2e" if safe_path.name.endswith((".ts", ".tsx")) else "unit"

        content = safe_path.read_text(encoding="utf-8")
        return self.evaluate_code(content, target_kind=inferred_kind)
