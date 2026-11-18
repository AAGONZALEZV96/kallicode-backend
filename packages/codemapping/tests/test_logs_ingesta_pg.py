"""Los logs de una ingesta real no contienen credenciales, rutas absolutas ni código (KC-CM-12-6).

Ejecuta el pipeline real (build_graph.full_build y refresh.run_refresh, con los mismos dobles que el
benchmark de ingesta: LLM, TEI y base del cliente) sobre el repositorio sintético, con secretos
sembrados donde una fuga los arrastraría: la clave del LLM, la contraseña de la base del cliente, la
ruta absoluta del workdir y un archivo del cliente con una credencial en su código. Captura TODO lo
que se registra en el proceso (raíz de logging, no solo «codemapping») y lo pasa por el auditor
(revision_logs) con esos literales como prohibidos.

Para generar la evidencia del acta (≥ 1.000 líneas) se repiten ciclos de refresco y se guarda el log:

    KC_CM_LOGS_CICLOS=60 KC_CM_LOGS_SALIDA=ingesta.jsonl \
        uv run pytest packages/codemapping/tests/test_logs_ingesta_pg.py -q
    uv run python -m kallicode_codemapping.revision_logs ingesta.jsonl --minimo-lineas 1000
"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import build_graph, refresh, repo_sintetico, revision_logs
from kallicode_codemapping.config import Settings
from kallicode_codemapping.graph_loader import normalizar_dsn
from kallicode_codemapping.logging_setup import JsonFormatter

TENANT = "kc_cm_12_6_logs"
DIVISOR = 1000  # 8 archivos, 176 símbolos: el contenido de los logs no depende de la escala
CICLOS = int(os.getenv("KC_CM_LOGS_CICLOS", "1"))
CLAVE_LLM = "sk-" + "kccm126" + "Q" * 30  # pragma: allowlist secret - sembrada, falsa
CLAVE_BD_CLIENTE = "Cl4ve-BD-del-Cliente-126"  # pragma: allowlist secret - sembrada, falsa
TOKEN_EN_CODIGO = "ghp_" + "kccm126" + "Z" * 30  # pragma: allowlist secret - sembrado, falso
CODIGO_CLIENTE = f'''
API_TOKEN = "{TOKEN_EN_CODIGO}"


def cobrar_pedido_kccm126(pedido):
    total = sum(linea.importe for linea in pedido.lineas)
    return total
'''


def _admin() -> str | None:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    return normalizar_dsn(url) if url else None


def _db_disponible() -> bool:
    dsn = _admin()
    if not dsn:
        return False
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
        return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _db_disponible(), reason="KC_DATABASE_URL_MIGRATIONS no definida o Postgres no disponible"
)


def _cargar_bench() -> Any:
    """Dobles del benchmark de ingesta (KC-CM-11-1): mismo pipeline, mismos sustitutos."""
    ruta = Path(__file__).with_name("bench_ingesta_escala.py")
    spec = importlib.util.spec_from_file_location("bench_ingesta_escala_logs", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    modulo.TENANT = TENANT  # _limpiar() borra por este tenant
    return modulo


@pytest.fixture
def log_del_proceso() -> Iterator[io.StringIO]:
    """Todo lo que se registra en el proceso, formateado como en producción."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(JsonFormatter())
    raiz = logging.getLogger()
    nivel = raiz.level
    raiz.addHandler(handler)
    raiz.setLevel(logging.INFO)
    try:
        yield buffer
    finally:
        raiz.removeHandler(handler)
        raiz.setLevel(nivel)


def test_los_logs_de_una_ingesta_real_pasan_la_auditoria(log_del_proceso: io.StringIO) -> None:
    bench = _cargar_bench()
    admin = _admin()
    assert admin
    app = bench._dsn_app(admin)
    escala = repo_sintetico.Escala.reducida(DIVISOR)
    base = Path(tempfile.mkdtemp(prefix="kc_cm_12_6_"))
    with psycopg.connect(admin, autocommit=True) as conn:
        bench._limpiar(conn)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (TENANT, TENANT)
        )
    try:
        origen = base / "origen"
        repo_sintetico.crear_repositorio_git(origen, escala)
        (origen / "pagos_cliente.py").write_text(CODIGO_CLIENTE, encoding="utf-8")
        repo_sintetico._git(origen, "add", "-A")
        repo_sintetico._git(origen, "commit", "-q", "-m", "codigo del cliente")
        settings = Settings(
            tenant_id=TENANT,
            repo_url=str(origen),
            workdir=str(base / "workdir"),
            store_database_url=app,
            llm_api_key=CLAVE_LLM,
            database_url=f"postgresql://lector:{CLAVE_BD_CLIENTE}@db.cliente.example:5432/erp",  # type: ignore[arg-type]
        )
        indices = repo_sintetico.archivos_a_modificar(escala, escala.archivos)
        with bench._aplicar(bench._dobles(escala)):
            build_graph.full_build(settings)
            for _ in range(CICLOS):
                repo_sintetico.modificar_archivos(origen, escala, indices)
                refresh.run_refresh(settings)
    finally:
        shutil.rmtree(base, ignore_errors=True)
        with psycopg.connect(admin, autocommit=True) as conn:
            bench._limpiar(conn)

    texto = log_del_proceso.getvalue()
    if salida := os.getenv("KC_CM_LOGS_SALIDA"):
        Path(salida).write_text(texto, encoding="utf-8")
    prohibidos = [
        CLAVE_LLM,
        CLAVE_BD_CLIENTE,
        TOKEN_EN_CODIGO,
        "cobrar_pedido_kccm126",
        str(base),
        base.as_posix(),
        app.split("@")[0],  # usuario:contraseña del store
    ]
    informe = revision_logs.auditar(texto.splitlines(), prohibidos)
    assert informe.lineas >= 20, "la ingesta debería dejar rastro de sus fases"
    assert informe.lineas_no_json == 0
    assert informe.hallazgos == [], informe.a_dict()
