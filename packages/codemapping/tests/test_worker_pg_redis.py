"""Worker de ingesta contra Redis y PostgreSQL reales (KC-CM-10-1).

Requiere `make up && make db` (migración 054) y Redis (`CM_TEST_REDIS_URL`, por defecto
redis://localhost:6379/0; con contraseña, `CM_TEST_REDIS_PASSWORD`). Sin ellos se omite.

Cada test usa streams propios (el sufijo se inyecta en las constantes del módulo): no toca las
colas reales. El pipeline se sustituye por un ejecutor que escribe un snapshot, un fichero, un
símbolo y una arista, como lo haría `full_build`, para comprobar que reinyectar el mismo evento no
duplica nada.

Criterios:
- Reinyectar el mismo evento no duplica nodos, aristas ni snapshots.
- Un fallo persistente tras los reintentos va a la DLQ con traza estructurada y trace_id, sin
  bloquear al grupo de consumidores.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import worker
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.worker import (
    AlmacenIdempotencia,
    ConfigWorker,
    EventoIngesta,
    Procesador,
)

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)


def _dsn_rol(rol: str) -> str:
    return ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + f"options=-c%20role%3D{rol}"


APP_DSN = _dsn_rol("kallicode_app")
WORKER_DSN = _dsn_rol("kallicode_worker")
REDIS_URL = os.getenv("CM_TEST_REDIS_URL", "redis://localhost:6379/0")
REDIS_PASSWORD = os.getenv("CM_TEST_REDIS_PASSWORD") or None

TENANT_A = "org_test_wk_a"
TENANT_B = "org_test_wk_b"
SHA = "c" * 40


def _redis() -> Any:
    import redis

    return redis.Redis.from_url(REDIS_URL, password=REDIS_PASSWORD, decode_responses=True)


def _entorno_listo() -> bool:
    try:
        _redis().ping()
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                "SELECT has_table_privilege('kallicode_worker', 'core.idempotency_keys', 'INSERT')"
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _entorno_listo(), reason="Redis o Postgres no disponibles, o 054 sin aplicar"
)


# ---------------------------------------------------------------------------
# Entorno
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in (TENANT_A, TENANT_B):
        for tabla in (
            "core.idempotency_keys",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


@pytest.fixture
def base() -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        for t in (TENANT_A, TENANT_B):
            conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
        yield
        _limpiar(conn)


@pytest.fixture
def colas(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Streams, grupo y DLQ propios del test; se borran al terminar."""
    sufijo = uuid.uuid4().hex[:8]
    nombres = {
        "STREAM": f"kc:test:cm_ingesta_{sufijo}",
        "GRUPO": f"kc-test-cm-{sufijo}",
        "STREAM_REINTENTOS": f"kc:test:cm_ingesta_{sufijo}:retry",
        "DLQ": f"kc:test:cm_dlq_{sufijo}",
    }
    for clave, valor in nombres.items():
        monkeypatch.setattr(worker, clave, valor)
    r = _redis()
    yield r
    r.delete(
        *nombres.values(),
        f"kc:metrics:dlq:{nombres['STREAM']}",
    )


def _fabrica(dsn: str) -> Callable[[str], GraphLoader]:
    return lambda tenant: GraphLoader(dsn=dsn, tenant_id=tenant)


def _almacen(dsn: str = APP_DSN, lease: int = 3600) -> AlmacenIdempotencia:
    return AlmacenIdempotencia(_fabrica(dsn), lease)  # type: ignore[arg-type]


def _evento(**kw: Any) -> EventoIngesta:
    campos = {
        "tenant_id": TENANT_A,
        "repo_id": "repo_1",
        "repo_url": "https://git.local/acme/app.git",
        "commit_sha": SHA,
        **kw,
    }
    return EventoIngesta.model_validate(campos)


def _campos(evento: EventoIngesta | None = None, **kw: Any) -> dict[str, str]:
    e = evento or _evento(**kw)
    return {k: str(v) for k, v in e.model_dump(mode="json").items() if v is not None}


def _contar(tenant: str = TENANT_A) -> dict[str, int]:
    with psycopg.connect(ADMIN_DSN) as conn:
        return {
            t: int(
                conn.execute(
                    f"SELECT count(*) FROM cm.{t} WHERE tenant_id = %s", (tenant,)
                ).fetchone()[0]  # type: ignore[index]
            )
            for t in ("repo_snapshots", "files", "symbols", "edges")
        }


