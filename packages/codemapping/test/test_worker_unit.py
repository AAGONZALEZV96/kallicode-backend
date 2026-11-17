"""Worker de ingesta (KC-CM-10-1) sin Redis ni base de datos: Redis y la idempotencia van en memoria.

La semántica real (reserva atómica en PostgreSQL, XREADGROUP/XAUTOCLAIM y el script Lua contra un
Redis verdadero) está en tests/test_worker_pg_redis.py.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from kallicode_codemapping import worker
from kallicode_codemapping.worker import (
    ConfigWorker,
    ErrorNoRecuperable,
    EventoIngesta,
    EventoInvalido,
    Procesador,
    backoff_segundos,
    error_estructurado,
)

SHA = "a" * 40
CAMPOS = {
    "tenant_id": "org_a",
    "repo_id": "repo_1",
    "repo_url": "https://github.com/acme/app.git",
    "commit_sha": SHA,
}


class RedisFalso:
    """Los comandos que usa el worker, en memoria."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.acks: list[str] = []
        self.contadores: dict[str, int] = {}
        self._n = 0

    def xadd(
        self,
        stream: str,
        campos: dict[str, str],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._n += 1
        mid = f"{self._n}-0"
        self.streams.setdefault(stream, []).append((mid, dict(campos)))
        return mid

    def xack(self, stream: str, grupo: str, mid: str) -> int:
        self.acks.append(mid)
        return 1

    def zadd(self, clave: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(clave, {}).update(mapping)
        return len(mapping)

    def incr(self, clave: str) -> int:
        self.contadores[clave] = self.contadores.get(clave, 0) + 1
        return self.contadores[clave]

    def eval(self, script: str, nkeys: int, zset: str, stream: str, hasta: str, limite: str) -> int:
        assert script == worker.LUA_PROMOVER
        vencidos = sorted(
            (m for m, score in self.zsets.get(zset, {}).items() if score <= float(hasta)),
        )[: int(limite)]
        for miembro in vencidos:
            del self.zsets[zset][miembro]
            self.xadd(stream, json.loads(miembro))
        return len(vencidos)


class AlmacenFalso:
    """Idempotencia en memoria con la misma interfaz que AlmacenIdempotencia."""

    def __init__(self) -> None:
        self.estado: dict[str, int] = {}
        self.llamadas: list[str] = []
        self.snapshots: dict[str, str] = {}
        self.fallar_completar = False

    def reservar(self, evento: EventoIngesta, trace_id: str, worker_id: str) -> str:
        self.llamadas.append("reservar")
        actual = self.estado.get(evento.clave)
        if actual is None:
            self.estado[evento.clave] = 0
            return "reservada"
        return "en_curso" if actual == 0 else "completada"

    def renovar(self, evento: EventoIngesta) -> None:
        self.llamadas.append("renovar")

    def completar(self, evento: EventoIngesta, resumen: dict[str, Any]) -> None:
        self.llamadas.append("completar")
        if self.fallar_completar:
            raise RuntimeError("base caída")
        self.estado[evento.clave] = 200

    def liberar(self, evento: EventoIngesta) -> None:
        self.llamadas.append("liberar")
        if self.estado.get(evento.clave) == 0:
            del self.estado[evento.clave]

    def snapshot_completo(self, evento: EventoIngesta) -> str | None:
        return self.snapshots.get(evento.commit_sha)

    lease_segundos = 3600


class Reloj:
    def __init__(self) -> None:
        self.ahora = 1_000.0

    def __call__(self) -> float:
        return self.ahora


def _procesador(
    ejecutor: Any, *, almacen: AlmacenFalso | None = None, max_intentos: int = 3
) -> tuple[Procesador, RedisFalso, AlmacenFalso, Reloj]:
    redis, reloj = RedisFalso(), Reloj()
    alm = almacen or AlmacenFalso()
    cfg = ConfigWorker(
        max_intentos=max_intentos, backoff_base_segundos=2.0, backoff_max_segundos=60
    )
    return Procesador(redis, alm, ejecutor, cfg, "test-1", reloj), redis, alm, reloj  # type: ignore[arg-type]


def _ok(llamadas: list[EventoIngesta]) -> Any:
    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        llamadas.append(evento)
        return {"modo": evento.modo}

    return ejecutor


def _falla(error: Exception) -> Any:
    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        raise error

    return ejecutor


# ---------------------------------------------------------------------------
# Evento
# ---------------------------------------------------------------------------
def test_evento_valido_normaliza_el_sha_y_define_la_clave() -> None:
    e = EventoIngesta.desde_campos({**CAMPOS, "commit_sha": "ABCDEF1"})
    assert e.commit_sha == "abcdef1" and e.clave == "repo_1:abcdef1"
    assert e.modo == "refresh" and e.intentos == 0 and e.trace_id is None


@pytest.mark.parametrize(
    "cambio",
    [
        {"commit_sha": "no-es-un-sha"},
        {"commit_sha": "abc"},
        {"tenant_id": ""},
        {"modo": "otro"},
        {"intentos": "-1"},
    ],
)
def test_evento_invalido_es_un_error_de_contrato(cambio: dict[str, str]) -> None:
    with pytest.raises(EventoInvalido, match="evento de ingesta inválido"):
        EventoIngesta.desde_campos({**CAMPOS, **cambio})


def test_evento_sin_campos_obligatorios_nombra_cuales_faltan() -> None:
    with pytest.raises(EventoInvalido) as exc:
        EventoIngesta.desde_campos({"tenant_id": "org_a"})
    assert "repo_id" in str(exc.value) and "commit_sha" in str(exc.value)


# ---------------------------------------------------------------------------
# Camino feliz e idempotencia
# ---------------------------------------------------------------------------
def test_un_evento_valido_ejecuta_una_vez_y_se_confirma() -> None:
    llamadas: list[EventoIngesta] = []
    p, redis, alm, _ = _procesador(_ok(llamadas))
    assert p.procesar("1-0", CAMPOS) == "ok"
    assert len(llamadas) == 1 and redis.acks == ["1-0"]
    assert alm.estado == {f"repo_1:{SHA}": 200}
    assert alm.llamadas[:1] == ["reservar"] and alm.llamadas[-1] == "completar"


def test_reinyectar_el_mismo_evento_no_vuelve_a_ejecutar() -> None:
    llamadas: list[EventoIngesta] = []
    p, redis, _alm, _ = _procesador(_ok(llamadas))
    resultados = [p.procesar(f"{i}-0", CAMPOS) for i in range(1, 5)]
    assert resultados == ["ok", "omitido", "omitido", "omitido"]
    assert len(llamadas) == 1
    assert redis.acks == ["1-0", "2-0", "3-0", "4-0"]  # los duplicados también se confirman


def test_mismo_commit_en_otro_repo_o_tenant_no_es_duplicado() -> None:
    llamadas: list[EventoIngesta] = []
    p, _redis, _alm, _ = _procesador(_ok(llamadas))
    p.procesar("1-0", CAMPOS)
    p.procesar("2-0", {**CAMPOS, "repo_id": "repo_2"})
    assert len(llamadas) == 2


def test_una_reserva_en_curso_en_otro_worker_no_se_confirma_ni_se_ejecuta() -> None:
    llamadas: list[EventoIngesta] = []
    alm = AlmacenFalso()
    alm.estado[f"repo_1:{SHA}"] = 0
    p, redis, _, _ = _procesador(_ok(llamadas), almacen=alm)
    assert p.procesar("1-0", CAMPOS) == "en_curso"
    assert llamadas == [] and redis.acks == []  # queda pendiente para XAUTOCLAIM


def test_un_commit_con_snapshot_completo_no_se_reprocesa() -> None:
    llamadas: list[EventoIngesta] = []
    alm = AlmacenFalso()
    alm.snapshots[SHA] = "snap_1"
    p, redis, _, _ = _procesador(_ok(llamadas), almacen=alm)
    assert p.procesar("1-0", CAMPOS) == "omitido_snapshot"
    assert llamadas == [] and redis.acks == ["1-0"] and alm.estado == {f"repo_1:{SHA}": 200}


def test_el_ejecutor_recibe_el_trace_id_del_evento_o_genera_uno() -> None:
    llamadas: list[EventoIngesta] = []
    p, _r, _a, _ = _procesador(_ok(llamadas))
    p.procesar("1-0", {**CAMPOS, "trace_id": "tr_dado"})
    p.procesar("2-0", {**CAMPOS, "repo_id": "otro"})
    assert llamadas[0].trace_id == "tr_dado"
    assert llamadas[1].trace_id and llamadas[1].trace_id.startswith("cm_")


# ---------------------------------------------------------------------------
# Fallos: reintento con backoff y DLQ
# ---------------------------------------------------------------------------
def test_backoff_exponencial_acotado() -> None:
    assert [backoff_segundos(n, 2.0, 60.0) for n in (1, 2, 3, 4, 5, 6, 7)] == [
        2.0,
        4.0,
        8.0,
        16.0,
        32.0,
        60.0,
        60.0,
    ]
    assert backoff_segundos(0, 2.0, 60.0) == 2.0


def test_un_fallo_libera_la_reserva_y_reprograma_con_backoff() -> None:
    p, redis, alm, reloj = _procesador(_falla(RuntimeError("TEI caído")))
    assert p.procesar("1-0", CAMPOS) == "reintento"
    assert "liberar" in alm.llamadas and alm.estado == {}
    assert redis.acks == ["1-0"]  # el original se confirma: el reintento vive en el ZSET
    [(miembro, score)] = redis.zsets[worker.STREAM_REINTENTOS].items()
    assert score == reloj.ahora + 2.0
    campos = json.loads(miembro)
    assert campos["intentos"] == "1" and campos["commit_sha"] == SHA and campos["retry_id"]
    assert campos["trace_id"].startswith("cm_")
    assert worker.DLQ not in redis.streams


def test_el_reintento_vencido_vuelve_al_stream_con_el_contador() -> None:
    p, redis, _alm, reloj = _procesador(_falla(RuntimeError("x")))
    p.procesar("1-0", CAMPOS)
    assert p.promover_reintentos() == 0  # todavía no vence
    reloj.ahora += 2.0
    assert p.promover_reintentos() == 1
    [(_, campos)] = redis.streams[worker.STREAM]
    assert campos["intentos"] == "1" and not redis.zsets[worker.STREAM_REINTENTOS]


def test_el_backoff_crece_con_cada_intento_y_el_ultimo_va_a_la_dlq() -> None:
    p, redis, alm, reloj = _procesador(_falla(RuntimeError("persistente")), max_intentos=3)
    esperas: list[float] = []
    campos = dict(CAMPOS)
    for _ in range(2):
        assert p.procesar("1-0", campos) == "reintento"
        [(miembro, score)] = redis.zsets[worker.STREAM_REINTENTOS].items()
        esperas.append(score - reloj.ahora)
        redis.zsets[worker.STREAM_REINTENTOS].clear()
        campos = json.loads(miembro)
    assert esperas == [2.0, 4.0]
    assert p.procesar("9-0", campos) == "dlq"  # tercer intento == max_intentos
    assert alm.estado == {}  # nada queda reservado


def test_la_dlq_lleva_trace_id_error_estructurado_y_metrica() -> None:
    p, redis, _alm, _ = _procesador(_falla(ValueError("falló el parseo")), max_intentos=1)
    assert p.procesar("7-0", {**CAMPOS, "trace_id": "tr_9"}) == "dlq"
    [(_, entrada)] = redis.streams[worker.DLQ]
    assert entrada["original_stream"] == worker.STREAM and entrada["original_mid"] == "7-0"
    assert entrada["trace_id"] == "tr_9" and entrada["tenant_id"] == "org_a"
    assert entrada["repo_id"] == "repo_1" and entrada["commit_sha"] == SHA
    assert entrada["attempts"] == "1" and entrada["error"] == "ValueError: falló el parseo"
    est = json.loads(entrada["error_estructurado"])
    assert est["tipo"] == "ValueError" and est["mensaje"] == "falló el parseo"
    assert est["frames"] and {"archivo", "linea", "funcion"} <= est["frames"][-1].keys()
    assert json.loads(entrada["payload"])["commit_sha"] == SHA
    assert redis.acks == ["7-0"]
    assert redis.contadores[f"kc:metrics:dlq:{worker.STREAM}"] == 1


def test_un_error_no_recuperable_va_a_la_dlq_sin_reintentos() -> None:
    p, redis, _a, _ = _procesador(_falla(ErrorNoRecuperable("credenciales vencidas")))
    assert p.procesar("1-0", CAMPOS) == "dlq"
    assert not redis.zsets.get(worker.STREAM_REINTENTOS)


def test_un_evento_invalido_va_directo_a_la_dlq_y_no_ejecuta() -> None:
    llamadas: list[EventoIngesta] = []
    p, redis, alm, _ = _procesador(_ok(llamadas))
    assert p.procesar("3-0", {"tenant_id": "org_a", "commit_sha": "x"}) == "dlq"
    assert llamadas == [] and alm.llamadas == [] and redis.acks == ["3-0"]
    [(_, entrada)] = redis.streams[worker.DLQ]
    assert (
        entrada["trace_id"].startswith("cm_") and "evento de ingesta inválido" in entrada["error"]
    )


def test_la_dlq_y_los_errores_no_filtran_credenciales_de_la_url() -> None:
    url = "https://usuario:tok3n@github.com/acme/app.git"
    p, redis, _a, _ = _procesador(
        _falla(RuntimeError(f"fatal: no se pudo clonar {url}")), max_intentos=1
    )
    p.procesar("1-0", {**CAMPOS, "repo_url": url})
    [(_, entrada)] = redis.streams[worker.DLQ]
    volcado = json.dumps(entrada)
    assert "tok3n" not in volcado  # el usuario puede quedar; la contraseña o token, no


def test_error_estructurado_acota_mensaje_y_frames() -> None:
    def profundo(n: int) -> None:
        if n == 0:
            raise RuntimeError("x" * 10_000)
        profundo(n - 1)

    try:
        profundo(50)
    except RuntimeError as exc:
        est = error_estructurado(exc)
    assert len(est["mensaje"]) == worker.MENSAJE_MAX_CHARS
    assert len(est["frames"]) == worker.FRAMES_MAX


def test_si_no_se_puede_marcar_completado_el_evento_se_reintenta() -> None:
    alm = AlmacenFalso()
    alm.fallar_completar = True
    llamadas: list[EventoIngesta] = []
    p, redis, _, _ = _procesador(_ok(llamadas), almacen=alm)
    assert p.procesar("1-0", CAMPOS) == "reintento"
    assert redis.zsets[worker.STREAM_REINTENTOS]


# ---------------------------------------------------------------------------
# Bucle: un mensaje envenenado no bloquea al grupo
# ---------------------------------------------------------------------------
class RedisConLecturas(RedisFalso):
    def __init__(
        self, lotes: list[list[tuple[str, dict[str, str]]]], parar: threading.Event
    ) -> None:
        super().__init__()
        self.lotes = lotes
        self.parar = parar
        self.grupos: list[tuple[str, str]] = []

    def xgroup_create(self, stream: str, grupo: str, id: str = "0", mkstream: bool = False) -> None:
        self.grupos.append((stream, grupo))

    def xreadgroup(self, grupo: str, consumidor: str, streams: dict[str, str], **kw: Any) -> Any:
        if not self.lotes:
            self.parar.set()
            return []
        return [(worker.STREAM, self.lotes.pop(0))]

    def xautoclaim(self, *a: Any, **kw: Any) -> Any:
        return ("0-0", [], [])


def test_el_bucle_sigue_despues_de_un_mensaje_envenenado() -> None:
    parar = threading.Event()
    bueno = {**CAMPOS, "repo_id": "repo_ok"}
    redis = RedisConLecturas(
        [[("1-0", {"basura": "1"}), ("2-0", bueno)], [("3-0", bueno)]],
        parar,
    )
    llamadas: list[EventoIngesta] = []
    alm = AlmacenFalso()
    cfg = ConfigWorker(max_intentos=2, bloqueo_ms=10)
    p = Procesador(redis, alm, _ok(llamadas), cfg, "test-1", Reloj())  # type: ignore[arg-type]
    worker.ciclo(p, parar)
    assert redis.grupos == [(worker.STREAM, worker.GRUPO)]
    assert len(llamadas) == 1  # el bueno se procesó; el repetido (3-0) fue duplicado
    assert [m for m, _ in redis.streams[worker.DLQ]] and redis.acks == ["1-0", "2-0", "3-0"]
