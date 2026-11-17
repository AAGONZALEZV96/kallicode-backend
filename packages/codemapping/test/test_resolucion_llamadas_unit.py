"""Rutas importadas y familias de lenguaje para desambiguar llamadas (KC-CM-12-4)."""

from __future__ import annotations

from kallicode_codemapping.resolucion_llamadas import FAMILIA_JS, familia, rutas_importadas


def test_familia_une_typescript_tsx_y_javascript() -> None:
    assert familia("typescript") == familia("tsx") == familia("javascript") == FAMILIA_JS
    assert familia("python") == "python"
    assert familia("java") == "java"


def test_python_relativo_un_punto_es_el_paquete_del_fichero() -> None:
    rutas = rutas_importadas(
        "facturacion/servicio.py", "python", ["from .utilidades import redondear, validar"]
    )
    assert "facturacion/utilidades.py" in rutas
    assert "facturacion/utilidades/__init__.py" in rutas
    # Los nombres importados también pueden ser módulos (`from .paquete import modulo`).
    assert "facturacion/utilidades/redondear.py" in rutas


def test_python_relativo_dos_puntos_sube_un_nivel() -> None:
    rutas = rutas_importadas("app/api/rutas.py", "python", ["from ..servicios import pagos"])
    assert "app/servicios.py" in rutas
    assert "app/servicios/pagos.py" in rutas


def test_python_from_punto_import_modulo() -> None:
    rutas = rutas_importadas("informes/morosidad.py", "python", ["from . import exportar"])
    assert "informes/exportar.py" in rutas


def test_python_absoluto_con_alias_y_parentesis() -> None:
    rutas = rutas_importadas(
        "api/rutas.py",
        "python",
        [
            "import clientes.validaciones as v",
            "from facturacion.pagos import (\n    registrar_pago,\n    saldo_pendiente as saldo,\n)",
            "from x import *",
        ],
    )
    assert {"clientes/validaciones.py", "facturacion/pagos.py", "x.py"} <= rutas
    assert "facturacion/pagos/saldo_pendiente.py" in rutas
    assert not any("*" in r or " as " in r for r in rutas)


def test_js_relativo_con_y_sin_extension() -> None:
    rutas = rutas_importadas(
        "portal/vistas/Pantalla.tsx",
        "tsx",
        [
            'import { enviarPago } from "../formularios";',
            "import api from './api.ts';",
            'const x = require("./legado");',
        ],
    )
    assert {"portal/formularios.ts", "portal/formularios/index.ts", "portal/vistas/api.ts"} <= rutas
    assert "portal/vistas/legado.js" in rutas


def test_js_paquetes_y_rutas_fuera_del_repo_no_se_resuelven() -> None:
    rutas = rutas_importadas(
        "src/a.ts", "typescript", ['import React from "react";', 'import x from "../../fuera";']
    )
    assert rutas == set()


def test_java_import_de_clase_y_estatico() -> None:
    rutas = rutas_importadas(
        "src/main/java/App.java",
        "java",
        [
            "import com.acme.billing.InvoiceService;",
            "import static com.acme.util.Numeros.redondear;",
        ],
    )
    assert "com/acme/billing/InvoiceService.java" in rutas
    assert "com/acme/util/Numeros.java" in rutas


def test_lenguaje_sin_resolucion_de_imports_no_aporta_rutas() -> None:
    assert rutas_importadas("main.go", "go", ['import "fmt"']) == set()