def _clave(evento: EventoIngesta) -> tuple[int, dict[str, Any]] | None:
    with psycopg.connect(ADMIN_DSN) as conn:
        fila = conn.execute(
            "SELECT status_code, respuesta FROM core.idempotency_keys "
            "WHERE tenant_id = %s AND endpoint = %s AND clave = %s",
            (evento.tenant_id, worker.ENDPOINT_IDEMPOTENCIA, evento.clave),
        ).fetchone()
    return (int(fila[0]), fila[1]) if fila else None


class Pipeline:
    """Sustituto de full_build: persiste un snapshot completo con un fichero, un símbolo y una arista."""

    def __init__(self, fallos: int = 0, pausa: float = 0.0) -> None:
        self.llamadas = 0
        self.fallos = fallos
        self.pausa = pausa
        self._lock = threading.Lock()

    def __call__(self, evento: EventoIngesta) -> dict[str, Any]:
        with self._lock:
            self.llamadas += 1
            n = self.llamadas
        if n <= self.fallos:
            raise RuntimeError(f"TEI no responde (intento {n})")
        time.sleep(self.pausa)
        t, sha = evento.tenant_id, evento.commit_sha
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            snap = f"snap_{uuid.uuid4().hex[:10]}"
            conn.execute(
                "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado, finalizado_en)"
                " VALUES (%s, %s, %s, 'main', 'completo', now())",
                (snap, t, sha),
            )
            conn.execute(
                "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id)"
                " VALUES (%s, %s, 'src/a.py', 'python', 'x', %s)",
                (f"file_{snap}", t, snap),
            )
            conn.execute(
                "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line,"
                " signature) VALUES (%s, %s, %s, 'function', 'f', 1, 2, 'def f()')",
                (f"sym_{snap}", t, f"file_{snap}"),
            )
            conn.execute(
                "INSERT INTO cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id)"
                " VALUES (%s, 'DEFINES', 'file', %s, 'symbol', %s)",
                (t, f"file_{snap}", f"sym_{snap}"),
            )
        return {"snapshot_id": snap}


def _config(**kw: Any) -> ConfigWorker:
    base = {"max_intentos": 3, "backoff_base_segundos": 0.05, "backoff_max_segundos": 0.2}
    return ConfigWorker(bloqueo_ms=20, reclamar_inactivos_ms=1_000, **{**base, **kw})


def _procesador(
    redis: Any, ejecutor: Callable[[EventoIngesta], dict[str, Any]], **kw: Any
) -> Procesador:
    cfg = _config(**kw)
    return Procesador(
        redis, _almacen(lease=cfg.lease_segundos), ejecutor, cfg, f"c-{uuid.uuid4().hex[:6]}"
    )


class Corrida:
    """Ejecuta el bucle del worker en un hilo hasta que `condicion` se cumple o vence el plazo."""

    def __init__(self, procesador: Procesador) -> None:
        self.parar = threading.Event()
        self.hilo = threading.Thread(
            target=worker.ciclo, args=(procesador, self.parar, 0.2), daemon=True
        )

    def __enter__(self) -> Corrida:
        self.hilo.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.parar.set()
        self.hilo.join(timeout=10)

    @staticmethod
    def esperar(condicion: Callable[[], bool], plazo: float = 15.0) -> None:
        fin = time.monotonic() + plazo
        while time.monotonic() < fin:
            if condicion():
                return
            time.sleep(0.05)
        raise AssertionError("la condición no se cumplió a tiempo")


def _pendientes(r: Any) -> int:
    return int(r.xpending(worker.STREAM, worker.GRUPO)["pending"])


# ---------------------------------------------------------------------------
# Idempotencia
# ---------------------------------------------------------------------------
def test_reinyectar_el_mismo_evento_no_duplica_nodos_aristas_ni_snapshots(
    base: None, colas: Any
) -> None:
    pipeline = Pipeline()
    p = _procesador(colas, pipeline)
    evento = _evento()
    with Corrida(p) as corrida:
        colas.xadd(worker.STREAM, _campos(evento))
        corrida.esperar(lambda: _clave(evento) is not None and _clave(evento)[0] == 200)  # type: ignore[index]
        antes = _contar()
        for _ in range(5):  # el mismo evento, cinco veces más
            colas.xadd(worker.STREAM, _campos(evento))
        corrida.esperar(lambda: colas.xlen(worker.STREAM) >= 6 and _pendientes(colas) == 0)
        time.sleep(0.3)
    assert pipeline.llamadas == 1
    assert antes == {"repo_snapshots": 1, "files": 1, "symbols": 1, "edges": 1}
    assert _contar() == antes  # nada nuevo
    assert _pendientes(colas) == 0


