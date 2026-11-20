"""Acceso a la base del cliente: solo lectura y con tiempo límite, en PostgreSQL real (KC-CM-12-6).

Prueba el engine de la ingesta (extract_sql.build_client_engine) y el verificador que se ejecuta en
staging (verificacion_acceso_cliente). La "base del cliente" es la de integración, como en
test_extract_sql.py; el rol de solo lectura se crea y se borra aquí.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from kallicode_codemapping import extract_sql, verificacion_acceso_cliente
from kallicode_codemapping.config import Settings
from kallicode_codemapping.extract_sql import build_client_engine, extract_schema
from kallicode_codemapping.verificacion_acceso_cliente import (
    SQLSTATE_SOLO_LECTURA,
    Comprobacion,
    Informe,
    sqlstate,
    verificar,
)
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import DBAPIError, OperationalError

CLIENT_URL = os.environ.get("CM_TEST_CLIENT_DB_URL") or (
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
).replace("postgresql://", "postgresql+psycopg://", 1)
ESQUEMA = "t126_cliente"
ROL_LECTOR = "kc_cm_t126_lector"
CLAVE_LECTOR = "lector-t126"  # pragma: allowlist secret - rol efímero de la prueba


def _db_disponible() -> bool:
    try:
        create_engine(CLIENT_URL).connect().close()
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_disponible(), reason="Postgres no disponible (make up && make db)"
)


def _settings(url: str = CLIENT_URL, **overrides: Any) -> Settings:
    return Settings(tenant_id="org_t126", repo_url=".", database_url=url, **overrides)


@pytest.fixture
def base_cliente() -> Iterator[str]:
    """Un esquema con una tabla y un rol con SELECT y nada más. Devuelve la URL de ese rol."""
    admin = create_engine(CLIENT_URL)

    def limpiar() -> None:
        with admin.begin() as db:
            db.execute(text(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE"))
            existe = db.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROL_LECTOR}
            ).first()
            if existe:
                db.execute(text(f"DROP OWNED BY {ROL_LECTOR}"))
                db.execute(text(f"DROP ROLE {ROL_LECTOR}"))

    limpiar()
    with admin.begin() as db:
        db.execute(text(f"CREATE SCHEMA {ESQUEMA}"))
        db.execute(text(f"CREATE TABLE {ESQUEMA}.pedidos (id int PRIMARY KEY, total numeric)"))
        db.execute(text(f"CREATE ROLE {ROL_LECTOR} LOGIN PASSWORD '{CLAVE_LECTOR}'"))
        base = db.execute(text("SELECT current_database()")).scalar_one()
        db.execute(text(f'GRANT CONNECT ON DATABASE "{base}" TO {ROL_LECTOR}'))
        db.execute(text(f"GRANT USAGE ON SCHEMA {ESQUEMA} TO {ROL_LECTOR}"))
        db.execute(text(f"GRANT SELECT ON {ESQUEMA}.pedidos TO {ROL_LECTOR}"))
    url = make_url(CLIENT_URL).set(username=ROL_LECTOR, password=CLAVE_LECTOR)
    yield url.render_as_string(hide_password=False)
    limpiar()
    admin.dispose()


def test_verificador_en_verde_y_avisa_si_el_rol_puede_escribir(base_cliente: str) -> None:
    informe = verificar(_settings())  # el rol de integración es superusuario
    assert informe.ok, informe.a_dict()
    nombres = {c.nombre for c in informe.comprobaciones}
    assert {
        "transaccion_solo_lectura",
        "sesion_solo_lectura",
        "statement_timeout",
        "lock_timeout",
        "idle_in_transaction_session_timeout",
        "application_name",
        "escritura_rechazada",
        "escritura_rechazada_tras_reabrir_sesion",
        "transaccion_no_reabre_en_escritura",
        "tiempo_limite_efectivo",
    } <= nombres
    assert informe.privilegios["superusuario"] is True
    assert len(informe.avisos) == 1


def test_verificador_sin_avisos_con_un_rol_de_solo_lectura(base_cliente: str) -> None:
    informe = verificar(_settings(base_cliente))
    assert informe.ok, informe.a_dict()
    assert informe.privilegios == {
        "superusuario": False,
        "bypassrls": False,
        "tablas_con_escritura": 0,
        "esquemas_con_create": 0,
    }
    assert informe.avisos == []


def test_reabrir_la_sesion_en_escritura_no_permite_escribir(base_cliente: str) -> None:
    """La sesión arranca en solo lectura, pero quien la cambie con SET sigue sin poder escribir:
    cada transacción se abre con SET TRANSACTION READ ONLY."""
    engine = build_client_engine(_settings())
    try:
        with engine.connect() as db:
            db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE"))
            db.commit()
            assert db.execute(text("SHOW default_transaction_read_only")).scalar_one() == "off"
            with pytest.raises(DBAPIError) as err:
                db.execute(text(f"INSERT INTO {ESQUEMA}.pedidos VALUES (1, 10)"))
            assert sqlstate(err.value) == SQLSTATE_SOLO_LECTURA
            db.rollback()
            # Tampoco dentro de la transacción: el snapshot ya está tomado (SQLSTATE 25001).
            with pytest.raises(DBAPIError, match="must be set before any query"):
                db.execute(text("SET TRANSACTION READ WRITE"))
            db.rollback()
    finally:
        engine.dispose()
    with create_engine(CLIENT_URL).connect() as admin:
        assert admin.execute(text(f"SELECT count(*) FROM {ESQUEMA}.pedidos")).scalar_one() == 0


def test_lock_timeout_corta_la_espera_por_un_bloqueo_del_cliente(base_cliente: str) -> None:
    admin = create_engine(CLIENT_URL)
    bloqueado, liberar = threading.Event(), threading.Event()

    def bloquear() -> None:
        with admin.begin() as db:
            db.execute(text(f"LOCK TABLE {ESQUEMA}.pedidos IN ACCESS EXCLUSIVE MODE"))
            bloqueado.set()
            liberar.wait(10)

    hilo = threading.Thread(target=bloquear)
    hilo.start()
    engine = build_client_engine(_settings(client_db_lock_timeout_ms=200))
    try:
        assert bloqueado.wait(10)
        with pytest.raises(DBAPIError) as err, engine.connect() as db:
            db.execute(text(f"SELECT * FROM {ESQUEMA}.pedidos"))
        assert sqlstate(err.value) == "55P03"  # lock_not_available
    finally:
        liberar.set()
        hilo.join()
        engine.dispose()
        admin.dispose()


def test_la_extraccion_avisa_de_privilegios_de_escritura_y_sigue(
    base_cliente: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    avisos: list[dict[str, Any]] = []

    class _Logger:
        def info(self, msg: str, extra: dict[str, Any] | None = None) -> None:
            pass

        def warning(self, msg: str, extra: dict[str, Any] | None = None) -> None:
            avisos.append({"msg": msg, **(extra or {})})

    monkeypatch.setattr(extract_sql, "logger", _Logger())
    tablas, _fks = extract_schema(_settings())
    assert any(t["esquema"] == ESQUEMA for t in tablas)
    assert avisos and avisos[-1]["superusuario"] is True

    avisos.clear()
    extract_schema(_settings(base_cliente))
    assert avisos == []  # rol de solo lectura: sin aviso


def test_si_no_se_pueden_leer_los_privilegios_la_extraccion_sigue(
    base_cliente: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    avisos: list[str] = []

    class _Logger:
        def info(self, msg: str, extra: dict[str, Any] | None = None) -> None:
            pass

        def warning(self, msg: str, extra: dict[str, Any] | None = None) -> None:
            avisos.append(str((extra or {}).get("error")))

    def falla(_conn: Any) -> dict[str, Any]:
        raise DBAPIError("SELECT", None, Exception("permiso denegado"))

    monkeypatch.setattr(extract_sql, "logger", _Logger())
    monkeypatch.setattr(extract_sql, "privilegios_escritura", falla)
    tablas, _fks = extract_schema(_settings(base_cliente))
    assert any(t["esquema"] == ESQUEMA for t in tablas)
    assert avisos == ["DBAPIError"]


def test_informe_falla_si_una_comprobacion_falla() -> None:
    informe = Informe(comprobaciones=[Comprobacion("a", True, ""), Comprobacion("b", False, "")])
    assert informe.ok is False and informe.a_dict()["ok"] is False


def test_tiempo_limite_no_efectivo_se_reporta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verificacion_acceso_cliente, "TIMEOUT_SONDA_MS", 60_000)
    comprobacion = verificacion_acceso_cliente._comprobar_tiempo_limite(_settings())
    assert comprobacion.ok is False and "completada" in comprobacion.detalle
