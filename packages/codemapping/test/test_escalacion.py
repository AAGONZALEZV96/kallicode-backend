"""Escalación humana de una ingesta bloqueada (KC-CM-10-6) sin base de datos ni Redis.

El endpoint interno de Backend se simula con respx sobre el cliente HTTP real; Redis, la
idempotencia y la pausa del snapshot van en memoria. La pausa contra PostgreSQL real está en
tests/test_escalacion_pg.py.

Criterios:
- Ante un bloqueo irrecuperable (credenciales vencidas, repositorio corrupto, tiers agotados) se
  emite la solicitud de escalación con contexto diagnóstico y trace_id, según cm-escalacion-v1.
- El snapshot queda pausado (pendiente_intervencion) hasta la resolución y es reanudable después.
- La escalación incluye paso, tier, lote y referencia de entrada, sin código del cliente.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from kallicode_codemapping import llm_enrich, worker
from kallicode_codemapping.escalacion import (
    ClienteEscalacion,
    ErrorNoRecuperable,
    ErrorNotificacionEscalacion,
    Escalador,
    EscaladoSemantico,
    clasificar_bloqueo,
    construir_solicitud,
    tiers_de,
)
from kallicode_codemapping.ingest_repo import GitSyncError
from kallicode_codemapping.parser_treesitter import Symbol
from kallicode_codemapping.worker import (
    ConfigWorker,
    EventoIngesta,
    Procesador,
    error_estructurado,
)
from kallicode_core.cm_escalacion import (
    MENSAJE_MAX_CHARS,
    RUTA_ENDPOINT,
    EscalacionIngestaIn,
    EscalacionIngestaOut,
)
from kallicode_core.llm.cliente import LLMEscaladoHumano

SHA = "b" * 40
TOKEN_REPO = "ghp_TOKEN_SECRETO_123"
CAMPOS = {
    "tenant_id": "org_a",
    "repo_id": "repo_1",
    "repo_url": f"https://x-access-token:{TOKEN_REPO}@github.com/acme/app.git",
    "commit_sha": SHA,
    "trace_id": "tr_onboarding_42",
}
URL_BASE = "http://api.test/internal/v1"
URL = URL_BASE + RUTA_ENDPOINT
CODIGO_CLIENTE = "def calcular_tarifa_secreta(): return PRECIO_INTERNO_7781"


# ---------------------------------------------------------------------------
# Dobles en memoria
# ---------------------------------------------------------------------------
class RedisFalso:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.acks: list[str] = []
        self._n = 0

    def xadd(self, stream: str, campos: dict[str, str], **_: Any) -> str:
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
        return 1


class AlmacenFalso:
    lease_segundos = 3600

    def __init__(self) -> None:
        self.estado: dict[str, int] = {}

    def reservar(self, evento: EventoIngesta, trace_id: str, worker_id: str) -> str:
        actual = self.estado.get(evento.clave)
        if actual is None:
            self.estado[evento.clave] = 0
            return "reservada"
        return "en_curso" if actual == 0 else "completada"

    def renovar(self, evento: EventoIngesta) -> None:
        pass

    def completar(self, evento: EventoIngesta, resumen: dict[str, Any]) -> None:
        self.estado[evento.clave] = 200

    def liberar(self, evento: EventoIngesta) -> None:
        if self.estado.get(evento.clave) == 0:
            del self.estado[evento.clave]

    def snapshot_completo(self, evento: EventoIngesta) -> str | None:
        return None


class PausaFalsa:
    """Misma semántica que PausaSnapshots: un snapshot por commit con su estado y su detalle."""

    def __init__(self, fallar_pausa: bool = False) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.fallar_pausa = fallar_pausa

    def pausar(self, tenant_id: str, commit_sha: str, detalle: dict[str, Any]) -> str:
        if self.fallar_pausa:
            raise RuntimeError("base caída")
        snap = self.snapshots.setdefault(
            commit_sha, {"id": f"snap_{commit_sha[:6]}", "escalacion": {}}
        )
        snap["estado"] = "pendiente_intervencion"
        snap["escalacion"] = dict(detalle)
        return str(snap["id"])

    def anotar(self, tenant_id: str, snapshot_id: str, campos: dict[str, Any]) -> None:
        for snap in self.snapshots.values():
            if snap["id"] == snapshot_id:
                snap["escalacion"].update(campos)

    def en_pausa(self, tenant_id: str, commit_sha: str) -> str | None:
        snap = self.snapshots.get(commit_sha)
        return str(snap["id"]) if snap and snap["estado"] == "pendiente_intervencion" else None

    def reanudar(self, tenant_id: str, commit_sha: str, resuelto_por: str) -> str | None:
        snap = self.snapshots.get(commit_sha)
        if not snap or snap["estado"] != "pendiente_intervencion":
            return None
        snap["estado"] = "fallido"
        snap["escalacion"].update({"estado": "resuelta", "resuelto_por": resuelto_por})
        return str(snap["id"])


class Montaje:
    def __init__(self, ejecutor: Any, *, max_intentos: int = 3, pausa: PausaFalsa | None = None):
        self.redis = RedisFalso()
        self.almacen = AlmacenFalso()
        self.pausa = pausa or PausaFalsa()
        self.esperas: list[float] = []
        self.cliente = ClienteEscalacion(
            URL_BASE, emitir_token=lambda t: f"tok-{t}", dormir=self.esperas.append
        )
        self.procesador = Procesador(
            self.redis,
            self.almacen,  # type: ignore[arg-type]
            ejecutor,
            ConfigWorker(max_intentos=max_intentos),
            "test-1",
            lambda: 1_000.0,
            escalador=Escalador(self.cliente, self.pausa),
        )

    def dlq(self) -> list[dict[str, str]]:
        return [c for _, c in self.redis.streams.get(worker.DLQ, [])]


def _falla(error: Exception) -> Any:
    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        raise error

    return ejecutor


def _cuerpo(llamada: Any) -> dict[str, Any]:
    return dict(json.loads(llamada.request.content))


@pytest.fixture
def backend() -> Any:
    # assert_all_called=False: varias pruebas verifican justamente que NO se escale.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        ruta = router.post(URL).mock(
            return_value=httpx.Response(201, json={"escalation_id": "esc_01", "estado": "abierta"})
        )
        yield ruta


def _error_credenciales() -> GitSyncError:
    return GitSyncError(
        "Error sincronizando rama main para tenant org_a: fatal: Authentication failed for "
        f"'https://x-access-token:{TOKEN_REPO}@github.com/acme/app.git/'"
    )


# ---------------------------------------------------------------------------
# Criterio 1: bloqueo irrecuperable -> solicitud con diagnóstico y trace_id
# ---------------------------------------------------------------------------
def test_credenciales_expiradas_emiten_la_escalacion_con_trace_id_y_diagnostico(
    backend: Any,
) -> None:
    m = Montaje(_falla(_error_credenciales()))

    assert m.procesador.procesar("1-0", CAMPOS) == "dlq"

    assert backend.call_count == 1  # sin reintentos de la ingesta: escala a la primera
    llamada = backend.calls.last
    solicitud = EscalacionIngestaIn.model_validate(_cuerpo(llamada))  # cumple el contrato
    assert solicitud.motivo == "credenciales_expiradas" and solicitud.severidad == "alta"
    assert solicitud.trace_id == "tr_onboarding_42"
    assert solicitud.tenant_id == "org_a" and solicitud.commit_sha == SHA
    assert solicitud.paso == "ingesta" and solicitud.tier is None and solicitud.lote is None
    assert solicitud.entrada_ref == f"repo_1@{SHA}"
    assert solicitud.snapshot_id == f"snap_{SHA[:6]}"
    assert solicitud.diagnostico.tipo_error == "GitSyncError"
    assert "Authentication failed" in solicitud.diagnostico.mensaje
    assert solicitud.diagnostico.intentos == 1
    assert solicitud.diagnostico.frames, "la traza llega como frames acotados"

    cab = llamada.request.headers
    assert cab["Authorization"] == "Bearer tok-org_a"
    assert cab["Idempotency-Key"] == f"cm-esc:org_a:repo_1:{SHA}:credenciales_expiradas"
    assert cab["X-Trace-Id"] == "tr_onboarding_42"
    assert TOKEN_REPO not in llamada.request.content.decode()

    assert m.redis.zsets == {}  # no se reprograma
    [dlq] = m.dlq()
    assert dlq["motivo_escalacion"] == "credenciales_expiradas"
    assert dlq["escalation_id"] == "esc_01" and dlq["escalacion_notificada"] == "1"
    assert dlq["snapshot_id"] == f"snap_{SHA[:6]}" and dlq["trace_id"] == "tr_onboarding_42"
    assert m.redis.acks == ["1-0"]


def test_repositorio_corrupto_se_escala_con_su_motivo(backend: Any) -> None:
    error = subprocess.CalledProcessError(
        128,
        ["git", "fetch"],
        stderr="error: object file .git/objects/ab/cdef is empty\nfatal: loose object abcdef "
        "is corrupt",
    )
    m = Montaje(_falla(error))
    assert m.procesador.procesar("1-0", CAMPOS) == "dlq"
    solicitud = EscalacionIngestaIn.model_validate(_cuerpo(backend.calls.last))
    assert solicitud.motivo == "repositorio_corrupto" and solicitud.severidad == "alta"


def test_reintentos_agotados_escalan_solo_en_el_ultimo_intento(backend: Any) -> None:
    m = Montaje(_falla(TimeoutError("timeout leyendo el repositorio")), max_intentos=2)

    assert m.procesador.procesar("1-0", CAMPOS) == "reintento"
    assert backend.call_count == 0  # un fallo transitorio no molesta a nadie

    assert m.procesador.procesar("2-0", {**CAMPOS, "intentos": "1"}) == "dlq"
    solicitud = EscalacionIngestaIn.model_validate(_cuerpo(backend.calls.last))
    assert solicitud.motivo == "reintentos_agotados" and solicitud.severidad == "media"
    assert solicitud.diagnostico.intentos == 2


def test_un_evento_invalido_va_a_la_dlq_sin_escalar(backend: Any) -> None:
    m = Montaje(_falla(AssertionError("no debe ejecutarse")))
    assert m.procesador.procesar("1-0", {**CAMPOS, "commit_sha": "no-sha"}) == "dlq"
    assert backend.call_count == 0  # es un error del productor, no del cliente
    assert "motivo_escalacion" not in m.dlq()[0]


# ---------------------------------------------------------------------------
# Criterio 3: paso, tier y lote de la cadena semántica, sin código del cliente
# ---------------------------------------------------------------------------
def _simbolo(i: int) -> Symbol:
    return Symbol(
        id=f"sym_{i}",
        file="src/tarifas.py",
        language="python",
        kind="function",
        name=f"calcular_{i}",
        start_line=1,
        end_line=2,
        signature="def calcular_tarifa_secreta()",
        snippet=CODIGO_CLIENTE,
    )


def test_tiers_agotados_en_un_lote_escalan_con_paso_tier_y_lote_sin_codigo(
    backend: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    llamadas: list[int] = []

    def paso(nombre: str, tier: Any, variables: dict[str, Any], tenant_id: Any = None) -> Any:
        llamadas.append(len(variables["symbols_batch"]))
        if len(llamadas) == 2:  # el segundo lote no lo resuelve ningún tier
            raise LLMEscaladoHumano(
                nombre,
                "tiers_agotados",
                [
                    {"tier": "flash", "modelo": "m1", "resultado": "esquema_invalido"},
                    {"tier": "flash", "modelo": "m1", "resultado": "esquema_invalido"},
                    {"tier": "pro", "modelo": "m2", "resultado": "baja_confianza"},
                    {"tier": "fable", "modelo": "m3", "resultado": "esquema_invalido"},
                ],
            )

        return SimpleNamespace(salida={"symbols": []})

    monkeypatch.setattr(llm_enrich, "_ejecutar_paso_resultado_sync", paso)

    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        llm_enrich.describe_symbols([_simbolo(i) for i in range(5)], batch_size=2)
        return {}

    m = Montaje(ejecutor)
    assert m.procesador.procesar("1-0", CAMPOS) == "dlq"

    contenido = backend.calls.last.request.content.decode()
    solicitud = EscalacionIngestaIn.model_validate(json.loads(contenido))
    assert solicitud.motivo == "tiers_agotados"
    assert solicitud.paso == "codemapping.describe_symbols"
    assert solicitud.tier == "fable" and solicitud.lote == 1
    assert solicitud.diagnostico.tiers_probados == ["flash", "pro", "fable"]
    assert solicitud.diagnostico.tipo_error == "EscaladoSemantico"
    assert "PRECIO_INTERNO_7781" not in contenido and "calcular_tarifa" not in contenido
    assert TOKEN_REPO not in contenido


def test_el_diagnostico_se_queda_con_la_primera_linea_acotada() -> None:
    exc = ErrorNoRecuperable("x" * 900 + "\n" + CODIGO_CLIENTE, motivo="repositorio_corrupto")
    solicitud = construir_solicitud(
        tenant_id="org_a",
        repo_id="repo_1",
        commit_sha=SHA,
        trace_id="tr",
        motivo="repositorio_corrupto",
        error=error_estructurado(exc),
        exc=exc,
        intentos=1,
        snapshot_id=None,
    )
    assert len(solicitud.diagnostico.mensaje) == MENSAJE_MAX_CHARS
    assert "PRECIO_INTERNO" not in solicitud.model_dump_json()


# ---------------------------------------------------------------------------
# Criterio 2: snapshot pausado hasta la resolución y reanudable
# ---------------------------------------------------------------------------
def test_el_snapshot_queda_pausado_y_los_eventos_nuevos_no_ejecutan(backend: Any) -> None:
    ejecutados: list[str] = []
    errores = [_error_credenciales()]

    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        ejecutados.append(evento.commit_sha)
        if errores:
            raise errores.pop()
        return {"modo": evento.modo}

    m = Montaje(ejecutor)
    assert m.procesador.procesar("1-0", CAMPOS) == "dlq"
    snap = m.pausa.snapshots[SHA]
    assert snap["estado"] == "pendiente_intervencion"
    assert snap["escalacion"]["notificada"] is True
    assert snap["escalacion"]["escalation_id"] == "esc_01"
    assert snap["escalacion"]["trace_id"] == "tr_onboarding_42"

    # Llega otro evento del mismo commit (refresco diario, reintento del onboarding...).
    assert m.procesador.procesar("2-0", CAMPOS) == "en_pausa"
    assert ejecutados == [SHA] and backend.call_count == 1
    assert m.redis.acks == ["1-0", "2-0"]  # no bloquea al grupo
    assert m.almacen.estado == {}  # la reserva no queda tomada

    # Resolución: se levanta la pausa y el evento vuelve al stream.
    mid = m.procesador.reanudar(EventoIngesta.desde_campos(CAMPOS), resuelto_por="usr_soporte")
    assert mid is not None
    assert snap["estado"] == "fallido"  # reanudable desde su checkpoint
    assert snap["escalacion"]["resuelto_por"] == "usr_soporte"
    [(_, reencolado)] = m.redis.streams[worker.STREAM]
    assert reencolado["reanudado_de"] == f"snap_{SHA[:6]}"
    assert reencolado["trace_id"] == "tr_onboarding_42" and "intentos" not in reencolado

    assert m.procesador.procesar(mid, reencolado) == "ok"
    assert ejecutados == [SHA, SHA]


def test_reanudar_un_commit_sin_pausa_no_reencola_nada() -> None:
    m = Montaje(_falla(AssertionError()))
    assert m.procesador.reanudar(EventoIngesta.desde_campos(CAMPOS), "usr") is None
    assert worker.STREAM not in m.redis.streams


def test_reanudar_sin_escalador_es_un_error_de_configuracion() -> None:
    p = Procesador(RedisFalso(), AlmacenFalso(), _falla(AssertionError()))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="escalador"):
        p.reanudar(EventoIngesta.desde_campos(CAMPOS), "usr")


def test_sin_escalador_el_worker_se_comporta_como_antes() -> None:
    redis = RedisFalso()
    p = Procesador(
        redis,
        AlmacenFalso(),  # type: ignore[arg-type]
        _falla(_error_credenciales()),
        ConfigWorker(max_intentos=3),
        "t",
        lambda: 1.0,
    )
    assert p.procesar("1-0", CAMPOS) == "dlq"
    assert "motivo_escalacion" not in redis.streams[worker.DLQ][0][1]


# ---------------------------------------------------------------------------
# Contrato de notificación: reintentos, rechazos e idempotencia
# ---------------------------------------------------------------------------
def test_un_5xx_se_reintenta_con_la_misma_clave_de_idempotencia() -> None:
    with respx.mock(assert_all_mocked=True) as router:
        ruta = router.post(URL).mock(
            side_effect=[
                httpx.Response(503),
                httpx.ConnectError("caído"),
                httpx.Response(200, json={"escalation_id": "esc_07", "estado": "abierta"}),
            ]
        )
        m = Montaje(_falla(_error_credenciales()))
        assert m.procesador.procesar("1-0", CAMPOS) == "dlq"
    assert ruta.call_count == 3
    claves = {c.request.headers["Idempotency-Key"] for c in ruta.calls}
    assert len(claves) == 1
    assert m.esperas == [0.5, 1.0]
    assert m.dlq()[0]["escalation_id"] == "esc_07"


def test_un_4xx_no_se_reintenta_y_la_pausa_se_mantiene() -> None:
    with respx.mock(assert_all_mocked=True) as router:
        ruta = router.post(URL).mock(return_value=httpx.Response(422, json={"detail": "x"}))
        m = Montaje(_falla(_error_credenciales()))
        assert m.procesador.procesar("1-0", CAMPOS) == "dlq"
    assert ruta.call_count == 1
    snap = m.pausa.snapshots[SHA]
    assert snap["estado"] == "pendiente_intervencion"
    assert snap["escalacion"]["notificada"] is False
    assert "422" in snap["escalacion"]["error_notificacion"]
    dlq = m.dlq()[0]
    assert dlq["escalacion_notificada"] == "0" and dlq["escalation_id"] == ""


def test_backend_caido_agota_los_reintentos_sin_romper_el_worker() -> None:
    with respx.mock(assert_all_mocked=True) as router:
        ruta = router.post(URL).mock(side_effect=httpx.ConnectError("caído"))
        m = Montaje(_falla(_error_credenciales()))
        assert m.procesador.procesar("1-0", CAMPOS) == "dlq"
    assert ruta.call_count == 3
    assert m.redis.acks == ["1-0"]
    assert m.pausa.en_pausa("org_a", SHA) is not None


def test_cliente_lanza_error_de_notificacion_tras_agotar() -> None:
    with respx.mock(assert_all_mocked=True) as router:
        router.post(URL).mock(return_value=httpx.Response(500))
        cliente = ClienteEscalacion(
            URL_BASE, emitir_token=lambda t: "tok", reintentos=2, dormir=lambda s: None
        )
        exc = ErrorNoRecuperable("x")
        sol = construir_solicitud(
            tenant_id="org_a",
            repo_id="r",
            commit_sha=SHA,
            trace_id="tr",
            motivo="otro",
            error=error_estructurado(exc),
            exc=exc,
            intentos=1,
            snapshot_id=None,
        )
        with pytest.raises(ErrorNotificacionEscalacion, match="2 intentos: HTTP 500"):
            cliente.notificar(sol)
        cliente.close()


def test_si_la_pausa_falla_la_escalacion_igual_se_notifica(backend: Any) -> None:
    m = Montaje(_falla(_error_credenciales()), pausa=PausaFalsa(fallar_pausa=True))
    assert m.procesador.procesar("1-0", CAMPOS) == "dlq"
    solicitud = EscalacionIngestaIn.model_validate(_cuerpo(backend.calls.last))
    assert solicitud.snapshot_id is None
    assert m.dlq()[0]["escalation_id"] == "esc_01"


def test_la_respuesta_ignora_campos_nuevos_de_backend() -> None:
    r = EscalacionIngestaOut.model_validate(
        {"escalation_id": "esc_1", "estado": "abierta", "responsable": "x"}
    )
    assert r.estado == "abierta"


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "motivo"),
    [
        (ErrorNoRecuperable("x"), "otro"),
        (ErrorNoRecuperable("x", motivo="credenciales_expiradas"), "credenciales_expiradas"),
        (LLMEscaladoHumano("p", "tiers_agotados", []), "tiers_agotados"),
        (EscaladoSemantico("p", "esquema", [], 0), "tiers_agotados"),
        (
            subprocess.CalledProcessError(
                128, "git", stderr="fatal: unable to access: The requested URL returned error: 403"
            ),
            "credenciales_expiradas",
        ),
        (
            GitSyncError("fatal: could not read Username for 'https://github.com'"),
            "credenciales_expiradas",
        ),
        (GitSyncError("fatal: bad object HEAD"), "repositorio_corrupto"),
        (TimeoutError("timeout"), None),
        (RuntimeError("conexión rechazada por PostgreSQL"), None),
    ],
)
def test_clasificar_bloqueo(exc: BaseException, motivo: str | None) -> None:
    assert clasificar_bloqueo(exc) == motivo


def test_la_causa_de_git_tambien_se_clasifica() -> None:
    try:
        try:
            raise subprocess.CalledProcessError(
                128, "git", stderr="remote: Invalid username or password."
            )
        except subprocess.CalledProcessError as causa:
            raise GitSyncError("Error sincronizando rama main") from causa
    except GitSyncError as exc:
        assert clasificar_bloqueo(exc) == "credenciales_expiradas"


def test_escalado_semantico_sigue_siendo_llm_escalado_humano() -> None:
    e = EscaladoSemantico("codemapping.link_definitions", "m", [{"tier": "pro"}], 3)
    assert isinstance(e, LLMEscaladoHumano) and e.tier == "pro" and e.lote == 3
    assert EscaladoSemantico("p", "m", [], None).tier is None
    assert tiers_de([{"tier": "flash"}, {"tier": "flash"}, {"modelo": "x"}]) == ["flash"]


def test_llm_enrich_no_reenvuelve_un_escalado_semantico(monkeypatch: pytest.MonkeyPatch) -> None:
    original = EscaladoSemantico("p", "m", [], 4)

    def paso(*_: Any, **__: Any) -> Any:
        raise original

    monkeypatch.setattr(llm_enrich, "_ejecutar_paso_resultado_sync", paso)
    with pytest.raises(EscaladoSemantico) as info:
        llm_enrich._ejecutar_paso_sync("p", llm_enrich.Tier.FLASH, {}, lote=9)
    assert info.value is original


def test_por_defecto_usa_la_api_interna_y_un_jwt_de_servicio_valido() -> None:
    from kallicode_core.config import get_settings
    from kallicode_core.seguridad import validar_claims_servicio, verificar_token

    base = get_settings().internal_api_base_url.rstrip("/")
    with respx.mock(assert_all_mocked=True) as router:
        ruta = router.post(base + RUTA_ENDPOINT).mock(
            return_value=httpx.Response(201, json={"escalation_id": "esc_d", "estado": "abierta"})
        )
        cliente = ClienteEscalacion()
        exc = ErrorNoRecuperable("x")
        sol = construir_solicitud(
            tenant_id="org_a",
            repo_id="r",
            commit_sha=SHA,
            trace_id="tr",
            motivo="otro",
            error=error_estructurado(exc),
            exc=exc,
            intentos=1,
            snapshot_id=None,
        )
        assert cliente.notificar(sol).escalation_id == "esc_d"
        cliente.close()
    token = ruta.calls.last.request.headers["Authorization"].removeprefix("Bearer ")
    claims = validar_claims_servicio(verificar_token(token, "svc"))
    assert claims.svc == "ingesta" and claims.org == "org_a"
