"""Resolución de llamadas homónimas contra PostgreSQL real (KC-CM-12-4).

Lo que mide test_calidad_semantica, aquí caso a caso: entre símbolos con el mismo nombre gana el
del mismo fichero, después el de un fichero importado, y solo sin ninguno de los dos se enlazan todos,
como antes. TypeScript, TSX y JavaScript forman una familia; Python y TypeScript no se mezclan.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.ingest_repo import SourceFile
from kallicode_codemapping.parser_treesitter import parse_file

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
TENANT = "org_test_resolucion_llamadas"


def _base_lista() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            return bool(conn.execute("SELECT to_regclass('cm.edges')").fetchone()[0])  # type: ignore[index]
    except psycopg.Error:
        return False


pytestmark = [
    pytest.mark.integracion,
    pytest.mark.skipif(not _base_lista(), reason="PostgreSQL no disponible (make up && make db)"),
]

FICHEROS = {
    # Dos `validar` en Python: cada llamador debe enlazar con el suyo.
    "fact/util.py": "def validar(x):\n    return x\n\n\ndef redondear(x):\n    return x\n",
    "cli/valid.py": (
        "def validar(x):\n    return x\n\n\ndef validar_cliente(c):\n    return validar(c)\n"
    ),
    "fact/iva.py": "from .util import validar\n\n\ndef calcular(b):\n    return validar(b)\n",
    # Import absoluto con una raíz que no aparece en el import (src/).
    "src/api/rutas.py": "from cli.valid import validar\n\n\ndef alta(c):\n    return validar(c)\n",
    # Sin import ni definición local: se enlazan los dos, como antes de KC-CM-12-4.
    "otros/suelto.py": "def usar(obj):\n    return obj.validar(1)\n",
    "cli/__init__.py": "",
    "src/cli/valid.py": "def otra():\n    return 1\n",
    # Familia JS: un .tsx que llama a un .ts; el redondear de Python no entra.
    "web/formato.ts": "export function redondear(v: number) { return v; }\n",
    "web/Vista.tsx": (
        'import { redondear } from "./formato";\n'
        "export function Vista(p: { v: number }) { return <b>{redondear(p.v)}</b>; }\n"
    ),
}
LENGUAJE = {".py": "python", ".ts": "typescript", ".tsx": "tsx"}


def _fuentes() -> list[SourceFile]:
    return [
        SourceFile(ruta, LENGUAJE["." + ruta.rsplit(".", 1)[1]], "x", contenido)
        for ruta, contenido in FICHEROS.items()
    ]


def _limpiar(conn: psycopg.Connection[object]) -> None:
    for tabla in ("cm.edges", "cm.nodes", "cm.symbols", "cm.files", "cm.repo_snapshots"):
        conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (TENANT,))
    conn.execute("DELETE FROM core.organizations WHERE id = %s", (TENANT,))


@pytest.fixture(scope="module")
def llamadas() -> Iterator[set[tuple[str, str]]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (TENANT, TENANT)
        )
        fuentes = _fuentes()
        with GraphLoader(dsn=ADMIN_DSN, tenant_id=TENANT) as loader:
            snap = loader.start_snapshot(commit_sha="sha_resolucion", rama="main")
            file_ids = loader.load_files(snap, fuentes)
            loader.load_symbols([s for f in fuentes for s in parse_file(f)], file_ids)
        filas = conn.execute(
            """SELECT fo.path || '::' || so.name, fd.path || '::' || sd.name
                 FROM cm.edges e
                 JOIN cm.symbols so ON so.tenant_id = e.tenant_id AND so.id = e.src_id
                 JOIN cm.files fo ON fo.tenant_id = e.tenant_id AND fo.id = so.file_id
                 JOIN cm.symbols sd ON sd.tenant_id = e.tenant_id AND sd.id = e.dst_id
                 JOIN cm.files fd ON fd.tenant_id = e.tenant_id AND fd.id = sd.file_id
                WHERE e.tenant_id = %s AND e.tipo = 'CALLS'""",
            (TENANT,),
        ).fetchall()
        try:
            yield {(str(a), str(b)) for a, b in filas}
        finally:
            _limpiar(conn)


def test_gana_el_homonimo_del_mismo_fichero(llamadas: set[tuple[str, str]]) -> None:
    salientes = {d for o, d in llamadas if o == "cli/valid.py::validar_cliente"}
    assert salientes == {"cli/valid.py::validar"}


def test_gana_el_homonimo_del_fichero_importado(llamadas: set[tuple[str, str]]) -> None:
    salientes = {d for o, d in llamadas if o == "fact/iva.py::calcular"}
    assert salientes == {"fact/util.py::validar"}


def test_el_import_absoluto_se_compara_como_sufijo_de_ruta(
    llamadas: set[tuple[str, str]],
) -> None:
    salientes = {d for o, d in llamadas if o == "src/api/rutas.py::alta"}
    assert salientes == {"cli/valid.py::validar"}


def test_sin_informacion_se_enlazan_todos_los_homonimos(llamadas: set[tuple[str, str]]) -> None:
    salientes = {d for o, d in llamadas if o == "otros/suelto.py::usar"}
    assert salientes == {"fact/util.py::validar", "cli/valid.py::validar"}


def test_tsx_resuelve_contra_ts_y_no_contra_python(llamadas: set[tuple[str, str]]) -> None:
    salientes = {d for o, d in llamadas if o == "web/Vista.tsx::Vista"}
    assert salientes == {"web/formato.ts::redondear"}
