"""Redacción de los logs y auditor de logs de ingesta (KC-CM-12-6).

Criterio: un log de ingesta no contiene credenciales, rutas absolutas del cliente ni fragmentos de
código. Se prueba en tres capas: las reglas (redaccion), el formateador que las aplica a cada línea
(logging_setup) y el auditor que revisa un log ya escrito (revision_logs).
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from kallicode_codemapping import revision_logs
from kallicode_codemapping.config import build_settings
from kallicode_codemapping.ingest_repo import GitSyncError, sync_repo
from kallicode_codemapping.logging_setup import JsonFormatter, get_logger, resumen_excepcion
from kallicode_codemapping.redaccion import (
    hallazgos,
    mensaje_de_error,
    parece_codigo,
    redactar,
    redactar_valor,
)
from kallicode_codemapping.worker import error_estructurado

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"  # formato de token de GitHub, falso
CLAVE_BD = "Cl4ve-Del-Cliente"

SENSIBLES = [
    (f"fatal: unable to access 'https://x-access-token:{TOKEN}@github.com/o/r.git/'", "credencial"),
    (f"postgresql://lector:{CLAVE_BD}@db.cliente.local:5432/erp", "credencial"),
    (f"remote: invalid token {TOKEN}", "credencial"),
    ("sk-" + "abcdefghijklmnopqrstuvwxyz012345", "credencial"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJhLWZhbHNh", "credencial"),
    ("host=db user=app password=hunter2 dbname=erp", "credencial"),
    ("DefaultEndpointsProtocol=https;AccountName=a;AccountKey=bG9yZW0=;", "credencial"),
    ("https://cuenta.blob.core.windows.net/c/x?sv=2024&sig=abcDEF123%3D", "credencial"),
    ("Authorization: Bearer 0a1b2c3d4e5f6a7b8c9d", "credencial"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "credencial"),
    ("no se pudo abrir /srv/kallicode/workdir/org_1/src/pagos.py", "ruta_absoluta"),
    ("fatal: destination path '/tmp/kc_ingesta_x/workdir/org_1' already exists", "ruta_absoluta"),
    (r"FileNotFoundError: C:\Users\cliente\repo\main.py", "ruta_absoluta"),
    (r"\\servidor\recurso\repo", "ruta_absoluta"),
    ("file:///home/cliente/repo", "ruta_absoluta"),
    ("def cobrar(pedido):\n    total = pedido.total\n    return total\n", "codigo"),
    ("public class Pagos { private int total() { return 0; } }", "codigo"),
    ("const total = items.reduce((a, b) => { return a + b; });", "codigo"),
    ("1 validation error\n  [type=missing, input_value={'src': 'x'}, input_type=dict]", "codigo"),
]

INOCUOS = [
    "reconstrucción determinista completa @ 3f2a9c1b7e",
    "fase 3/7: extraccion de esquema del cliente",
    "http://localhost:8080/v1/embed",
    "redis://localhost:6379/0",
    "cm.files: 120 filas",
    "SELECT estado FROM cm.refrescar_reachability()",
    "basic authentication failed for user",
    "tokens_in=300 total_tokens_estimados=20",
    "src/pagos/cobro.py:12",
    "git@github.com:org/repo.git",
    "abortando build por presupuesto insuficiente",
]


@pytest.mark.parametrize(("texto", "categoria"), SENSIBLES)
def test_lo_sensible_se_redacta_y_el_auditor_lo_detecta(texto: str, categoria: str) -> None:
    assert categoria in hallazgos(texto)
    limpio = redactar(texto)
    assert hallazgos(limpio) == []
    assert TOKEN not in limpio and CLAVE_BD not in limpio and "/srv/" not in limpio
    assert redactar(limpio) == limpio  # idempotente: el auditor no ve lo ya redactado


@pytest.mark.parametrize("texto", INOCUOS)
def test_lo_inocuo_no_se_toca(texto: str) -> None:
    assert hallazgos(texto) == []
    assert redactar(texto) == texto


def test_ruta_absoluta_conserva_el_nombre_del_archivo() -> None:
    assert redactar("leyendo /srv/workdir/org/src/pagos.py") == "leyendo <ruta>/pagos.py"


def test_parece_codigo_exige_mas_de_un_indicio() -> None:
    assert not parece_codigo("corto")
    assert not parece_codigo("la llamada terminó con return code 1;")  # un solo indicio
    assert parece_codigo("x = 1;\ny = 2;\nz = 3;\n")  # un indicio y tres líneas


def test_textos_largos_se_recortan() -> None:
    texto = "a" * 5000
    salida = redactar(texto, 100)
    assert salida.startswith("a" * 100) and "4900 caracteres omitidos" in salida


def test_redactar_valor_recorre_estructuras_y_oculta_claves_secretas() -> None:
    valor = {
        "password": "hunter2",
        "token": "abc",
        "vacio": "",
        "dsn": None,
        "rutas": ["/srv/a/b.py", ("/srv/c/d.py",)],
        "n": 3,
        "anidado": {"api_key": "k", "detalle": f"https://u:{TOKEN}@h/r"},
    }
    salida = redactar_valor(valor)
    assert salida["password"] == "***" and salida["token"] == "***"
    assert salida["dsn"] is None and salida["n"] == 3
    assert salida["rutas"] == ["<ruta>/b.py", ["<ruta>/d.py"]]
    assert salida["anidado"] == {"api_key": "***", "detalle": "https://***@h/r"}


def test_mensaje_de_error_redacta_y_acota() -> None:
    exc = RuntimeError(f"fallo en https://u:{TOKEN}@h/r " + "x" * 2000)
    mensaje = mensaje_de_error(exc)
    assert TOKEN not in mensaje and len(mensaje) < 600


# --------------------------------------------------------------------------
# logging_setup.JsonFormatter
# --------------------------------------------------------------------------
def _formatear(
    msg: str, *args: Any, exc: BaseException | None = None, **extra: Any
) -> dict[str, Any]:
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    rec = logging.LogRecord("codemapping", logging.ERROR, __file__, 1, msg, args, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    salida: dict[str, Any] = json.loads(JsonFormatter().format(rec))
    return salida


def test_formatter_emite_los_extra_redactados() -> None:
    linea = _formatear(
        "clonando %s",
        f"https://x:{TOKEN}@github.com/o/r.git",
        tenant_id="org_1",
        detalle="no existe /srv/kallicode/workdir/org_1",
        max_connections=2,
        password="hunter2",
        objeto=Path("relativo"),
    )
    assert linea["message"] == "clonando https://***@github.com/o/r.git"
    assert linea["tenant_id"] == "org_1"
    assert linea["detalle"] == "no existe <ruta>/org_1"
    assert linea["max_connections"] == 2  # antes los extra se descartaban
    assert linea["password"] == "***"
    assert linea["objeto"] == "relativo"  # lo no serializable sale como texto
    assert TOKEN not in json.dumps(linea)


def test_formatter_resume_la_excepcion_sin_traza_ni_codigo() -> None:
    try:
        raise ValueError("def secreto(x):\n    return x;\n")
    except ValueError as exc:
        linea = _formatear("fallo", exc=exc)
        assert exc.__traceback__ is not None
        linea_raise = exc.__traceback__.tb_lineno
    assert linea["exc"]["tipo"] == "ValueError"
    assert linea["exc"]["mensaje"].startswith("<omitido: posible código")
    assert linea["exc"]["donde"] == f"test_redaccion_unit.py:{linea_raise}"
    assert "Traceback" not in json.dumps(linea)


def test_resumen_excepcion_sin_traceback() -> None:
    assert resumen_excepcion(KeyError("k")) == {"tipo": "KeyError", "mensaje": "'k'", "donde": None}


def test_formatter_mantiene_extra_fields() -> None:
    linea = _formatear("x", extra_fields={"files": 4, "ruta": "/home/u/repo/a.py"})
    assert linea["files"] == 4 and linea["ruta"] == "<ruta>/a.py"


def test_get_logger_de_modulo_escribe_por_la_raiz_redactado() -> None:
    raiz = logging.getLogger("codemapping")
    get_logger()
    handler = raiz.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    buffer = io.StringIO()
    anterior = handler.setStream(buffer)
    try:
        get_logger("codemapping.ingest_docs").warning(
            "documento fallido", extra={"motivo": f"token={TOKEN}"}
        )
    finally:
        handler.setStream(anterior)
    lineas = buffer.getvalue().splitlines()
    assert len(lineas) == 1  # una sola línea: el hijo no tiene handler propio
    assert json.loads(lineas[0])["motivo"] == "token=***"


# --------------------------------------------------------------------------
# Errores que salían crudos: DLQ del worker y stderr de git
# --------------------------------------------------------------------------
def test_error_estructurado_de_la_dlq_va_redactado() -> None:
    exc = RuntimeError(f"git clone https://u:{TOKEN}@h/r.git en /srv/workdir/org_1 falló")
    est = error_estructurado(exc)
    assert est["mensaje"] == "git clone https://***@h/r.git en <ruta>/org_1 falló"


def test_fallo_de_git_no_filtra_token_ni_ruta(tmp_path: Path) -> None:
    cfg = build_settings(
        tenant_id="org_git",
        repo_url=f"https://x-access-token:{TOKEN}@127.0.0.1:1/cliente/repo.git",
        workdir=str(tmp_path / "workdir"),
    )
    raiz = logging.getLogger("codemapping")
    handler = raiz.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    buffer = io.StringIO()
    anterior = handler.setStream(buffer)
    try:
        with pytest.raises(GitSyncError) as err:
            sync_repo(cfg)
    finally:
        handler.setStream(anterior)
    for texto in (str(err.value), buffer.getvalue()):
        assert TOKEN not in texto
        assert str(tmp_path) not in texto and tmp_path.as_posix() not in texto
    # la causa original conserva el stderr completo para clasificar la escalación en memoria
    assert err.value.__cause__ is not None
    informe = revision_logs.auditar(buffer.getvalue().splitlines(), [TOKEN, str(tmp_path)])
    assert informe.lineas >= 1 and informe.hallazgos == []


# --------------------------------------------------------------------------
# revision_logs: el auditor
# --------------------------------------------------------------------------
def test_auditor_localiza_cada_hallazgo_sin_repetir_el_valor() -> None:
    lineas = [
        json.dumps({"message": "ok", "tenant_id": "t"}),
        json.dumps({"message": "x", "detalle": f"https://u:{TOKEN}@h/r"}),
        json.dumps({"message": "x", "archivos": {"/srv/a/b.py": 1}}),
        "Traceback (most recent call last): File /usr/lib/python3/x.py",
        "",
        json.dumps({"message": "def f(x):\n    return x;\n"}),
        json.dumps({"message": "literal prohibido: org-secreto"}),
        json.dumps(["no", "es", "un", "objeto"]),
    ]
    informe = revision_logs.auditar(lineas, ["org-secreto", ""])
    assert informe.lineas == 7 and informe.lineas_no_json == 2
    encontrados = {(h.linea, h.campo, h.categoria) for h in informe.hallazgos}
    assert (2, "detalle", "credencial") in encontrados
    assert (3, "archivos.<clave#0>", "ruta_absoluta") in encontrados
    assert (4, "<linea>", "ruta_absoluta") in encontrados
    assert (6, "message", "codigo") in encontrados
    assert (7, "message", "prohibido") in encontrados
    salida = json.dumps(informe.a_dict())
    assert TOKEN not in salida and "/srv/a" not in salida
    assert informe.a_dict()["por_categoria"]["credencial"] == 1


def test_auditor_cli_falla_con_hallazgos_o_pocas_lineas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    limpio = tmp_path / "limpio.jsonl"
    limpio.write_text("\n".join(json.dumps({"message": f"l{i}"}) for i in range(5)), "utf-8")
    assert revision_logs.main([str(limpio), "--minimo-lineas", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert revision_logs.main([str(limpio), "--minimo-lineas", "1000"]) == 1
    capsys.readouterr()

    sucio = tmp_path / "sucio.jsonl"
    sucio.write_text(json.dumps({"message": "token=abc123"}) + "\n", "utf-8")
    assert revision_logs.main([str(sucio)]) == 1
    salida = json.loads(capsys.readouterr().out)
    assert salida["hallazgos"] == 1 and "abc123" not in json.dumps(salida)


def test_auditor_cli_lee_de_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"message": "ok"}) + "\n"))
    assert revision_logs.main(["-", "--prohibido", "x-no-esta"]) == 0
    assert json.loads(capsys.readouterr().out)["lineas"] == 1
