"""
Ticket: KC-QA-B3-05
Mapeador de Trazabilidad entre Pruebas Generadas y Requerimientos (traceability.py).
Construye, registra y reconcilia el mapa bidireccional entre los criterios BDD (AC-n)
en core.spec_criteria y los artefactos de prueba generados (.py y .spec.ts).
Impide el avance a Gate 2 ante cobertura incompleta (422 MATRIZ_INCOMPLETA)
y garantiza aislamiento multi-tenant (Cierra hallazgo QA-F06).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_testing.logger import get_logger
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

TestKind = Literal["unit", "api", "e2e"]

_CRITERION_ID_RE = re.compile(r"^AC-\d+$")
_TS_ANNOTATION_RE = re.compile(
    r'type\s*:\s*["\']criterion["\']\s*,\s*description\s*:\s*["\'](AC-\d+)["\']'
    r'|description\s*:\s*["\'](AC-\d+)["\']\s*,\s*type\s*:\s*["\']criterion["\']',
    re.DOTALL,
)
_TS_TEST_TITLE_RE = re.compile(r'\btest(?:\.only|\.skip)?\s*\(\s*["\'](AC-\d+)\b')


class TraceabilityRecord(BaseModel):
    """Registro inmutable de trazabilidad bidireccional entre criterio BDD, prueba y nodo."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    spec_id: str
    criterion_id: str
    test_path: str
    test_kind: TestKind
    node_id: str


class IncompleteTraceabilityMatrixError(Exception):
    """
    Excepción dura lanzada cuando la reconciliación de cobertura detecta criterios AC-n
    sin cobertura de pruebas al intentar avanzar hacia Gate 2 (422 MATRIZ_INCOMPLETA).
    """

    def __init__(
        self,
        missing_criteria_ids: list[str],
        extra_unmapped_ids: list[str] | None = None,
    ) -> None:
        self.status_code: int = 422
        self.error_code: str = "MATRIZ_INCOMPLETA"
        self.missing_criteria_ids: list[str] = list(missing_criteria_ids)
        self.extra_unmapped_ids: list[str] = list(extra_unmapped_ids or [])
        missing_str = ", ".join(self.missing_criteria_ids) or "especificación vacía"
        super().__init__(
            f"422 MATRIZ_INCOMPLETA: Cobertura BDD insuficiente para Gate 2. "
            f"Criterios faltantes: [{missing_str}]"
        )


class TenantTraceabilityIsolationError(PermissionError):
    """
    Excepción de seguridad lanzada cuando un tenant intenta leer, registrar o asociar
    trazabilidad sobre un spec_id perteneciente a otro tenant distinto.
    """


def _extract_decorator_criterion(decorator: ast.expr) -> str | None:
    """
    Inspecciona un nodo decorador AST de Python buscando @criterio("AC-n")
    o @pytest.mark.criterio("AC-n") y devuelve el ID del criterio si coincide.
    """
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return None

    func = decorator.func
    is_criterio_decorator = (isinstance(func, ast.Name) and func.id == "criterio") or (
        isinstance(func, ast.Attribute) and func.attr == "criterio"
    )
    if not is_criterio_decorator:
        return None

    first_arg = decorator.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        candidate = first_arg.value.strip()
        if _CRITERION_ID_RE.match(candidate):
            return candidate
    return None


