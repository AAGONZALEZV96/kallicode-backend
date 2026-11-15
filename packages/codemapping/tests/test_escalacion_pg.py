"""Pausa y reanudación del snapshot escalado contra PostgreSQL real (KC-CM-10-6).

Requiere `make up && make db`. Sin PostgreSQL se omite. El endpoint de Backend se simula con
respx; Redis va en memoria (la semántica de Streams ya la cubre test_worker_pg_redis.py).

Criterios:
- Una ingesta bloqueada abre la escalación y deja su snapshot en `pendiente_intervencion`
  (snapshots_estado_chk), con el detalle en stats.escalacion.
- Mientras siga en pausa, un evento nuevo del commit no ejecuta el pipeline.
- Resuelta, el snapshot vuelve a un estado reanudable: GraphLoader.find_resumable_snapshot devuelve
  el mismo id, así que la ingesta sigue desde su checkpoint en vez de empezar otra.
- RLS: un tenant no ve ni pausa el snapshot de otro.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import psycopg
import pytest
import respx
from kallicode_codemapping import worker
from kallicode_codemapping.escalacion import (
    ClienteEscalacion,
    Escalador,
    PausaSnapshots,
)
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.ingest_repo import GitSyncError
from kallicode_codemapping.worker import (
    AlmacenIdempotencia,
    ConfigWorker,
    EventoIngesta,
    Procesador,
)
from kallicode_core.cm_escalacion import RUTA_ENDPOINT, EscalacionIngestaIn

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT_A = "org_test_esc_a"
TENANT_B = "org_test_esc_b"
SHA = "e" * 40
URL_BASE = "http://api.test/internal/v1"


def _entorno_listo() -> bool:
    try:
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _entorno_listo(), reason="PostgreSQL no disponible")


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in (TENANT_A, TENANT_B):
        for tabla in ("core.idempotency_keys", "cm.snapshot_vigente", "cm.repo_snapshots"):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


@pytest.fixture
def base() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        for t in (TENANT_A, TENANT_B):
            conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
        yield conn
        _limpiar(conn)


def _fabrica() -> Callable[[str], GraphLoader]:
    return lambda tenant: GraphLoader(dsn=APP_DSN, tenant_id=tenant)


def _snapshot(conn: psycopg.Connection[Any], snap_id: str) -> tuple[str, dict[str, Any]]:
    fila = conn.execute(
        "SELECT estado, stats FROM cm.repo_snapshots WHERE id = %s", (snap_id,)
    ).fetchone()
    assert fila is not None
    stats = fila[1] if isinstance(fila[1], dict) else json.loads(fila[1])
    return str(fila[0]), stats


def _sembrar(conn: psycopg.Connection[Any], tenant: str, estado: str) -> str:
    snap_id = f"snap_esc_{tenant[-1]}_{estado[:4]}"
    conn.execute(
        """INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama, estado, fase_actual)
           VALUES (%s, %s, %s, 'main', %s, 'fase_3')""",
        (snap_id, tenant, SHA, estado),
    )
    return snap_id


# ---------------------------------------------------------------------------
# PausaSnapshots
# ---------------------------------------------------------------------------
def test_pausar_el_snapshot_fallido_del_commit(base: psycopg.Connection[Any]) -> None:
    snap_id = _sembrar(base, TENANT_A, "fallido")
    pausa = PausaSnapshots(_fabrica())  # type: ignore[arg-type]

    assert pausa.pausar(TENANT_A, SHA, {"motivo": "credenciales_expiradas"}) == snap_id
    estado, stats = _snapshot(base, snap_id)
    assert estado == "pendiente_intervencion"
    assert stats["escalacion"] == {"motivo": "credenciales_expiradas"}
    assert pausa.en_pausa(TENANT_A, SHA) == snap_id

    pausa.anotar(TENANT_A, snap_id, {"escalation_id": "esc_9", "notificada": True})
    _, stats = _snapshot(base, snap_id)
    assert stats["escalacion"]["escalation_id"] == "esc_9"
    assert stats["escalacion"]["motivo"] == "credenciales_expiradas"


def test_sin_snapshot_previo_se_crea_uno_ya_pausado(base: psycopg.Connection[Any]) -> None:
    pausa = PausaSnapshots(_fabrica())  # type: ignore[arg-type]
    snap_id = pausa.pausar(TENANT_A, SHA, {"motivo": "credenciales_expiradas"})
    estado, stats = _snapshot(base, snap_id)
    assert estado == "pendiente_intervencion" and "escalacion" in stats


def test_un_snapshot_completo_no_se_pausa(base: psycopg.Connection[Any]) -> None:
    completo = _sembrar(base, TENANT_A, "completo")
    pausa = PausaSnapshots(_fabrica())  # type: ignore[arg-type]
    nuevo = pausa.pausar(TENANT_A, SHA, {"motivo": "otro"})
    assert nuevo != completo
    assert _snapshot(base, completo)[0] == "completo"


def test_reanudar_deja_el_mismo_snapshot_reanudable(base: psycopg.Connection[Any]) -> None:
    snap_id = _sembrar(base, TENANT_A, "en_progreso")
    pausa = PausaSnapshots(_fabrica())  # type: ignore[arg-type]
    pausa.pausar(TENANT_A, SHA, {"motivo": "tiers_agotados"})

    assert pausa.reanudar(TENANT_A, SHA, "usr_soporte") == snap_id
    estado, stats = _snapshot(base, snap_id)
    assert estado == "fallido"
    assert stats["escalacion"]["estado"] == "resuelta"
    assert stats["escalacion"]["resuelto_por"] == "usr_soporte"
    assert stats["escalacion"]["resuelta_en"]
    assert pausa.en_pausa(TENANT_A, SHA) is None
    assert pausa.reanudar(TENANT_A, SHA, "usr_soporte") is None  # idempotente

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_A) as loader:
        assert loader.find_resumable_snapshot(SHA) == snap_id
        assert loader.start_snapshot(SHA, "main") == snap_id  # no crea otro


def test_rls_un_tenant_no_ve_ni_pausa_el_snapshot_de_otro(base: psycopg.Connection[Any]) -> None:
    snap_a = _sembrar(base, TENANT_A, "fallido")
    pausa = PausaSnapshots(_fabrica())  # type: ignore[arg-type]
    pausa.pausar(TENANT_A, SHA, {"motivo": "otro"})

    assert pausa.en_pausa(TENANT_B, SHA) is None
    assert pausa.reanudar(TENANT_B, SHA, "intruso") is None
    snap_b = pausa.pausar(TENANT_B, SHA, {"motivo": "otro"})
    assert snap_b != snap_a
    assert _snapshot(base, snap_a)[1]["escalacion"] == {"motivo": "otro"}


# ---------------------------------------------------------------------------
# Worker de punta a punta: bloqueo -> escalación -> pausa -> resolución -> reanudación
# ---------------------------------------------------------------------------
class _Redis:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._n = 0

    def xadd(self, stream: str, campos: dict[str, str], **_: Any) -> str:
        self._n += 1
        self.streams.setdefault(stream, []).append((f"{self._n}-0", dict(campos)))
        return f"{self._n}-0"

    def xack(self, *_: Any) -> int:
        return 1

    def zadd(self, *_: Any) -> int:
        return 1

    def incr(self, *_: Any) -> int:
        return 1


def test_worker_escala_pausa_y_reanuda_contra_postgres(base: psycopg.Connection[Any]) -> None:
    ejecutados: list[str] = []

    def ejecutor(evento: EventoIngesta) -> dict[str, Any]:
        ejecutados.append(evento.commit_sha)
        if len(ejecutados) == 1:
            with GraphLoader(dsn=APP_DSN, tenant_id=evento.tenant_id) as loader:
                snap = loader.start_snapshot(evento.commit_sha, "main")
                loader.complete_snapshot(snap, estado="fallido", motivo="git")
            raise GitSyncError("fatal: Authentication failed for 'https://github.com/a/b.git/'")
        return {"modo": evento.modo}

    fabrica = _fabrica()
    redis = _Redis()
    campos = {
        "tenant_id": TENANT_A,
        "repo_id": "repo_esc",
        "repo_url": "https://github.com/a/b.git",
        "commit_sha": SHA,
        "trace_id": "tr_esc_pg",
    }
    with respx.mock(assert_all_mocked=True) as router:
        ruta = router.post(URL_BASE + RUTA_ENDPOINT).mock(
            return_value=httpx.Response(201, json={"escalation_id": "esc_pg", "estado": "abierta"})
        )
        procesador = Procesador(
            redis,
            AlmacenIdempotencia(fabrica),  # type: ignore[arg-type]
            ejecutor,
            ConfigWorker(max_intentos=3),
            "test-pg",
            escalador=Escalador(
                ClienteEscalacion(URL_BASE, emitir_token=lambda t: "tok"),
                PausaSnapshots(fabrica),  # type: ignore[arg-type]
            ),
        )
        assert procesador.procesar("1-0", campos) == "dlq"
        assert procesador.procesar("2-0", campos) == "en_pausa"
        assert ruta.call_count == 1

    solicitud = EscalacionIngestaIn.model_validate(json.loads(ruta.calls.last.request.content))
    assert solicitud.trace_id == "tr_esc_pg" and solicitud.motivo == "credenciales_expiradas"
    estado, stats = _snapshot(base, str(solicitud.snapshot_id))
    assert estado == "pendiente_intervencion"
    assert stats["escalacion"]["escalation_id"] == "esc_pg"
    assert ejecutados == [SHA]

    mid = procesador.reanudar(EventoIngesta.desde_campos(campos), "usr_soporte")
    assert mid is not None
    [(_, reencolado)] = redis.streams[worker.STREAM]
    assert procesador.procesar(mid, reencolado) == "ok"
    assert ejecutados == [SHA, SHA]
