"""
Ticket: KC-QA-B3-07
Ingestor de Inventario de Selectores UI (data-testid) desde Portal (inventory_client.py).
Construye y proporciona el inventario inmutable de selectores data-testid ('vista.zona.elemento')
combinando consultas multi-tenant sobre el grafo cm.* en PostgreSQL 16 y escaneo estático
confinado (resolve_safe_path) de componentes React (.tsx / .jsx).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_testing.logger import get_logger
from kallicode_testing.utils.path_utils import resolve_safe_path

logger = get_logger()

SelectorKind = Literal["button", "input", "table", "card", "modal", "link"]

_SELECTOR_CONVENTION_RE = re.compile(r"^[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+$")
_CANDIDATE_TOKEN_RE = re.compile(r"\b([a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+)\b")

_JSX_ELEMENT_WITH_TESTID_RE = re.compile(
    r"<(?P<tag>[A-Za-z0-9_.-]+)(?P<before>[^>]*?)"
    r'(?:data-testid|(?<![\w-])testid)\s*=\s*["\'](?P<testid>[^"\']+)["\']'
    r"(?P<after>[^>]*?)>(?P<inner>[^<]*)",
    re.DOTALL,
)
_BARE_TESTID_ATTR_RE = re.compile(
    r'(?:data-testid|(?<![\w-])testid)\s*=\s*["\'](?P<testid>[^"\']+)["\']'
)
_ARIA_OR_PLACEHOLDER_RE = re.compile(
    r'(?:aria-label|placeholder|title)\s*=\s*["\'](?P<label>[^"\']+)["\']'
)


class SelectorDTO(BaseModel):
    """DTO inmutable que representa un selector UI data-testid del portal."""

    model_config = ConfigDict(frozen=True)

    screen: str
    testid: str
    kind: str
    label: str | None = None
    file_path: str | None = None


def _infer_selector_kind(testid: str, tag_name: str = "") -> SelectorKind:
    """
    Determina el tipo semántico del componente UI ('button', 'input', 'table', 'card',
    'modal', 'link') a partir de la etiqueta JSX/HTML y los segmentos del testid.
    """
    lower_tag = tag_name.strip().lower()
    parts = testid.lower().split(".")
    zone = parts[1] if len(parts) > 1 else ""
    element = parts[2] if len(parts) > 2 else ""
    combined = f"{zone}.{element}"

    if lower_tag in {"button", "btn"} or any(
        tok in combined
        for tok in ("btn", "button", "submit", "entrar", "salir", "aprobar", "rechazar", "nuevo")
    ):
        return "button"
    if lower_tag in {"input", "textarea", "select"} or any(
        tok in combined for tok in ("input", "field", "search", "filtro", "correo", "clave", "form")
    ):
        return "input"
    if lower_tag in {"table", "tr", "td", "th", "tbody", "thead"} or any(
        tok in combined for tok in ("table", "tabla", "row", "fila", "col", "list")
    ):
        return "table"
    if lower_tag in {"dialog", "modal"} or any(
        tok in combined for tok in ("modal", "dialog", "drawer", "popup")
    ):
        return "modal"
    if lower_tag in {"a", "link", "navlink"} or any(
        tok in combined for tok in ("link", "nav", "tab", "breadcrumb")
    ):
        return "link"
    return "card"


def _extract_label_from_jsx(attrs: str, inner_text: str) -> str | None:
    """Extrae una etiqueta descriptiva desde aria-label/placeholder/title o texto interno JSX."""
    attr_match = _ARIA_OR_PLACEHOLDER_RE.search(attrs)
    if attr_match:
        candidate = attr_match.group("label").strip()
        if candidate:
            return candidate

    cleaned_inner = " ".join(inner_text.strip().split())
    if cleaned_inner and not cleaned_inner.startswith("{"):
        return cleaned_inner
    return None


class UIInventoryClient:
    """
    Cliente híbrido de inventario de selectores UI (data-testid).
    1. Consulta cm.symbols y cm.files (con aislamiento multi-tenant RLS) en PostgreSQL 16.
    2. Ejecuta fallback automático a escaneo estático de componentes .tsx / .jsx cuando
       no hay sesión de BD o el grafo del tenant está vacío.
    """

    def __init__(
        self,
        db_session: AsyncSession | None = None,
        portal_root: Path | None = None,
    ) -> None:
        self.db_session: AsyncSession | None = db_session
        default_portal = Path(__file__).resolve().parents[4] / "kallicode-portal" / "src"
        self.portal_root: Path = portal_root if portal_root is not None else default_portal

    def validate_selector_convention(self, testid: str) -> bool:
        """
        Verifica el cumplimiento estricto de la convención 'vista.zona.elemento'
        bajo el patrón ^[a-z0-9_-]+\\.[a-z0-9_-]+\\.[a-z0-9_-]+$.
        """
        if not testid or not isinstance(testid, str):
            return False
        return bool(_SELECTOR_CONVENTION_RE.match(testid.strip()))

    def _constrain_portal_directory(self, portal_dir: Path) -> Path:
        """
        Valida que portal_dir sea seguro y no contenga secuencias de Path Traversal ('..')
        ni escape fuera del directorio raíz autorizado mediante resolve_safe_path.
        """
        raw_str = str(portal_dir)
        if ".." in Path(raw_str).parts or ".." in raw_str:
            base_anchor = self.portal_root.resolve() if self.portal_root else Path.cwd().resolve()
            resolve_safe_path(base_anchor, portal_dir)

        if self.portal_root is not None and self.portal_root.exists():
            base_root = self.portal_root.resolve()
            target_resolved = (
                portal_dir.resolve()
                if portal_dir.is_absolute()
                else (base_root / portal_dir).resolve()
            )
            if not target_resolved.is_relative_to(base_root):
                resolve_safe_path(base_root, portal_dir)
            return target_resolved

        return resolve_safe_path(Path.cwd(), portal_dir)

    def scan_portal_source(self, portal_dir: Path) -> list[SelectorDTO]:
        """
        Recorre archivos .tsx y .jsx dentro de portal_dir confinando todas las rutas
        con resolve_safe_path y extrae los selectores data-testid que cumplen 'vista.zona.elemento'.
        """
        safe_portal_dir = self._constrain_portal_directory(portal_dir)
        if not safe_portal_dir.exists() or not safe_portal_dir.is_dir():
            return []

        selectors_by_key: dict[tuple[str, str], SelectorDTO] = {}

        for root_str, _dirs, files in os.walk(safe_portal_dir, followlinks=False):
            root_path = Path(root_str)
            for filename in sorted(files):
                if not filename.endswith((".tsx", ".jsx")):
                    continue
                if filename.endswith((".test.tsx", ".spec.tsx", ".test.jsx", ".spec.jsx")):
                    continue

                candidate_path = root_path / filename
                safe_file = resolve_safe_path(safe_portal_dir, candidate_path)
                rel_path = str(safe_file.relative_to(safe_portal_dir)).replace("\\", "/")

                try:
                    source_code = safe_file.read_text(encoding="utf-8")
                except OSError:
                    continue

                for match in _JSX_ELEMENT_WITH_TESTID_RE.finditer(source_code):
                    raw_testid = match.group("testid").strip()
                    if not self.validate_selector_convention(raw_testid):
                        continue

                    tag_name = match.group("tag")
                    attrs_combined = f"{match.group('before')} {match.group('after')}"
                    inner_text = match.group("inner")
                    screen = raw_testid.split(".")[0]
                    kind = _infer_selector_kind(raw_testid, tag_name)
                    label = _extract_label_from_jsx(attrs_combined, inner_text)

                    dto = SelectorDTO(
                        screen=screen,
                        testid=raw_testid,
                        kind=kind,
                        label=label,
                        file_path=rel_path,
                    )
                    selectors_by_key.setdefault((raw_testid, rel_path), dto)

                for bare_match in _BARE_TESTID_ATTR_RE.finditer(source_code):
                    raw_testid = bare_match.group("testid").strip()
                    if not self.validate_selector_convention(raw_testid):
                        continue
                    key = (raw_testid, rel_path)
                    if key not in selectors_by_key:
                        screen = raw_testid.split(".")[0]
                        kind = _infer_selector_kind(raw_testid)
                        selectors_by_key[key] = SelectorDTO(
                            screen=screen,
                            testid=raw_testid,
                            kind=kind,
                            label=None,
                            file_path=rel_path,
                        )

        return list(selectors_by_key.values())

    async def _query_db_inventory(self, tenant_id: str) -> list[SelectorDTO]:
        """Consulta cm.symbols y cm.files en PostgreSQL bajo aislamiento RLS del tenant."""
        if self.db_session is None:
            return []

        await self.db_session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        query = text(
            """
            SELECT s.name,
                   s.kind AS symbol_kind,
                   s.signature,
                   s.descripcion,
                   f.path AS file_path
            FROM cm.symbols s
            JOIN cm.files f
              ON f.tenant_id = s.tenant_id
             AND f.id = s.file_id
            WHERE s.tenant_id = :tenant_id
              AND f.tenant_id = :tenant_id
            ORDER BY f.path ASC, s.start_line ASC
            """
        )
        result = await self.db_session.execute(query, {"tenant_id": tenant_id})
        rows = result.mappings().all()

        extracted: dict[str, SelectorDTO] = {}
        for row in rows:
            name_str = str(row.get("name") or "")
            sig_str = str(row.get("signature") or "")
            desc_val = row.get("descripcion")
            desc_str = str(desc_val) if desc_val is not None else None
            file_path_str = str(row.get("file_path") or "")

            candidates: list[str] = []
            if self.validate_selector_convention(name_str):
                candidates.append(name_str.strip())

            for attr_match in _BARE_TESTID_ATTR_RE.finditer(sig_str):
                cand = attr_match.group("testid").strip()
                if self.validate_selector_convention(cand):
                    candidates.append(cand)

            for tok_match in _CANDIDATE_TOKEN_RE.finditer(sig_str):
                cand = tok_match.group(1).strip()
                if self.validate_selector_convention(cand) and cand not in candidates:
                    candidates.append(cand)

            for testid in candidates:
                if testid in extracted:
                    continue
                screen = testid.split(".")[0]
                kind = _infer_selector_kind(testid, sig_str)
                extracted[testid] = SelectorDTO(
                    screen=screen,
                    testid=testid,
                    kind=kind,
                    label=desc_str,
                    file_path=file_path_str or None,
                )

        return list(extracted.values())

    async def get_inventory(
        self,
        tenant_id: str,
        modules: list[str] | None = None,
    ) -> list[SelectorDTO]:
        """
        Obtiene el inventario de selectores UI para el tenant_id indicado.
        Intenta primero desde PostgreSQL (cm.symbols / cm.files) y, si no hay sesión,
        el grafo está vacío o falla la consulta, activa automáticamente el fallback
        al escaneo estático de componentes TSX/JSX sin lanzar excepciones.
        """
        clean_tenant = tenant_id.strip() if tenant_id else ""
        if not clean_tenant:
            raise ValueError("tenant_id es obligatorio para consultar el inventario UI.")

        selectors: list[SelectorDTO] = []
        if self.db_session is not None:
            try:
                selectors = await self._query_db_inventory(clean_tenant)
            except SQLAlchemyError as exc:
                logger.warning(
                    "inventory_client.db_fallback",
                    tenant_id=clean_tenant,
                    error=str(exc),
                )
                selectors = []

        if not selectors and self.portal_root is not None and self.portal_root.exists():
            try:
                selectors = self.scan_portal_source(self.portal_root)
            except ValueError:
                selectors = []

        if modules:
            normalized_modules = {m.strip().lower() for m in modules if m and m.strip()}
            if normalized_modules:
                selectors = [
                    sel
                    for sel in selectors
                    if sel.screen.lower() in normalized_modules
                    or any(mod in (sel.file_path or "").lower() for mod in normalized_modules)
                ]

        return selectors
