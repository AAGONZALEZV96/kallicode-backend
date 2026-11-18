"""Pruebas de observabilidad (/metrics, Grafana) y Dockerfile del worker (KC-CM-10-3 / KC-CM-10-5)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from kallicode_codemapping.metricas import (
    REGISTRY_CODEMAPPING,
    iniciar_servidor_metrics,
    medir_embedding_tei,
    medir_fase,
    renderizar_metrics,
)

ROOT = Path(__file__).resolve().parents[3]


def test_metrics_openmetrics_y_baja_cardinalidad() -> None:
    """Verifica que /metrics exponga métricas Prometheus sin labels de alta cardinalidad."""
    with medir_fase("full"):
        pass
    with medir_embedding_tei("embed"):
        pass

    payload_bytes, content_type = renderizar_metrics()
    texto = payload_bytes.decode("utf-8")

    assert "text/plain" in content_type or "openmetrics" in content_type
    assert "kc_codemapping_ingesta_fase_duracion_segundos" in texto
    assert "kc_codemapping_worker_eventos_total" in texto
    assert "kc_codemapping_worker_errores_total" in texto
    assert "kc_codemapping_stream_longitud" in texto
    assert "kc_codemapping_dlq_mensajes" in texto
    assert "kc_codemapping_tei_embeddings_duracion_segundos" in texto

    for metric in REGISTRY_CODEMAPPING.collect():
        for sample in metric.samples:
            assert "tenant_id" not in sample.labels
            assert "repo_id" not in sample.labels
            assert "commit_sha" not in sample.labels


def test_servidor_http_metrics_responde_200() -> None:
    """Levanta el servidor HTTP de métricas en puerto efímero y valida GET /metrics."""
    servidor = iniciar_servidor_metrics(host="127.0.0.1", port=0)
    try:
        puerto = servidor.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/metrics", timeout=3) as resp:
            assert resp.status == 200
            cuerpo = resp.read().decode("utf-8")
            assert "kc_codemapping_stream_longitud" in cuerpo
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_dashboard_grafana_versionado_valido() -> None:
    """Verifica el JSON del panel Grafana para el worker de CodeMapping."""
    ruta = ROOT / "infra/grafana/codemapping-worker-dashboard.json"
    assert ruta.exists(), "Falta infra/grafana/codemapping-worker-dashboard.json"
    data = json.loads(ruta.read_text(encoding="utf-8"))
    assert data["uid"] == "kc-codemapping-worker"
    assert len(data["panels"]) >= 5
    expresiones = " ".join(
        t["expr"] for p in data["panels"] for t in p.get("targets", []) if "expr" in t
    )
    assert "kc_codemapping_ingesta_fase_duracion_segundos" in expresiones
    assert "kc_codemapping_dlq_mensajes" in expresiones


def test_dockerfile_codemapping_multi_stage_no_root_y_sha() -> None:
    """Verifica usuario no-root, fijación de GIT_SHA, --no-dev y HEALTHCHECK."""
    dockerfile = ROOT / "apps/workers/Dockerfile.codemapping"
    assert dockerfile.exists(), "Falta apps/workers/Dockerfile.codemapping"
    contenido = dockerfile.read_text(encoding="utf-8")

    assert "AS builder" in contenido
    assert "--no-dev" in contenido
    assert "ARG GIT_SHA" in contenido
    assert "KC_GIT_SHA" in contenido
    assert "USER app" in contenido
    assert "USER root" not in contenido
    assert "HEALTHCHECK" in contenido
    assert "kallicode_codemapping.worker" in contenido


def test_dockerfile_codemapping_endurecido() -> None:
    """KC-CM-12-6: versiones fijadas, sin credenciales embebidas, código de solo lectura y sin pruebas."""
    contenido = (ROOT / "apps/workers/Dockerfile.codemapping").read_text(encoding="utf-8")
    assert ":latest" not in contenido
    assert "postgres:dev" not in contenido and "localhost:5432" not in contenido
    assert "--chown=app:app /app/.venv" not in contenido  # el venv no es del usuario de ejecución
    assert "apt-get upgrade" in contenido
    ignorados = (ROOT / "apps/workers/Dockerfile.codemapping.dockerignore").read_text(
        encoding="utf-8"
    )
    for patron in (
        "packages/*/tests",
        "packages/*/test",
        "packages/codemapping/mutants",
        "**/.env",
    ):
        assert patron in ignorados.splitlines()
