"""Verificación del acceso a la base del cliente: solo lectura y con tiempo límite (KC-CM-12-6).

Se ejecuta en staging dentro de la imagen del worker, con la configuración real del tenant
(variables CM_*), y deja un informe JSON que se adjunta al acta docs/seguridad/revision-cm-ga.md:

    python -m kallicode_codemapping.verificacion_acceso_cliente > acceso_cliente.json

Usa el mismo engine que la ingesta (extract_sql.build_client_engine), así que verifica lo que la
ingesta hace de verdad. Sale con código 1 si alguna comprobación falla. Los privilegios de
escritura del rol son un aviso, no un fallo: la defensa es la transacción de solo lectura.
No imprime el DSN ni ningún dato de la base: solo nombres de ajustes, SQLSTATE y recuentos.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import DBAPIError

from .config import Settings, build_settings
from .extract_sql import (
    APPLICATION_NAME,
    build_client_engine,
    privilegios_escritura,
    tiene_escritura,
)

SQLSTATE_SOLO_LECTURA = "25006"  # read_only_sql_transaction
SQLSTATE_CANCELADA = "57014"  # query_canceled (statement_timeout)
SQLSTATE_MODO_FIJADO = "25001"  # active_sql_transaction: el modo ya no se puede cambiar
TIMEOUT_SONDA_MS = 300


@dataclass
class Comprobacion:
    nombre: str
    ok: bool
    detalle: str


@dataclass
class Informe:
    comprobaciones: list[Comprobacion] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    privilegios: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.comprobaciones)

    def a_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, **asdict(self)}


def sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _ajuste(conn: Connection, nombre: str) -> str:
    return str(conn.exec_driver_sql(f"SHOW {nombre}").scalar_one())


def _ajuste_ms(conn: Connection, nombre: str) -> int:
    fila = conn.exec_driver_sql(
        f"SELECT setting::bigint, unit FROM pg_settings WHERE name = '{nombre}'"
    ).one()
    return int(fila[0]) * (1000 if fila[1] == "s" else 1)


def _escritura_rechazada(engine: Engine, preparar: Callable[[Connection], None] | None) -> str:
    """Intenta escribir; devuelve el SQLSTATE con que la base lo rechaza (o 'aceptada')."""
    with engine.connect() as conn:
        if preparar is not None:
            preparar(conn)
        try:
            conn.exec_driver_sql("CREATE TEMP TABLE kc_cm_sonda_escritura (id int)")
        except DBAPIError as exc:
            conn.rollback()
            return sqlstate(exc) or type(exc).__name__
        conn.rollback()
        return "aceptada"


def _reapertura_rechazada(engine: Engine) -> str:
    """SET TRANSACTION READ WRITE como primera sentencia del código: debe fallar (25001)."""
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql("SET TRANSACTION READ WRITE")
        except DBAPIError as exc:
            conn.rollback()
            return sqlstate(exc) or type(exc).__name__
        conn.rollback()
        return "aceptada"


def _intentar_reabrir_en_escritura(conn: Connection) -> None:
    # Lo que haría un código descuidado o una inyección: volver la sesión a lectura-escritura.
    conn.exec_driver_sql("SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE")
    conn.exec_driver_sql("SET default_transaction_read_only = off")
    conn.commit()


def verificar(settings: Settings) -> Informe:
    informe = Informe()
    anotar = informe.comprobaciones.append
    engine = build_client_engine(settings)
    try:
        with engine.connect() as conn:
            anotar(
                Comprobacion(
                    "transaccion_solo_lectura",
                    _ajuste(conn, "transaction_read_only") == "on",
                    f"transaction_read_only={_ajuste(conn, 'transaction_read_only')}",
                )
            )
            anotar(
                Comprobacion(
                    "sesion_solo_lectura",
                    _ajuste(conn, "default_transaction_read_only") == "on",
                    f"default_transaction_read_only={_ajuste(conn, 'default_transaction_read_only')}",
                )
            )
            for ajuste, esperado in (
                ("statement_timeout", settings.client_db_statement_timeout_ms),
                ("lock_timeout", settings.client_db_lock_timeout_ms),
                ("idle_in_transaction_session_timeout", settings.client_db_idle_tx_timeout_ms),
            ):
                real = _ajuste_ms(conn, ajuste)
                anotar(Comprobacion(ajuste, real == esperado, f"{real} ms (esperado {esperado})"))
            nombre_app = _ajuste(conn, "application_name")
            anotar(Comprobacion("application_name", nombre_app == APPLICATION_NAME, nombre_app))
            informe.privilegios = privilegios_escritura(conn)
            conn.rollback()

        for nombre, preparar in (
            ("escritura_rechazada", None),
            ("escritura_rechazada_tras_reabrir_sesion", _intentar_reabrir_en_escritura),
        ):
            estado = _escritura_rechazada(engine, preparar)
            anotar(Comprobacion(nombre, estado == SQLSTATE_SOLO_LECTURA, f"SQLSTATE {estado}"))
        estado = _reapertura_rechazada(engine)
        anotar(
            Comprobacion(
                "transaccion_no_reabre_en_escritura",
                estado == SQLSTATE_MODO_FIJADO,
                f"SET TRANSACTION READ WRITE: SQLSTATE {estado}",
            )
        )
    finally:
        engine.dispose()

    anotar(_comprobar_tiempo_limite(settings))
    if tiene_escritura(informe.privilegios):
        informe.avisos.append(
            "el rol de la base del cliente tiene privilegios de escritura: pedir al cliente un rol "
            "de solo lectura (la ingesta sigue protegida por transacciones de solo lectura)"
        )
    return informe


def _comprobar_tiempo_limite(settings: Settings) -> Comprobacion:
    """El mecanismo de corte funciona: una sentencia más larga que el límite se cancela."""
    sonda = settings.model_copy(update={"client_db_statement_timeout_ms": TIMEOUT_SONDA_MS})
    engine = build_client_engine(sonda)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT pg_sleep(2)")
        estado = "completada"
    except DBAPIError as exc:
        estado = sqlstate(exc) or type(exc).__name__
    finally:
        engine.dispose()
    return Comprobacion(
        "tiempo_limite_efectivo",
        estado == SQLSTATE_CANCELADA,
        f"pg_sleep(2) con statement_timeout={TIMEOUT_SONDA_MS} ms: SQLSTATE {estado}",
    )


def main() -> int:  # pragma: no cover - punto de entrada en staging
    informe = verificar(build_settings())
    print(json.dumps(informe.a_dict(), ensure_ascii=False, indent=2))
    return 0 if informe.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
