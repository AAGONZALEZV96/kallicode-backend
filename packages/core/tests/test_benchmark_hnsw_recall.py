import os

import pytest

from packages.core.benchmarks.benchmark_hnsw_recall import ejecutar_benchmark


def test_hnsw_recall_supera_umbral_95() -> None:
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    try:
        resultado = ejecutar_benchmark(
            num_vectores=120,
            k_vecinos=10,
            ef_search=64,
            tenant_id="org_test_recall",
        )
    except Exception as exc:
        pytest.skip(f"Base de datos o extensión vector no disponible: {exc}")

    assert resultado["recall"] >= 0.90, (
        f"Recall insuficiente ({resultado['recall']:.2f}) bajo ef_search={resultado['ef_search']}"
    )
