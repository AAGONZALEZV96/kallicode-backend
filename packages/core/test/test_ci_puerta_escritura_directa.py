import re
from pathlib import Path

ARCHIVOS_AUTORIZADOS = {
    "packages/core/src/kallicode_core/pipeline/transiciones.py",
    "packages/core/src/kallicode_core/pipeline/__init__.py",
}

# Baseline heredado: número máximo permitido de escrituras directas legacy en todo el repo.
# Esta cifra solo puede disminuir en futuros PRs hasta llegar a 0.
MAXIMO_ESCRITURAS_LEGACY = 12

PATRON_UPDATE_ESTADO = re.compile(
    r"UPDATE\s+(?:core\.)?jobs\s+SET\s+.*?estado\s*=",
    re.IGNORECASE | re.DOTALL,
)


def test_puerta_ci_no_permite_mas_escrituras_directas():
    """
    KC-FAB-02-5: Puerta de CI contra mutaciones no controladas de core.jobs.estado.
    Asegura que la deuda técnica heredada no aumente y que ningún módulo nuevo
    escriba directamente sin pasar por transicionar().
    """
    ruta_raiz = Path(__file__).resolve().parents[3]
    carpetas_a_inspeccionar = ["apps", "packages"]
    total_violaciones = 0
    detalle_violaciones = []

    for carpeta in carpetas_a_inspeccionar:
        ruta_carpeta = ruta_raiz / carpeta
        if not ruta_carpeta.exists():
            continue

        for archivo_py in ruta_carpeta.rglob("*.py"):
            rel_path = archivo_py.relative_to(ruta_raiz).as_posix()

            if any(rel_path.startswith(auth) for auth in ARCHIVOS_AUTORIZADOS):
                continue
            if "test" in rel_path or "migrations" in rel_path or "__pycache__" in rel_path:
                continue

            contenido = archivo_py.read_text(encoding="utf-8", errors="ignore")
            matches = list(PATRON_UPDATE_ESTADO.finditer(contenido))
            if matches:
                total_violaciones += len(matches)
                detalle_violaciones.append(f"{rel_path}: {len(matches)} ocurrencias")

    assert total_violaciones <= MAXIMO_ESCRITURAS_LEGACY, (
        f"REGRESIÓN DETECTADA EN CI (KC-FAB-02-5):\n"
        f"Se encontraron {total_violaciones} escrituras directas (máximo permitido: {MAXIMO_ESCRITURAS_LEGACY}).\n"
        f"Detalle:\n" + "\n".join(f"  - {d}" for d in detalle_violaciones)
    )
