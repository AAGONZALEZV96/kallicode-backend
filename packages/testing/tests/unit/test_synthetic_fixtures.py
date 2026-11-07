"""Pruebas del generador de fixtures sintéticos (KC-QA-B3-06)."""

import json
from pathlib import Path

import pytest
from kallicode_testing.synthetic_fixtures import (
    LARGO_EXTREMO,
    CasoBorde,
    casos_borde,
    datos_validos,
    escribir_fixtures,
)
from pydantic import BaseModel, Field, ValidationError


class TicketDemo(BaseModel):
    """Réplica de ``TicketIn`` (apps/api/routers/tickets.py) con sus mismas reglas."""

    tipo: str = Field(pattern=r"^(bug|mejora)$")
    titulo: str = Field(min_length=5, max_length=200)
    descripcion: str = Field(min_length=10, max_length=10_000)
    prioridad: str = Field(default="media", pattern=r"^(alta|media|baja)$")
    modulo: str | None = Field(default=None, max_length=200)


class NotaLibre(BaseModel):
    """Modelo con un texto sin límite de largo y un número con mínimo."""

    texto: str
    puntaje: int = Field(ge=1, le=5)


class Contenedor(BaseModel):
    """Modelo anidado: un ticket dentro de otro modelo."""

    ticket: TicketDemo


def test_datos_validos_son_aceptados_por_el_modelo() -> None:
    """El ejemplo válido pasa la validación de Pydantic sin errores."""
    datos = datos_validos(TicketDemo)
    TicketDemo.model_validate(datos)
    assert datos["tipo"] == "bug"
    assert len(datos["titulo"]) == 5


def test_datos_validos_respetan_minimos_y_modelos_anidados() -> None:
    """Respeta ge=1 en números y genera también el modelo anidado."""
    NotaLibre.model_validate(datos_validos(NotaLibre))
    Contenedor.model_validate(datos_validos(Contenedor))


@pytest.mark.parametrize(
    "caso", casos_borde(TicketDemo), ids=lambda c: f"{c['campo']}-{c['descripcion']}"
)
def test_cada_caso_de_borde_cumple_su_expectativa(caso: CasoBorde) -> None:
    """Cada caso se acepta o rechaza exactamente como indica esperado_valido."""
    if caso["esperado_valido"]:
        TicketDemo.model_validate(caso["datos"])
    else:
        with pytest.raises(ValidationError):
            TicketDemo.model_validate(caso["datos"])


def test_casos_incluyen_largo_extremo_y_unicode() -> None:
    """Cubre el criterio de aceptación: strings extremadamente largos y Unicode."""
    casos = casos_borde(NotaLibre)
    extremo = next(c for c in casos if "extremo" in c["descripcion"])
    unicode = next(c for c in casos if "Unicode" in c["descripcion"])

    assert len(extremo["datos"]["texto"]) == LARGO_EXTREMO
    assert extremo["esperado_valido"] is True
    assert not unicode["datos"]["texto"].isascii()
    NotaLibre.model_validate(unicode["datos"])


def test_campos_con_pattern_no_generan_casos_de_texto() -> None:
    """tipo y prioridad tienen pattern: un texto Unicode los rompería por diseño."""
    campos = {c["campo"] for c in casos_borde(TicketDemo)}
    assert campos == {"titulo", "descripcion", "modulo"}


def test_escribir_fixtures_genera_json_local_legible(tmp_path: Path) -> None:
    """El JSON se escribe en disco y conserva los caracteres Unicode sin escapar."""
    ruta = escribir_fixtures(TicketDemo, tmp_path / "fixtures")
    texto = ruta.read_text(encoding="utf-8")
    contenido = json.loads(texto)

    assert ruta.name == "TicketDemo.json"
    assert contenido["modelo"] == "TicketDemo"
    assert len(contenido["validos"]) == 1
    assert len(contenido["borde"]) == len(casos_borde(TicketDemo))
    assert "漢字" in texto
