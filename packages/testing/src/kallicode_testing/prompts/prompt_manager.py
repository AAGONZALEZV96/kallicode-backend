"""
Ticket: KC-QA-B3-08
Gestor de Plantillas de Prompts Versionadas (prompt_manager.py).
Carga, valida y renderiza de forma segura (Jinja2 SandboxedEnvironment + resolve_safe_path)
las plantillas de generación de pruebas para Pytest, Playwright y Maestro,
cerrando el hallazgo QA-F30 (desacoplamiento de prompts hardcodeados).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field

from kallicode_testing.utils.path_utils import resolve_safe_path

DEFAULT_PROMPTS_DIR: Path = Path(__file__).resolve().parent


class PromptTemplateDTO(BaseModel):
    """DTO inmutable que representa una plantilla de prompt versionada."""

    model_config = ConfigDict(frozen=True)

    template_name: str
    version: str
    system_prompt: str
    user_prompt_template: str
    required_vars: list[str] = Field(default_factory=list)


def _parse_template_sections(raw_content: str) -> tuple[list[str], str, str]:
    """
    Extrae las secciones [REQUIRED_VARS], [SYSTEM_PROMPT] y [USER_PROMPT]
    de un archivo de plantilla .jinja2.
    """
    req_marker = "[REQUIRED_VARS]"
    sys_marker = "[SYSTEM_PROMPT]"
    usr_marker = "[USER_PROMPT]"

    if sys_marker not in raw_content or usr_marker not in raw_content:
        raise ValueError(
            "Formato de plantilla inválido: faltan marcadores [SYSTEM_PROMPT] o [USER_PROMPT]."
        )

    required_vars: list[str] = []
    if req_marker in raw_content:
        after_req = raw_content.split(req_marker, 1)[1]
        req_block = after_req.split(sys_marker, 1)[0].strip()
        required_vars = [v.strip() for v in req_block.split(",") if v.strip()]

    after_sys = raw_content.split(sys_marker, 1)[1]
    system_part, user_part = after_sys.split(usr_marker, 1)
    return required_vars, system_part.strip(), user_part.strip()


class PromptManager:
    """
    Gestor centralizado de plantillas de prompts versionadas para generación de pruebas.
    Garantiza confinamiento de rutas mediante resolve_safe_path y validación estricta
    de variables obligatorias antes del renderizado Jinja2 en entorno aislado (Sandbox).
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self.prompts_dir: Path = (
            prompts_dir.resolve() if prompts_dir is not None else DEFAULT_PROMPTS_DIR
        )
        self._env = SandboxedEnvironment(
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def get_template(self, name: str, version: str = "v1") -> PromptTemplateDTO:
        """
        Carga una plantilla versionada desde prompts_dir validando confinamiento
        mediante resolve_safe_path para impedir ataques de Path Traversal.
        """
        clean_name = name.strip() if name else ""
        clean_version = version.strip() if version else ""
        if not clean_name or not clean_version:
            raise ValueError("El nombre y la versión de la plantilla no pueden estar vacíos.")

        # Validar cada segmento mediante resolve_safe_path para bloquear '../' o rutas absolutas
        resolve_safe_path(self.prompts_dir, clean_name)
        resolve_safe_path(self.prompts_dir, clean_version)
        if "/" in clean_name or "\\" in clean_name or "/" in clean_version or "\\" in clean_version:
            raise ValueError(
                f"Intento de Path-Traversal detectado en nombre o versión: {clean_name}_{clean_version}"
            )

        filename = f"{clean_name}_{clean_version}.jinja2"
        safe_path = resolve_safe_path(self.prompts_dir, filename)
        if not safe_path.is_file():
            raise FileNotFoundError(f"Plantilla de prompt no encontrada: {safe_path}")

        raw_content = safe_path.read_text(encoding="utf-8")
        required_vars, system_prompt, user_prompt_template = _parse_template_sections(raw_content)

        return PromptTemplateDTO(
            template_name=clean_name,
            version=clean_version,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            required_vars=required_vars,
        )

    def render_prompt(
        self,
        name: str,
        variables: dict[str, Any],
        version: str = "v1",
    ) -> tuple[str, str]:
        """
        Valida la presencia de todas las variables en required_vars y renderiza
        (system_prompt_rendered, user_prompt_rendered) usando Jinja2 SandboxedEnvironment.
        """
        dto = self.get_template(name=name, version=version)

        missing_vars: list[str] = []
        for req_var in dto.required_vars:
            val = variables.get(req_var)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing_vars.append(req_var)

        if missing_vars:
            missing_list = ", ".join(missing_vars)
            raise ValueError(
                f"Variables obligatorias faltantes para la plantilla "
                f"'{dto.template_name}_{dto.version}': [{missing_list}]"
            )

        sys_tmpl = self._env.from_string(dto.system_prompt)
        usr_tmpl = self._env.from_string(dto.user_prompt_template)

        system_rendered = sys_tmpl.render(**variables).strip()
        user_rendered = usr_tmpl.render(**variables).strip()
        return system_rendered, user_rendered

    def list_available_templates(self) -> list[dict[str, str]]:
        """Lista todas las plantillas y versiones disponibles dentro de prompts_dir."""
        if not self.prompts_dir.exists() or not self.prompts_dir.is_dir():
            return []

        available: list[dict[str, str]] = []
        for item in sorted(self.prompts_dir.glob("*.jinja2")):
            safe_item = resolve_safe_path(self.prompts_dir, item.name)
            stem = safe_item.stem
            if "_" not in stem:
                continue
            template_name, version = stem.rsplit("_", 1)
            available.append(
                {
                    "template_name": template_name,
                    "version": version,
                }
            )
        return available
