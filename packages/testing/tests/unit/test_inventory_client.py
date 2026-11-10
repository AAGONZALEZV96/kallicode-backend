"""
Ticket: KC-QA-B3-07
Suite de pruebas unitarias y adversarias para UIInventoryClient (inventory_client.py).
Verifica:
1. Escaneo estático de componentes .tsx / .jsx y validación de convención 'vista.zona.elemento'.
2. Consulta de inventario desde cm.symbols / cm.files en PostgreSQL 16 con aislamiento RLS por tenant.
3. Fallback automático hacia escaneo estático cuando la base de datos o el grafo están vacíos.
4. Prevención de Path Traversal mediante resolve_safe_path al escanear el directorio del portal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kallicode_testing.inventory_client import SelectorDTO, UIInventoryClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_escaneo_estatico_componentes_tsx(tmp_path: Path) -> None:
    """
    Prueba 1: Crea componentes sintéticos con data-testid="tickets.table.row" y
    data-testid="login.form.btn-submit", validando que scan_portal_source extraiga
    correctamente los SelectorDTO y filtre selectores fuera de la convención vista.zona.elemento.
    """
    vistas_dir = tmp_path / "vistas"
    vistas_dir.mkdir(parents=True)

    tickets_tsx = vistas_dir / "Tickets.tsx"
    tickets_tsx.write_text(
        "export function TicketsView() {\n"
        "  return (\n"
        "    <div>\n"
        '      <table data-testid="tickets.table.main" aria-label="Tabla de tickets">\n'
        "        <tbody>\n"
        '          <tr data-testid="tickets.table.row">Fila de ticket</tr>\n'
        "        </tbody>\n"
        "      </table>\n"
        '      <div data-testid="selector_invalido_sin_puntos">Ignorar</div>\n'
        "    </div>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )

    login_jsx = vistas_dir / "Login.jsx"
    login_jsx.write_text(
        "export function LoginForm() {\n"
        "  return (\n"
        "    <form>\n"
        '      <input data-testid="login.form.input-email" placeholder="Correo electrónico" />\n'
        '      <button data-testid="login.form.btn-submit" aria-label="Iniciar sesión">Entrar</button>\n'
        '      <a data-testid="login.nav.link-reset">Recuperar</a>\n'
        '      <dialog data-testid="login.modal.dialog-mfa">MFA</dialog>\n'
        "    </form>\n"
        "  );\n"
        "}\n",
        encoding="utf-8",
    )

    client = UIInventoryClient(portal_root=tmp_path)

    # Validar el validador de convención vista.zona.elemento
    assert client.validate_selector_convention("tickets.table.row") is True
    assert client.validate_selector_convention("login.form.btn-submit") is True
    assert client.validate_selector_convention("selector_invalido_sin_puntos") is False
    assert client.validate_selector_convention("MAYUSCULAS.ZONA.BTN") is False
    assert client.validate_selector_convention("") is False

    selectors = client.scan_portal_source(tmp_path)
    assert len(selectors) == 6

    by_testid: dict[str, SelectorDTO] = {sel.testid: sel for sel in selectors}
    assert "tickets.table.row" in by_testid
    assert "login.form.btn-submit" in by_testid
    assert "selector_invalido_sin_puntos" not in by_testid

    row_dto = by_testid["tickets.table.row"]
    assert row_dto.screen == "tickets"
    assert row_dto.kind == "table"
    assert row_dto.label == "Fila de ticket"
    assert row_dto.file_path == "vistas/Tickets.tsx"

    btn_dto = by_testid["login.form.btn-submit"]
    assert btn_dto.screen == "login"
    assert btn_dto.kind == "button"
    assert btn_dto.label == "Iniciar sesión"
    assert btn_dto.file_path == "vistas/Login.jsx"

    assert by_testid["login.form.input-email"].kind == "input"
    assert by_testid["login.nav.link-reset"].kind == "link"
    assert by_testid["login.modal.dialog-mfa"].kind == "modal"


async def test_consulta_inventario_desde_cm_db(db_session: AsyncSession) -> None:
    """
    Prueba 2: Inserta datos sintéticos en cm.symbols y cm.files aislados por tenant
    y verifica que Tenant A reciba exclusivamente los selectores pertenecientes a Tenant A.
    """
    tenant_a = "tenant_inv_alpha_b307"
    tenant_b = "tenant_inv_beta_b307"

    for tenant_id, prefix, testid_val, screen_file in [
        (tenant_a, "alpha", "tickets.list.btn-create", "src/vistas/Tickets.tsx"),
        (tenant_b, "beta", "dashboard.kpis.card-total", "src/vistas/Dashboard.tsx"),
    ]:
        await db_session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        await db_session.execute(
            text(
                """
                INSERT INTO core.organizations (id, nombre)
                VALUES (:id, :nombre)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": tenant_id, "nombre": f"Org {prefix}"},
        )
        snap_id = f"snap_b307_{prefix}"
        file_id = f"file_b307_{prefix}"
        sym_id = f"sym_b307_{prefix}"

        await db_session.execute(
            text(
                """
                INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado)
                VALUES (:id, :tenant_id, 'sha123456', 'main', 'completo')
                """
            ),
            {"id": snap_id, "tenant_id": tenant_id},
        )
        await db_session.execute(
            text(
                """
                INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id)
                VALUES (:id, :tenant_id, :path, 'typescript', 'hash123', :snap_id)
                """
            ),
            {"id": file_id, "tenant_id": tenant_id, "path": screen_file, "snap_id": snap_id},
        )
        await db_session.execute(
            text(
                """
                INSERT INTO cm.symbols (
                    id, tenant_id, file_id, kind, name, start_line, end_line, signature, descripcion
                )
                VALUES (
                    :id, :tenant_id, :file_id, 'function', :name, 1, 20, :signature, :descripcion
                )
                """
            ),
            {
                "id": sym_id,
                "tenant_id": tenant_id,
                "file_id": file_id,
                "name": f"Comp_{prefix}",
                "signature": f'<button data-testid="{testid_val}">Acción</button>',
                "descripcion": f"Selector de {prefix}",
            },
        )

    client = UIInventoryClient(db_session=db_session)

    inv_a = await client.get_inventory(tenant_id=tenant_a)
    assert len(inv_a) == 1
    assert inv_a[0].testid == "tickets.list.btn-create"
    assert inv_a[0].screen == "tickets"
    assert inv_a[0].kind == "button"
    assert inv_a[0].label == "Selector de alpha"
    assert inv_a[0].file_path == "src/vistas/Tickets.tsx"

    inv_b = await client.get_inventory(tenant_id=tenant_b)
    assert len(inv_b) == 1
    assert inv_b[0].testid == "dashboard.kpis.card-total"
    assert inv_b[0].screen == "dashboard"
    assert inv_b[0].kind == "card"

    # Filtrado opcional por módulo
    filtered_a = await client.get_inventory(tenant_id=tenant_a, modules=["tickets"])
    assert len(filtered_a) == 1
    empty_filtered = await client.get_inventory(tenant_id=tenant_a, modules=["billing"])
    assert empty_filtered == []