class TraceabilityMapper:
    """
    Mapeador de trazabilidad bidireccional entre criterios BDD (core.spec_criteria)
    y artefactos de prueba generados (.py y .spec.ts) con aislamiento multi-tenant.
    """

    def __init__(self, db_session: AsyncSession | None = None) -> None:
        self.db_session: AsyncSession | None = db_session
        # Propiedad estricta de cada spec_id -> tenant_id propietario
        self._spec_owner: dict[str, str] = {}
        # Registros indexados por (tenant_id, spec_id)
        self._records_by_spec: dict[tuple[str, str], list[TraceabilityRecord]] = {}
        # Índices bidireccionales por (tenant_id, criterion_id) y (tenant_id, test_path)
        self._by_criterion: dict[tuple[str, str], list[TraceabilityRecord]] = {}
        self._by_test_path: dict[tuple[str, str], list[TraceabilityRecord]] = {}

    def _validate_tenant_and_spec(self, tenant_id: str, spec_id: str) -> tuple[str, str]:
        """Verifica que tenant_id y spec_id no estén vacíos y respeta el confinamiento por tenant."""
        clean_tenant = tenant_id.strip() if tenant_id else ""
        clean_spec = spec_id.strip() if spec_id else ""
        if not clean_tenant:
            raise ValueError("tenant_id es obligatorio para cualquier operación de trazabilidad.")
        if not clean_spec:
            raise ValueError("spec_id es obligatorio para cualquier operación de trazabilidad.")

        existing_owner = self._spec_owner.get(clean_spec)
        if existing_owner is not None and existing_owner != clean_tenant:
            raise TenantTraceabilityIsolationError(
                f"Violación de aislamiento multi-tenant: spec_id '{clean_spec}' "
                f"pertenece a otro tenant y no puede ser accedido por '{clean_tenant}'."
            )
        return clean_tenant, clean_spec

    def register_mapping(
        self,
        tenant_id: str,
        spec_id: str,
        criterion_id: str,
        test_path: str,
        test_kind: str,
        node_id: str,
    ) -> TraceabilityRecord:
        """
        Registra de forma transaccional e idempotente el vínculo bidireccional entre
        criterion_id, el archivo de prueba generado (test_path) y el nodo del grafo (node_id).
        """
        clean_tenant, clean_spec = self._validate_tenant_and_spec(tenant_id, spec_id)
        clean_criterion = criterion_id.strip()
        if not _CRITERION_ID_RE.match(clean_criterion):
            raise ValueError(
                f"criterion_id inválido '{criterion_id}': debe cumplir el formato 'AC-n'."
            )

        normalized_kind = test_kind.strip().lower()
        if normalized_kind not in {"unit", "api", "e2e"}:
            raise ValueError(
                f"test_kind inválido '{test_kind}': se esperaba 'unit', 'api' o 'e2e'."
            )
        cast_kind = cast(TestKind, normalized_kind)

        clean_path = test_path.strip()
        clean_node = node_id.strip()
        if not clean_path or not clean_node:
            raise ValueError("test_path y node_id no pueden estar vacíos.")

        self._spec_owner[clean_spec] = clean_tenant

        record = TraceabilityRecord(
            tenant_id=clean_tenant,
            spec_id=clean_spec,
            criterion_id=clean_criterion,
            test_path=clean_path,
            test_kind=cast_kind,
            node_id=clean_node,
        )

        spec_key = (clean_tenant, clean_spec)
        spec_list = self._records_by_spec.setdefault(spec_key, [])
        if record not in spec_list:
            spec_list.append(record)

        crit_key = (clean_tenant, clean_criterion)
        crit_list = self._by_criterion.setdefault(crit_key, [])
        if record not in crit_list:
            crit_list.append(record)

        path_key = (clean_tenant, clean_path)
        path_list = self._by_test_path.setdefault(path_key, [])
        if record not in path_list:
            path_list.append(record)

        return record

    def get_mappings_for_spec(self, tenant_id: str, spec_id: str) -> list[TraceabilityRecord]:
        """
        Devuelve los registros de trazabilidad asociados a (tenant_id, spec_id).
        Lanza TenantTraceabilityIsolationError si otro tenant intenta consultar el spec_id.
        """
        clean_tenant, clean_spec = self._validate_tenant_and_spec(tenant_id, spec_id)
        return list(self._records_by_spec.get((clean_tenant, clean_spec), []))

    def get_tests_for_criterion(
        self,
        tenant_id: str,
        criterion_id: str,
    ) -> list[TraceabilityRecord]:
        """Consulta inversa: obtiene todas las pruebas vinculadas a un criterion_id del tenant."""
        clean_tenant = tenant_id.strip()
        if not clean_tenant:
            raise ValueError("tenant_id no puede estar vacío.")
        return list(self._by_criterion.get((clean_tenant, criterion_id.strip()), []))

    def get_criteria_for_test_path(
        self,
        tenant_id: str,
        test_path: str,
    ) -> list[TraceabilityRecord]:
        """Consulta inversa: obtiene todos los criterios cubiertos por un archivo de prueba."""
        clean_tenant = tenant_id.strip()
        if not clean_tenant:
            raise ValueError("tenant_id no puede estar vacío.")
        return list(self._by_test_path.get((clean_tenant, test_path.strip()), []))

    def extract_criteria_from_file(self, file_path: Path, test_kind: str) -> list[str]:
        """
        Inspecciona estáticamente un archivo de prueba (.py o .spec.ts) y extrae los IDs
        de criterios BDD ('AC-n') preservando el orden de aparición sin duplicados.
        """
        if not file_path.is_file():
            raise FileNotFoundError(f"Archivo de prueba no encontrado: {file_path}")

        normalized_kind = test_kind.strip().lower()
        if normalized_kind not in {"unit", "api", "e2e"}:
            raise ValueError(f"test_kind no soportado '{test_kind}': use 'unit', 'api' o 'e2e'.")

        content = file_path.read_text(encoding="utf-8")
        extracted: list[str] = []

        if normalized_kind in {"unit", "api"}:
            tree = ast.parse(content, filename=str(file_path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for decorator in node.decorator_list:
                        crit_id = _extract_decorator_criterion(decorator)
                        if crit_id and crit_id not in extracted:
                            extracted.append(crit_id)
            return extracted

        # Extracción para TypeScript / Playwright (test_kind == "e2e")
        for match in _TS_ANNOTATION_RE.finditer(content):
            crit_id = match.group(1) or match.group(2)
            if crit_id and crit_id not in extracted:
                extracted.append(crit_id)

        for match in _TS_TEST_TITLE_RE.finditer(content):
            crit_id = match.group(1)
            if crit_id and crit_id not in extracted:
                extracted.append(crit_id)

        return extracted

    def reconcile_spec_coverage(
        self,
        spec_criteria_ids: list[str],
        executed_reports: list[dict[str, Any]],
        *,
        raise_on_incomplete: bool = False,
    ) -> tuple[bool, list[str], list[str]]:
        """
        Compara los criterios exigidos por la especificación contra los reportes ejecutados.
        Retorna (is_complete, missing_criteria_ids, extra_unmapped_ids).
        Si raise_on_incomplete=True y falta algún criterio, lanza IncompleteTraceabilityMatrixError
        con código '422 MATRIZ_INCOMPLETA'.
        """
        required_ordered: list[str] = []
        for item in spec_criteria_ids:
            cleaned = item.strip()
            if cleaned and cleaned not in required_ordered:
                required_ordered.append(cleaned)
        required_set = set(required_ordered)

        covered_set: set[str] = set()
        for report in executed_reports:
            status_val = str(report.get("status", report.get("resultado", "passed"))).lower()
            if status_val in {"failed", "falla", "error", "skipped", "escalate"}:
                continue

            direct_id = report.get("criterion_id") or report.get("criterio_id")
            if isinstance(direct_id, str) and direct_id.strip():
                covered_set.add(direct_id.strip())

            criteria_list = report.get("criteria") or report.get("criteria_ids")
            if isinstance(criteria_list, list):
                for cid in criteria_list:
                    if isinstance(cid, str) and cid.strip():
                        covered_set.add(cid.strip())

            annotations = report.get("annotations")
            if isinstance(annotations, list):
                for ann in annotations:
                    if (
                        isinstance(ann, dict)
                        and ann.get("type") == "criterion"
                        and isinstance(ann.get("description"), str)
                    ):
                        covered_set.add(ann["description"].strip())

        missing_criteria_ids = [cid for cid in required_ordered if cid not in covered_set]
        extra_unmapped_ids = sorted(cid for cid in covered_set if cid not in required_set)
        is_complete = len(required_ordered) > 0 and len(missing_criteria_ids) == 0

        if raise_on_incomplete and not is_complete:
            raise IncompleteTraceabilityMatrixError(
                missing_criteria_ids=missing_criteria_ids,
                extra_unmapped_ids=extra_unmapped_ids,
            )

        return is_complete, missing_criteria_ids, extra_unmapped_ids

    def enforce_gate2_coverage(
        self,
        spec_criteria_ids: list[str],
        executed_reports: list[dict[str, Any]],
    ) -> tuple[bool, list[str], list[str]]:
        """
        Valida la cobertura de criterios para el paso a Gate 2 y fuerza error duro
        '422 MATRIZ_INCOMPLETA' (IncompleteTraceabilityMatrixError) si falta algún criterio.
        """
        return self.reconcile_spec_coverage(
            spec_criteria_ids=spec_criteria_ids,
            executed_reports=executed_reports,
            raise_on_incomplete=True,
        )

    async def fetch_spec_criteria_from_db(self, tenant_id: str, spec_id: str) -> list[str]:
        """
        Consulta core.spec_criteria en PostgreSQL 16 bajo aislamiento RLS estricto
        fijando app.tenant_id = tenant_id en la transacción actual.
        """
        clean_tenant, clean_spec = self._validate_tenant_and_spec(tenant_id, spec_id)
        if self.db_session is None:
            raise RuntimeError("db_session no configurada en TraceabilityMapper.")

        await self.db_session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": clean_tenant},
        )
        result = await self.db_session.execute(
            text(
                """
                SELECT criterio_id
                FROM core.spec_criteria
                WHERE tenant_id = :tenant_id
                  AND spec_id = :spec_id
                ORDER BY criterio_id ASC
                """
            ),
            {"tenant_id": clean_tenant, "spec_id": clean_spec},
        )
        return [str(row[0]) for row in result.fetchall()]

    def export_json(self, tenant_id: str, spec_id: str, base_dir: Path, filename: str) -> Path:
        """
        Exporta el mapa de trazabilidad de (tenant_id, spec_id) a un archivo JSON
        validando confinamiento de ruta mediante resolve_safe_path.
        """
        records = self.get_mappings_for_spec(tenant_id, spec_id)
        safe_target = resolve_safe_path(base_dir, filename)
        payload = {
            "tenant_id": tenant_id.strip(),
            "spec_id": spec_id.strip(),
            "mappings": [rec.model_dump() for rec in records],
        }
        safe_target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return safe_target
