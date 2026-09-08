"""KC-FAB-01-4: Inventario ejecutable del catálogo de plantillas LLM.

Audita las plantillas de la fábrica, su tier asignado, sus variables requeridas
y detecta los llamadores huérfanos (SIN_LLAMADOR) en el monorepo.
"""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

# Importar directamente el catálogo fuente de verdad
from kallicode_core.llm.plantillas import PLANTILLAS

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class PromptAuditEntry:
    id: str
    version: str
    tier: str
    escalable: bool
    description: str
    required_inputs: list[str]
    caller: str
    status: str


def search_caller(prompt_id: str) -> str:
    """Busca en el código fuente de apps y packages si existe una función que invoque el prompt."""
    search_dirs = [REPO_ROOT / "packages", REPO_ROOT / "apps"]
    pattern = re.compile(rf'["\']{re.escape(prompt_id)}["\']')

    for s_dir in search_dirs:
        if not s_dir.exists():
            continue
        for py_file in s_dir.rglob("*.py"):
            # Excluir el propio archivo de definición de plantillas y scripts de auditoría
            if py_file.name in ("plantillas.py", "audit_prompts.py", "test_prompt_catalog.py"):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                if pattern.search(content):
                    return str(py_file.relative_to(REPO_ROOT).as_posix())
            except OSError:
                continue

    return "SIN_LLAMADOR"


def run_audit() -> list[dict[str, Any]]:
    report: list[dict[str, Any]] = []
    orphan_count = 0

    print("=" * 105)
    print(
        f"{'PROMPT ID':<28} {'TIER':<8} {'ESCALA':<8} {'ESTADO':<15} {'LLAMADOR':<40}"
    )
    print("=" * 105)

    for prompt_id, data in PLANTILLAS.items():
        caller = search_caller(prompt_id)
        is_orphan = caller == "SIN_LLAMADOR"
        status = "HUÉRFANO" if is_orphan else "ACTIVO"

        if is_orphan:
            orphan_count += 1

        # Extraer nombres de parámetros de entrada documentados
        entrada = data.get("entrada", {})
        inputs = list(entrada.keys()) if isinstance(entrada, dict) else []

        tier_val = getattr(data["tier"], "value", str(data["tier"]))

        entry = PromptAuditEntry(
            id=prompt_id,
            version=str(data.get("version", "v1")),
            tier=tier_val,
            escalable=bool(data.get("escalable", False)),
            description=str(data.get("descripcion", "")),
            required_inputs=inputs,
            caller=caller,
            status=status,
        )
        report.append(asdict(entry))

        print(
            f"{prompt_id:<28} {tier_val:<8} {str(data.get('escalable')):<8} {status:<15} {caller:<40}"
        )

    print("=" * 105)
    print(f"Total plantillas auditadas: {len(PLANTILLAS)}")
    print(f"Plantillas con llamador activo: {len(PLANTILLAS) - orphan_count}")
    print(f"Plantillas huérfanas (SIN_LLAMADOR): {orphan_count}")
    print("=" * 105)

    output_path = REPO_ROOT / "reports" / "prompt_inventory.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nReporte JSON generado en: {output_path.relative_to(REPO_ROOT)}")
    return report


if __name__ == "__main__":
    run_audit()