async def test_fallback_a_escaneo_estatico_cuando_db_vacia(
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """
    Prueba 3: Consulta una base sin snapshots ni ingesta para un tenant nuevo y verifica
    que se active automáticamente el fallback hacia el escaneo estático sin excepciones.
    """
    comp_file = tmp_path / "GateApproval.tsx"
    comp_file.write_text(
        "export const GateApproval = () => (\n"
        '  <button data-testid="gates.approval.btn-approve" aria-label="Aprobar Gate">OK</button>\n'
        ");\n",
        encoding="utf-8",
    )

    client_with_empty_db = UIInventoryClient(db_session=db_session, portal_root=tmp_path)
    selectors = await client_with_empty_db.get_inventory(tenant_id="tenant_sin_grafo_b307")

    assert len(selectors) == 1
    assert selectors[0].testid == "gates.approval.btn-approve"
    assert selectors[0].screen == "gates"
    assert selectors[0].kind == "button"
    assert selectors[0].label == "Aprobar Gate"

    # También debe funcionar cuando db_session es None (modo 100% offline)
    client_offline = UIInventoryClient(db_session=None, portal_root=tmp_path)
    offline_selectors = await client_offline.get_inventory(tenant_id="tenant_offline_b307")
    assert len(offline_selectors) == 1
    assert offline_selectors[0].testid == "gates.approval.btn-approve"


async def test_prevencion_path_traversal_al_escanear_portal(tmp_path: Path) -> None:
    """
    Prueba 4: Intenta rutas maliciosas (ej. '../../etc/') al invocar scan_portal_source
    y verifica que resolve_safe_path lance ValueError impidiendo cualquier fuga de directorio.
    """
    client = UIInventoryClient(portal_root=tmp_path)

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        client.scan_portal_source(Path("../../etc/"))

    with pytest.raises(ValueError, match="Path-Traversal detectado"):
        client.scan_portal_source(Path("/etc/passwd"))
