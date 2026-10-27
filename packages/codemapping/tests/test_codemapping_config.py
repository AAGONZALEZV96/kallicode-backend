"""Tests de config.py: valida los 4 criterios de aceptación de KC-CM-01-3."""

import ast
from pathlib import Path
from typing import Any

import pytest
from kallicode_codemapping.config import Settings, build_settings
from pydantic import ValidationError

VALID_KWARGS: dict[str, Any] = {
    "tenant_id": "org_demo",
    "repo_url": "https://github.com/demo/repo.git",
    "database_url": "postgresql://user:pass@localhost:5432/kallicode",
    "embeddings_url": "http://localhost:8080",
    "llm_base_url": "http://localhost:9001/v1",
    "llm_api_key": "fake-key-for-tests",
}


def test_falta_tenant_id_lanza_validation_error() -> None:
    """Criterio 1: instanciar sin tenant_id debe lanzar ValidationError."""
    kwargs = {k: v for k, v in VALID_KWARGS.items() if k != "tenant_id"}
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_build_settings_con_todos_los_campos_no_falla() -> None:
    """Confirma que con todos los campos obligatorios, no lanza error."""
    settings = build_settings(**VALID_KWARGS)
    assert settings.tenant_id == "org_demo"


def test_no_hay_settings_a_nivel_de_modulo_ni_getenv_con_default() -> None:
    """Criterio 2: recorre el AST de config.py y falla si aparece
    un os.getenv con valor por defecto, o una instancia de Settings
    a nivel de módulo (fuera de una función/clase)."""
    ruta = Path(__file__).parent.parent / "src" / "kallicode_codemapping" / "config.py"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            es_getenv = isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "getenv"
            if es_getenv and len(nodo.args) >= 2:
                pytest.fail(f"os.getenv con valor por defecto encontrado en línea {nodo.lineno}")

    for nodo in arbol.body:
        if isinstance(nodo, ast.Assign) and isinstance(nodo.value, ast.Call):
            nombre_funcion = getattr(nodo.value.func, "id", None)
            if nombre_funcion == "Settings":
                pytest.fail(f"Instancia de Settings a nivel de módulo en línea {nodo.lineno}")


def test_dos_tenants_no_comparten_estado() -> None:
    """Criterio 3: dos invocaciones con tenants distintos en el mismo
    proceso no deben compartir la misma instancia ni el mismo valor."""
    kwargs_a = {**VALID_KWARGS, "tenant_id": "tenant_a"}
    kwargs_b = {**VALID_KWARGS, "tenant_id": "tenant_b"}

    settings_a = build_settings(**kwargs_a)
    settings_b = build_settings(**kwargs_b)

    assert settings_a.tenant_id == "tenant_a"
    assert settings_b.tenant_id == "tenant_b"
    assert settings_a is not settings_b


def test_database_url_y_embeddings_url_validadas_como_url() -> None:
    """Criterio 4a: database_url y embeddings_url deben validarse como URL."""
    kwargs = {**VALID_KWARGS, "database_url": "no-es-una-url"}
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_neo4j_y_qdrant_eliminados_del_modelo() -> None:
    """Criterio 4b: los campos de Neo4j y Qdrant ya no existen en el modelo."""
    campos = Settings.model_fields.keys()
    for campo_prohibido in ("neo4j_uri", "neo4j_user", "neo4j_password", "qdrant_url"):
        assert campo_prohibido not in campos


def test_todo_campo_de_settings_que_lee_el_codigo_existe_en_settings() -> None:
    """Red de seguridad (KC-CM-04-3): si alguien quita de Settings un campo que el codigo sigue
    leyendo (settings.X o cfg.X), la fase que lo usa falla en tiempo de ejecucion con AttributeError
    y ningun test unitario lo avisa. Esta prueba lo detecta al instante."""
    src = Path(__file__).parents[1] / "src" / "kallicode_codemapping"
    propiedades = {n for n, v in vars(Settings).items() if isinstance(v, property)}
    disponibles = set(Settings.model_fields) | propiedades | {"model_dump", "model_copy"}
    faltantes: dict[str, list[str]] = {}
    usados_en_total = 0
    for archivo in sorted(src.glob("*.py")):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        usados = {
            n.attr
            for n in ast.walk(arbol)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id in {"settings", "cfg"}
        }
        usados_en_total += len(usados)
        if usados - disponibles:
            faltantes[archivo.name] = sorted(usados - disponibles)
    assert usados_en_total > 0, "la prueba no encontro ningun uso de settings: revisa el patron"
    assert not faltantes, f"campos que el codigo lee y Settings no tiene: {faltantes}"


def test_campos_de_la_base_del_cliente_tienen_valores_por_defecto_y_limites() -> None:
    """KC-CM-04-3 (CM-F13): pool acotado, timeout por sentencia y esquemas excluidos."""
    s = build_settings(**VALID_KWARGS)
    assert s.client_db_max_connections == 2
    assert s.client_db_statement_timeout_ms == 30_000
    assert s.client_db_exclude_schemas == ["information_schema", "pg_catalog", "pg_toast"]
    for invalido in (
        {"client_db_max_connections": 0},
        {"client_db_max_connections": 11},
        {"client_db_statement_timeout_ms": 50},
    ):
        with pytest.raises(ValidationError):
            Settings(**{**VALID_KWARGS, **invalido})
