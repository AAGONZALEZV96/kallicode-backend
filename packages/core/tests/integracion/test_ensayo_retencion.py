"""Ensayo integral de retención y minimización de datos (KC-DB-10-6).

Criterio de aceptación: evidencia reproducible, ejecutada en CI, de que el ciclo de vida completo y el descarte seguro
de datos ocurren sin pérdida de trazabilidad de auditoría. El ensayo vive en db/tools/ensayo_retencion.py (también se
ejecuta como paso del CI y publica su informe y los exports como artefacto); esta prueba lo corre y comprueba cada
paso y que no deja rastro en la base.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import psycopg
import pytest

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
HERRAMIENTA = RAIZ / "db" / "tools" / "ensayo_retencion.py"


def _cargar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ensayo_retencion", HERRAMIENTA)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["ensayo_retencion"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


ensayo = _cargar()


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SELECT '[1]'::vector")
    if c.execute("SELECT to_regproc('audit.registrar_export_worm')").fetchone() == (None,):
        pytest.skip("migraciones 054/055 sin aplicar")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def destino(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for f in tmp_path.rglob("*"):  # los exports quedan de solo lectura
        if f.is_file():
            f.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _fallos(inf: object) -> list[str]:
    return [f"{p['paso']}: {p['detalle']}" for p in inf.pasos if not p["ok"]]  # type: ignore[attr-defined]


def test_el_ensayo_completo_pasa_todos_sus_pasos(conn: psycopg.Connection, destino: Path) -> None:
    inf = ensayo.ejecutar(conn, destino)
    assert inf.ok, _fallos(inf)
    # el ensayo cubre las tres piezas: DROP con WORM, TTL e PII, y la validación final
    nombres = " | ".join(p["paso"] for p in inf.pasos)
    for clave in ("sin export", "export WORM", "ancla", "TTL", "PII", "validación final"):
        assert clave in nombres, clave


def test_la_auditoria_solo_se_descarta_tras_el_export(
    conn: psycopg.Connection, destino: Path
) -> None:
    """Criterio central: el orden exigido es purga bloqueada -> export verificado -> DROP; no al revés."""
    inf = ensayo.ejecutar(conn, destino)
    nombres = [p["paso"] for p in inf.pasos]
    i_bloqueo = next(i for i, n in enumerate(nombres) if n.startswith("purga sin export"))
    i_export = next(i for i, n in enumerate(nombres) if n.startswith("export WORM"))
    i_drop = next(i for i, n in enumerate(nombres) if n.startswith("purga con comprobante"))
    assert i_bloqueo < i_export < i_drop
    assert all(inf.pasos[i]["ok"] for i in (i_bloqueo, i_export, i_drop))


def test_el_ensayo_no_deja_nada_en_la_base(conn: psycopg.Connection, destino: Path) -> None:
    def foto() -> tuple[object, ...]:
        return (
            conn.execute("SELECT count(*) FROM audit.worm_exports").fetchone(),
            conn.execute("SELECT count(*) FROM audit.chain_anchors").fetchone(),
            conn.execute("SELECT count(*) FROM core.purgas_log").fetchone(),
            conn.execute("SELECT count(*) FROM core.retention_purgas").fetchone(),
            conn.execute(
                "SELECT action FROM core.retention_policies WHERE entity_name = 'audit.audit_events' AND tenant_id IS NULL"
            ).fetchone(),
            conn.execute(
                "SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit.audit_events'::regclass"
            ).fetchone(),
        )

    antes = foto()
    inf = ensayo.ejecutar(conn, destino)
    assert inf.ok, _fallos(inf)
    conn.rollback()  # lo que hace main()
    assert foto() == antes
    assert antes[4] == ("conservar",)  # la política de la auditoría no cambia


def test_es_reproducible_dos_ejecuciones_seguidas(conn: psycopg.Connection, tmp_path: Path) -> None:
    primera = ensayo.ejecutar(conn, tmp_path / "uno")
    conn.rollback()
    segunda = ensayo.ejecutar(conn, tmp_path / "dos")
    assert primera.ok and segunda.ok, (_fallos(primera), _fallos(segunda))
    assert [p["paso"] for p in primera.pasos] == [p["paso"] for p in segunda.pasos]
    for f in tmp_path.rglob("*"):
        if f.is_file():
            f.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_el_informe_resume_los_pasos_y_el_resultado() -> None:
    inf = ensayo.Informe()
    inf.paso("uno", True, {"x": 1})
    assert inf.ok and "APROBADO" in inf.markdown() and "| 1 | uno | OK |" in inf.markdown()
    inf.paso("dos", False, "mal | roto")
    assert not inf.ok
    md = inf.markdown()
    assert (
        "FALLIDO" in md and "FALLA" in md and "mal / roto" in md
    )  # la barra del detalle no rompe la tabla


def test_la_linea_de_ordenes_genera_el_informe_y_sale_con_cero(destino: Path) -> None:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    informe = destino / "informe.md"
    r = subprocess.run(
        [
            sys.executable,
            str(HERRAMIENTA),
            "--destino",
            str(destino / "exports"),
            "--informe",
            str(informe),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        timeout=300,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "APROBADO" in informe.read_text(encoding="utf-8")
    assert list((destino / "exports").glob("*.worm.jsonl"))


def test_sin_url_de_administrador_la_herramienta_se_niega() -> None:
    env = {k: v for k, v in os.environ.items() if k != "KC_DATABASE_URL_MIGRATIONS"}
    r = subprocess.run(
        [sys.executable, str(HERRAMIENTA), "--destino", "x"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=60,
    )
    assert r.returncode != 0 and "KC_DATABASE_URL_MIGRATIONS" in r.stderr


def test_aunque_se_relaje_el_gancho_el_drop_sigue_abortando_sin_comprobante(
    conn: psycopg.Connection, destino: Path
) -> None:
    """Defensa en profundidad: si alguien relaja core.puede_descartar_particion, el DROP de una partición de
    auditoría aborta igualmente porque audit.anclar_descarte exige el comprobante (y el ensayo lo señala)."""
    conn.execute(
        "CREATE OR REPLACE FUNCTION core.puede_descartar_particion(p_tabla text, p_particion regclass) "
        "RETURNS TABLE (permitido boolean, motivo text) LANGUAGE sql AS $$ SELECT true, NULL::text $$"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="sin comprobante de export WORM"):
        ensayo.ejecutar(conn, destino)
