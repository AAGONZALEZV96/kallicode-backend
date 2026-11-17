"""RPO/RTO declarados y ensayo de restauración PITR (KC-DB-11-1).

Aquí se prueban las piezas deterministas: los objetivos declarados y su aprobación (core.objetivos_recuperacion), el
estado del archivado de WAL, el archive_command (db/tools/archivar_wal.sh) y el acta. El ensayo completo con
contenedores Docker (db/tools/ensayo_pitr.py) tarda minutos: se ejecuta solo si KC_PITR_ENSAYO=1 (y en el flujo
programado .github/workflows/pitr.yml).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
SCRIPT = RAIZ / "db" / "tools" / "archivar_wal.sh"
HERRAMIENTA = RAIZ / "db" / "tools" / "ensayo_pitr.py"


def _cargar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ensayo_pitr", HERRAMIENTA)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["ensayo_pitr"] = modulo
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
    if c.execute("SELECT to_regclass('core.objetivos_recuperacion')").fetchone() == (None,):
        c.close()
        pytest.skip("migración 061 sin aplicar")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


# ---------------------------------------------------------------------------------------------------------
# Objetivos declarados
# ---------------------------------------------------------------------------------------------------------
def test_la_propuesta_del_ticket_esta_declarada_y_sin_aprobar(conn: psycopg.Connection) -> None:
    fila = conn.execute(
        "SELECT rpo_minutos, rto_minutos, estado, aprobado_por FROM core.objetivos_recuperacion ORDER BY id LIMIT 1"
    ).fetchone()
    assert fila == (5, 60, "propuesto", None)  # aprobar es una decisión humana, no del ensayo


def test_aprobar_exige_quien_y_el_acta_y_deja_uno_solo_aprobado(conn: psycopg.Connection) -> None:
    obj = conn.execute(
        "SELECT id FROM core.objetivos_recuperacion WHERE estado = 'propuesto' ORDER BY id LIMIT 1"
    ).fetchone()
    assert obj is not None
    for por, acta in (("", "ACTA-1"), ("Board", ""), ("  ", "ACTA-1")):
        with pytest.raises(errors.InvalidParameterValue), conn.transaction():
            conn.execute(
                "SELECT core.aprobar_objetivos_recuperacion(%s, %s, %s)", (obj[0], por, acta)
            )

    conn.execute(
        "SELECT core.aprobar_objetivos_recuperacion(%s, 'Architecture Board', 'ACTA-2026-11')",
        (obj[0],),
    )
    assert conn.execute(
        "SELECT estado, aprobado_por, acta_ref, aprobado_en IS NOT NULL FROM core.objetivos_recuperacion WHERE id = %s",
        (obj[0],),
    ).fetchone() == ("aprobado", "Architecture Board", "ACTA-2026-11", True)

    # unos objetivos nuevos aprobados sustituyen a los anteriores: nunca dos aprobados a la vez
    nuevo = conn.execute(
        "INSERT INTO core.objetivos_recuperacion (rpo_minutos, rto_minutos, propuesto_por) VALUES (15, 120, 'prueba') RETURNING id"
    ).fetchone()
    assert nuevo is not None
    conn.execute(
        "SELECT core.aprobar_objetivos_recuperacion(%s, 'Architecture Board', 'ACTA-2026-12')",
        (nuevo[0],),
    )
    estados: dict[int, str] = dict(
        conn.execute(
            "SELECT id, estado FROM core.objetivos_recuperacion WHERE id IN (%s, %s)",
            (obj[0], nuevo[0]),
        ).fetchall()
    )
    assert estados == {obj[0]: "sustituido", nuevo[0]: "aprobado"}
    assert conn.execute(
        "SELECT count(*) FROM core.objetivos_recuperacion WHERE estado = 'aprobado'"
    ).fetchone() == (1,)


def test_no_se_puede_aprobar_a_mano_sin_los_datos_de_la_aprobacion(
    conn: psycopg.Connection,
) -> None:
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO core.objetivos_recuperacion (rpo_minutos, rto_minutos, estado, propuesto_por) VALUES (5, 60, 'aprobado', 'x')"
        )


def test_dos_aprobados_a_la_vez_lo_impide_el_indice(conn: psycopg.Connection) -> None:
    conn.execute(
        "UPDATE core.objetivos_recuperacion SET estado = 'sustituido' WHERE estado = 'aprobado'"
    )
    ins = (
        "INSERT INTO core.objetivos_recuperacion (rpo_minutos, rto_minutos, estado, propuesto_por, aprobado_por, aprobado_en, acta_ref) "
        "VALUES (5, 60, 'aprobado', 'x', 'b', now(), 'a')"
    )
    conn.execute(ins)
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        conn.execute(ins)


@pytest.mark.parametrize(("rpo", "rto"), [(0, 60), (1441, 60), (5, 0), (5, 10081)])
def test_los_objetivos_fuera_de_rango_se_rechazan(
    conn: psycopg.Connection, rpo: int, rto: int
) -> None:
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO core.objetivos_recuperacion (rpo_minutos, rto_minutos, propuesto_por) VALUES (%s, %s, 'x')",
            (rpo, rto),
        )


def test_la_aplicacion_no_ve_ni_aprueba_objetivos(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE kallicode_app")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT count(*) FROM core.objetivos_recuperacion")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT core.aprobar_objetivos_recuperacion(1, 'a', 'b')")


# ---------------------------------------------------------------------------------------------------------
# Estado del archivado
# ---------------------------------------------------------------------------------------------------------
def test_el_estado_del_archivado_se_puede_consultar(conn: psycopg.Connection) -> None:
    fila = conn.execute(
        "SELECT archivado_activado, wal_level, archivado_al_dia, fallos FROM core.estado_archivado_wal()"
    ).fetchone()
    assert fila is not None
    activado, nivel, al_dia, fallos = fila
    assert nivel in ("replica", "logical")  # PITR exige al menos replica
    # sin archivado activado nunca se declara «al día»; con él, sin fallos recientes sí
    assert al_dia is (activado and al_dia)
    assert fallos >= 0


# ---------------------------------------------------------------------------------------------------------
# archive_command
# ---------------------------------------------------------------------------------------------------------
sh = shutil.which("sh")
pytestmark_sh = pytest.mark.skipif(sh is None, reason="no hay sh")


def _archivar(origen: Path, nombre: str, destino: Path) -> subprocess.CompletedProcess[str]:
    assert sh is not None
    return subprocess.run(
        [sh, SCRIPT.as_posix(), origen.as_posix(), nombre],
        capture_output=True,
        text=True,
        env={**os.environ, "KC_WAL_ARCHIVO": destino.as_posix()},
        check=False,
        timeout=60,
    )


@pytestmark_sh
def test_archivar_copia_el_segmento_y_es_idempotente(tmp_path: Path) -> None:
    wal = tmp_path / "wal_origen"
    wal.write_bytes(b"segmento-1" * 100)
    destino = tmp_path / "archivo"
    destino.mkdir()
    assert _archivar(wal, "000000010000000000000001", destino).returncode == 0
    assert (destino / "000000010000000000000001").read_bytes() == wal.read_bytes()
    assert (
        _archivar(wal, "000000010000000000000001", destino).returncode == 0
    )  # reintento tras un fallo: no falla
    assert [p.name for p in destino.iterdir()] == ["000000010000000000000001"]  # sin temporales


@pytestmark_sh
def test_archivar_nunca_sobrescribe_un_segmento_distinto(tmp_path: Path) -> None:
    destino = tmp_path / "archivo"
    destino.mkdir()
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"uno")
    b.write_bytes(b"otro")
    assert _archivar(a, "000000010000000000000002", destino).returncode == 0
    r = _archivar(b, "000000010000000000000002", destino)
    assert (
        r.returncode != 0 and "no se sobrescribe" in r.stderr
    )  # PostgreSQL lo reintentará y lo verá fallar
    assert (destino / "000000010000000000000002").read_bytes() == b"uno"


@pytestmark_sh
def test_archivar_falla_si_falta_el_destino_o_el_origen(tmp_path: Path) -> None:
    wal = tmp_path / "wal"
    wal.write_bytes(b"x")
    assert _archivar(wal, "000000010000000000000003", tmp_path / "no_existe").returncode != 0
    assert _archivar(tmp_path / "no_hay", "000000010000000000000003", tmp_path).returncode != 0


# ---------------------------------------------------------------------------------------------------------
# acta
# ---------------------------------------------------------------------------------------------------------
def _acta(cumple: bool, rpo: float = 12.3, rto: float = 25.0) -> dict[str, object]:
    verif = {
        "tenants_con_cadena": 3,
        "cadenas_rotas": 0,
        "chequeos": 26,
        "hallazgos": 0,
        "segundos": 1.2,
        "ok": True,
    }
    return {
        "ejecutado_en": "2026-11-15T10:00:00+00:00",
        "version_postgres": "16.4",
        "cumple": cumple,
        "copia_base": {"tamano_base_datos_mb": 12.0, "tamano_mb": 40, "segundos": 2.0},
        "archivado": {"segmentos_en_archivo": 5, "tamano_archivo_mb": 80, "fallos": 0},
        "desastre": {
            "eventos_confirmados_en_la_carga": 300,
            "ultimo_evento_confirmado": "2026-11-15T10:01:00+00:00",
        },
        "objetivos_medidos": {
            "rpo_objetivo_s": 300,
            "rpo_medido_s": rpo,
            "rto_objetivo_s": 3600,
            "rto_medido_a_s": rto,
            "rto_medido_b_s": rto,
        },
        "restauracion_a_punto_T": {
            "objetivo_tiempo": "2026-11-15 10:00:30+00",
            "copia_base_segundos": 1.0,
            "recuperacion_segundos": rto - 1,
            "segmentos_wal_reproducidos": 2,
            "eventos_esperados_hasta_T": 150,
            "eventos_restaurados": 150,
            "eventos_posteriores_a_T": 0,
            "claves_idempotencia_esperadas": 100,
            "claves_idempotencia_restauradas": 100,
            "punto_correcto": True,
            "verificacion": verif,
            "rto_segundos": rto,
        },
        "restauracion_hasta_el_final_del_wal": {
            "copia_base_segundos": 1.0,
            "recuperacion_segundos": rto - 1,
            "segmentos_wal_reproducidos": 3,
            "ultimo_evento_restaurado": "2026-11-15T10:00:48+00:00",
            "eventos_confirmados_perdidos": 60,
            "perdida_efectiva_segundos": rpo,
            "verificacion": verif,
            "rto_segundos": rto,
        },
    }


def test_el_acta_resume_tiempos_y_deja_la_aprobacion_pendiente() -> None:
    md = ensayo.markdown(_acta(True))
    assert "CUMPLE" in md and "NO CUMPLE" not in md
    assert "12.3 s" in md and "60 eventos confirmados perdidos" in md
    assert "pendiente" in md and "Architecture Board" in md  # nunca se da por aprobado
    assert "SIGKILL" in md  # los límites de la medición se dicen en el propio acta


def test_el_acta_marca_el_incumplimiento() -> None:
    md = ensayo.markdown(_acta(False, rpo=400.0, rto=4000.0))
    assert "NO CUMPLE" in md
    assert md.count("| NO |") >= 3  # RPO y los dos RTO fuera de objetivo


def test_la_herramienta_define_los_objetivos_del_ticket() -> None:
    assert ensayo.RPO_OBJETIVO_S == 300 and ensayo.RTO_OBJETIVO_S == 3600
    assert (
        ensayo.ARCHIVE_TIMEOUT_S * 5 <= ensayo.RPO_OBJETIVO_S
    )  # el cierre forzado de segmento cabe holgado en el RPO


def test_la_instancia_restaurada_arranca_sin_cron_ni_particionado() -> None:
    """Una copia sin verificar no debe disparar purgas ni mantenimiento programado."""
    restaurada = " ".join(ensayo._args_postgres(False))
    assert "pg_cron" not in restaurada and "pg_partman_bgw" not in restaurada
    origen = " ".join(ensayo._args_postgres(True))
    assert (
        "archive_mode=on" in origen and "archive_timeout" in origen and "archivar_wal.sh" in origen
    )


# ---------------------------------------------------------------------------------------------------------
# El ensayo completo (Docker)
# ---------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("KC_PITR_ENSAYO") != "1", reason="ensayo con Docker: exportar KC_PITR_ENSAYO=1"
)
def test_el_ensayo_completo_restaura_verifica_y_mide(tmp_path: Path) -> None:
    acta = ensayo.ejecutar(duracion=30, eventos_iniciales=900)
    assert acta["restauracion_a_punto_T"]["punto_correcto"], acta["restauracion_a_punto_T"]
    assert acta["restauracion_a_punto_T"]["verificacion"]["ok"]
    assert acta["restauracion_hasta_el_final_del_wal"]["verificacion"]["ok"]
    assert acta["cumple"], acta["objetivos_medidos"]