def test_mismo_commit_en_otro_tenant_si_se_procesa(base: None, colas: Any) -> None:
    pipeline = Pipeline()
    p = _procesador(colas, pipeline)
    with Corrida(p) as corrida:
        colas.xadd(worker.STREAM, _campos(_evento()))
        colas.xadd(worker.STREAM, _campos(_evento(tenant_id=TENANT_B)))
        corrida.esperar(lambda: pipeline.llamadas == 2 and _pendientes(colas) == 0)
    assert _contar(TENANT_A)["repo_snapshots"] == 1 and _contar(TENANT_B)["repo_snapshots"] == 1


def test_la_reserva_atomica_deja_pasar_a_uno_solo_entre_muchos_workers(base: None) -> None:
    evento = _evento()
    resultados: list[str] = []
    barrera = threading.Barrier(8)

    def intentar() -> None:
        alm = _almacen()
        barrera.wait()
        resultados.append(alm.reservar(evento, "tr", "w"))

    hilos = [threading.Thread(target=intentar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == ["en_curso"] * 7 + ["reservada"]
    assert _clave(evento)[0] == 0  # type: ignore[index]


def test_una_reserva_sin_latido_vence_y_la_toma_un_solo_worker(base: None) -> None:
    evento = _evento()
    assert _almacen().reservar(evento, "tr", "muerto") == "reservada"
    assert _almacen(lease=3600).reservar(evento, "tr", "w") == "en_curso"  # viva
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:  # el latido se detuvo hace 2 h
        conn.execute(
            "UPDATE core.idempotency_keys SET creado_en = now() - interval '2 hours' "
            "WHERE tenant_id = %s AND endpoint = %s",
            (evento.tenant_id, worker.ENDPOINT_IDEMPOTENCIA),
        )
    resultados: list[str] = []
    barrera = threading.Barrier(6)

    def intentar() -> None:
        barrera.wait()
        resultados.append(_almacen(lease=3600).reservar(evento, "tr", "w"))

    hilos = [threading.Thread(target=intentar) for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(resultados) == ["en_curso"] * 5 + ["reservada"]


def test_el_latido_mantiene_viva_la_reserva_de_un_pipeline_largo(base: None) -> None:
    evento = _evento()
    alm = _almacen(lease=30)  # el latido corre cada 10 s: se fuerza uno a mano
    assert alm.reservar(evento, "tr", "w") == "reservada"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE core.idempotency_keys SET creado_en = now() - interval '25 seconds'"
            " WHERE tenant_id = %s",
            (evento.tenant_id,),
        )
    alm.renovar(evento)
    assert alm.reservar(evento, "tr", "otro") == "en_curso"
    assert alm.renovar is not None


def test_el_rol_worker_puede_reservar_completar_y_liberar(base: None) -> None:
    alm = _almacen(WORKER_DSN)
    evento = _evento()
    assert alm.reservar(evento, "tr", "w") == "reservada"
    alm.completar(evento, {"x": 1})
    assert _clave(evento) == (200, {"estado": "completado", "x": 1})
    otro = _evento(repo_id="repo_2")
    assert alm.reservar(otro, "tr", "w") == "reservada"
    alm.liberar(otro)
    assert _clave(otro) is None
    alm.liberar(evento)  # una reserva completada no se borra
    assert _clave(evento) is not None


def test_la_clave_purgada_por_ttl_no_reprocesa_un_commit_con_snapshot_completo(
    base: None, colas: Any
) -> None:
    pipeline = Pipeline()
    p = _procesador(colas, pipeline)
    evento = _evento()
    with Corrida(p) as corrida:
        colas.xadd(worker.STREAM, _campos(evento))
        corrida.esperar(lambda: _clave(evento) is not None and _clave(evento)[0] == 200)  # type: ignore[index]
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:  # KC-DB-10-3 purgó la clave
            conn.execute("DELETE FROM core.idempotency_keys WHERE tenant_id = %s", (TENANT_A,))
        colas.xadd(worker.STREAM, _campos(evento))
        corrida.esperar(lambda: (_clave(evento) or (0,))[0] == 200)
    assert pipeline.llamadas == 1 and _contar()["repo_snapshots"] == 1
    assert _clave(evento)[1]["origen"] == "snapshot_existente"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Fallos, reintentos y DLQ
# ---------------------------------------------------------------------------
def test_un_fallo_transitorio_se_reintenta_con_backoff_y_termina_bien(
    base: None, colas: Any
) -> None:
    pipeline = Pipeline(fallos=2)
    p = _procesador(colas, pipeline)
    evento = _evento()
    with Corrida(p) as corrida:
        colas.xadd(worker.STREAM, _campos(evento))
        corrida.esperar(lambda: (_clave(evento) or (0,))[0] == 200)
    assert pipeline.llamadas == 3  # 2 fallos + 1 éxito
    assert _contar()["repo_snapshots"] == 1
    assert colas.xlen(worker.DLQ) == 0 and colas.zcard(worker.STREAM_REINTENTOS) == 0


def test_un_fallo_persistente_va_a_la_dlq_con_traza_y_no_bloquea_al_grupo(
    base: None, colas: Any
) -> None:
    pipeline = Pipeline(fallos=10_000)
    p = _procesador(colas, pipeline, max_intentos=3)
    veneno = _evento(trace_id="tr_veneno")
    sano = _evento(repo_id="repo_sano", commit_sha="d" * 40)

    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        if evento.repo_id == "repo_sano":
            return Pipeline()(evento)
        return pipeline(evento)

    p.ejecutor = ejecutor
    with Corrida(p) as corrida:
        colas.xadd(worker.STREAM, _campos(veneno))
        colas.xadd(worker.STREAM, _campos(sano))
        corrida.esperar(lambda: colas.xlen(worker.DLQ) == 1 and (_clave(sano) or (0,))[0] == 200)
        corrida.esperar(lambda: _pendientes(colas) == 0)

    [(_, entrada)] = colas.xrange(worker.DLQ)
    assert entrada["trace_id"] == "tr_veneno" and entrada["attempts"] == "3"
    assert entrada["original_stream"] == worker.STREAM and entrada["tenant_id"] == TENANT_A
    estructurado = json.loads(entrada["error_estructurado"])
    assert estructurado["tipo"] == "RuntimeError" and "TEI no responde" in estructurado["mensaje"]
    assert estructurado["frames"]
    assert pipeline.llamadas == 3  # max_intentos
    assert _clave(veneno) is None  # la reserva del envenenado quedó libre
    assert _contar()["repo_snapshots"] == 1  # el sano sí se procesó, el envenenado no dejó nada
    assert int(colas.get(f"kc:metrics:dlq:{worker.STREAM}")) == 1


def test_un_evento_invalido_va_a_la_dlq_sin_tocar_la_base(base: None, colas: Any) -> None:
    pipeline = Pipeline()
    p = _procesador(colas, pipeline)
    with Corrida(p) as corrida:
        colas.xadd(worker.STREAM, {"tenant_id": TENANT_A, "commit_sha": "zzz"})
        corrida.esperar(lambda: colas.xlen(worker.DLQ) == 1 and _pendientes(colas) == 0)
    [(_, entrada)] = colas.xrange(worker.DLQ)
    assert "evento de ingesta inválido" in entrada["error"] and entrada["trace_id"]
    assert pipeline.llamadas == 0 and _contar()["repo_snapshots"] == 0


# ---------------------------------------------------------------------------
# At-least-once: un worker que cae a mitad
# ---------------------------------------------------------------------------
def test_un_mensaje_sin_ack_lo_recupera_otro_consumidor_y_se_procesa_una_vez(
    base: None, colas: Any
) -> None:
    pipeline = Pipeline()
    evento = _evento()
    worker.crear_grupo(colas)
    colas.xadd(worker.STREAM, _campos(evento))
    # El consumidor "muerto" lee el mensaje y cae antes de confirmarlo.
    leido = colas.xreadgroup(worker.GRUPO, "muerto", {worker.STREAM: ">"}, count=1)
    assert leido and _pendientes(colas) == 1
    time.sleep(1.1)  # supera reclamar_inactivos_ms
    p = _procesador(colas, pipeline)
    with Corrida(p) as corrida:
        corrida.esperar(lambda: (_clave(evento) or (0,))[0] == 200 and _pendientes(colas) == 0)
    assert pipeline.llamadas == 1 and _contar()["repo_snapshots"] == 1


def test_el_reintento_vencido_vuelve_al_stream_una_sola_vez_con_dos_workers(
    base: None, colas: Any
) -> None:
    p1 = _procesador(colas, Pipeline())
    p2 = _procesador(colas, Pipeline())
    colas.zadd(
        worker.STREAM_REINTENTOS,
        {
            json.dumps(
                {**_campos(), "intentos": "1", "retry_id": "r1"}, sort_keys=True
            ): time.time() - 1
        },
    )
    promovidos: list[int] = []
    barrera = threading.Barrier(2)

    def promover(p: Procesador) -> None:
        barrera.wait()
        promovidos.append(p.promover_reintentos())

    hilos = [threading.Thread(target=promover, args=(p,)) for p in (p1, p2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(promovidos) == [0, 1]
    assert colas.xlen(worker.STREAM) == 1 and colas.zcard(worker.STREAM_REINTENTOS) == 0
