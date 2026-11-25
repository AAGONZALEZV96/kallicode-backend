"""Consultas SQL sobre el esquema cm (CodeMapping) y vec para el backend (KC-BE-10-6).

Todas las funciones operan dentro de una sesión PostgreSQL con RLS activo para
el tenant (`core.current_tenant()`) y parameters locales a la transacción
(`SET LOCAL` vía `vec.configurar_busqueda`), garantizando compatibilidad con
poolers en modo transacción como Supabase PgBouncer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import cm_impacto
from .cm_grafo_sesion import ConsultaGrafoSesion
from .cm_impacto import ConsultaGrafoIn, TipoArista
from .grafo import Impacto, Subgrafo
from .vectores import configurar_busqueda

_UI_PREFIJOS = ("ui/", "components/", "pages/", "views/", "frontend/")
_UI_SUBRUTAS = ("/ui/", "/components/", "/pages/", "/views/", "/frontend/")
_UI_EXTS = (".tsx", ".jsx", ".vue", ".svelte", ".html")
_UMBRAL_SIMILITUD_SEMILLA = 0.45


def _normalizar_modulo(valor: str) -> str:
    limpio = valor.strip().strip("/")
    return limpio


def _extraer_lista_json(raw: Any, clave: str) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, dict):
        return []
    valores = raw.get(clave)
    if not isinstance(valores, list):
        return []
    return [str(v).strip() for v in valores if isinstance(v, str) and v.strip()]


def _modulo_de_ruta(path: str) -> str:
    p = PurePosixPath(path.strip("/"))
    partes = p.parts
    if len(partes) <= 1:
        return p.stem or path
    if partes[0] == "src" and len(partes) >= 3:
        return "/".join(partes[:2])
    return partes[0]


async def _tiene_grafo(db: AsyncSession) -> bool:
    res = await db.execute(
        text(
            """
            SELECT 1
              FROM cm.nodes n
             WHERE n.tenant_id = core.current_tenant()
             LIMIT 1
            """
        )
    )
    return res.scalar() is not None


async def semillas_para_ticket(
    db: AsyncSession,
    ticket_id: str,
) -> tuple[list[str], list[str]]:
    """Resuelve (nodos, ficheros) semilla en `cm` para un ticket del tenant."""
    fila_ticket = (
        (
            await db.execute(
                text(
                    """
                SELECT id, numero, titulo, descripcion, modulo_pista, impacto, origen_ref
                  FROM core.tickets
                 WHERE tenant_id = core.current_tenant()
                   AND (numero = :tk OR id = :tk)
                 LIMIT 1
                """
                ),
                {"tk": ticket_id},
            )
        )
        .mappings()
        .first()
    )

    nodos: list[str] = []
    ficheros: list[str] = []
    modulos_pista: list[str] = []
    tablas_pista: list[str] = []
    tk_pk: str | None = None

    if fila_ticket is not None:
        tk_pk = str(fila_ticket["id"])
        pista = fila_ticket.get("modulo_pista")
        if isinstance(pista, str) and pista.strip():
            for trozo in re.split(r"[,;]+", pista):
                m = _normalizar_modulo(trozo)
                if m:
                    modulos_pista.append(m)

        impacto_raw = fila_ticket.get("impacto")
        modulos_pista.extend(
            _normalizar_modulo(m)
            for m in _extraer_lista_json(impacto_raw, "modulos")
            if _normalizar_modulo(m)
        )
        tablas_pista.extend(_extraer_lista_json(impacto_raw, "tablas"))

        origen_raw = fila_ticket.get("origen_ref")
        modulos_pista.extend(
            _normalizar_modulo(m)
            for m in _extraer_lista_json(origen_raw, "modulos")
            if _normalizar_modulo(m)
        )
        ficheros.extend(_extraer_lista_json(origen_raw, "ficheros"))
        ficheros.extend(_extraer_lista_json(origen_raw, "archivos"))
        nodos.extend(_extraer_lista_json(origen_raw, "nodos"))

    # Deduplicar preservando orden.
    modulos_pista = list(dict.fromkeys(modulos_pista))
    tablas_pista = list(dict.fromkeys(tablas_pista))

    # 1. Resolver módulos/rutas contra cm.files y cm.definitions.
    for mod in modulos_pista:
        filas_f = (
            (
                await db.execute(
                    text(
                        """
                    SELECT f.path
                      FROM cm.files f
                     WHERE f.tenant_id = core.current_tenant()
                       AND (f.path = :m OR starts_with(f.path, :m || '/'))
                     ORDER BY f.path COLLATE "C"
                     LIMIT 50
                    """
                    ),
                    {"m": mod},
                )
            )
            .scalars()
            .all()
        )
        ficheros.extend(str(p) for p in filas_f)

        filas_def = (
            (
                await db.execute(
                    text(
                        """
                    SELECT d.id
                      FROM cm.definitions d
                     WHERE d.tenant_id = core.current_tenant()
                       AND (d.modulo = :m OR starts_with(d.modulo, :m || '/'))
                     ORDER BY d.id COLLATE "C"
                     LIMIT 50
                    """
                    ),
                    {"m": mod},
                )
            )
            .scalars()
            .all()
        )
        nodos.extend(str(did) for did in filas_def)

    # 2. Resolver tablas mencionadas en impacto.tablas contra cm.db_tables.
    for tbl in tablas_pista:
        limpia = tbl.strip()
        if not limpia:
            continue
        if "." in limpia:
            esquema, nombre = limpia.split(".", 1)
            filas_t = (
                (
                    await db.execute(
                        text(
                            """
                        SELECT t.id
                          FROM cm.db_tables t
                         WHERE t.tenant_id = core.current_tenant()
                           AND t.esquema = :esq
                           AND t.nombre = :nom
                        """
                        ),
                        {"esq": esquema, "nom": nombre},
                    )
                )
                .scalars()
                .all()
            )
        else:
            filas_t = (
                (
                    await db.execute(
                        text(
                            """
                        SELECT t.id
                          FROM cm.db_tables t
                         WHERE t.tenant_id = core.current_tenant()
                           AND t.nombre = :nom
                        """
                        ),
                        {"nom": limpia},
                    )
                )
                .scalars()
                .all()
            )
        nodos.extend(str(tid) for tid in filas_t)

    # 3. Si el ticket tiene embedding guardado en vec.ticket_embeddings, buscar nodos cercanos.
    if tk_pk is not None:
        emb_row = (
            (
                await db.execute(
                    text(
                        """
                    SELECT embedding::text AS emb
                      FROM vec.ticket_embeddings
                     WHERE tenant_id = core.current_tenant()
                       AND ticket_id = :tk
                     LIMIT 1
                    """
                    ),
                    {"tk": tk_pk},
                )
            )
            .mappings()
            .first()
        )
        if emb_row and emb_row["emb"]:
            await configurar_busqueda(db, "diseno")
            vecinos = (
                (
                    await db.execute(
                        text(
                            """
                        WITH r AS MATERIALIZED (
                            SELECT d.nodo_ref, d.embedding <=> CAST(:emb AS vector) AS distancia
                              FROM vec.definition_embeddings d
                             WHERE d.tenant_id = core.current_tenant()
                             ORDER BY d.embedding <=> CAST(:emb AS vector)
                             LIMIT 10
                        )
                        SELECT nodo_ref, (1 - distancia) AS similitud
                          FROM r
                         WHERE (1 - distancia) >= :umbral
                         ORDER BY distancia + 0, nodo_ref COLLATE "C"
                        """
                        ),
                        {"emb": emb_row["emb"], "umbral": _UMBRAL_SIMILITUD_SEMILLA},
                    )
                )
                .mappings()
                .all()
            )
            nodos.extend(str(v["nodo_ref"]) for v in vecinos)

    # 4. Fallback si el ticket existe pero no tiene pista ni embedding: si ticket_id parece
    # directamente un nodo_ref o ruta de archivo, probarlo; de lo contrario tomar nodos raíz
    # recientes del grafo del tenant si existen pistas en título/descripción.
    if not nodos and not ficheros:
        directo_nodo = (
            await db.execute(
                text(
                    """
                    SELECT id
                      FROM cm.nodes
                     WHERE tenant_id = core.current_tenant()
                       AND id = :tk
                     LIMIT 1
                    """
                ),
                {"tk": ticket_id},
            )
        ).scalar()
        if directo_nodo:
            nodos.append(str(directo_nodo))
        else:
            directo_file = (
                await db.execute(
                    text(
                        """
                        SELECT path
                          FROM cm.files
                         WHERE tenant_id = core.current_tenant()
                           AND path = :tk
                         LIMIT 1
                        """
                    ),
                    {"tk": ticket_id},
                )
            ).scalar()
            if directo_file:
                ficheros.append(str(directo_file))

    nodos_unicos = list(dict.fromkeys(nodos))[: cm_impacto.MAX_SEMILLAS]
    ficheros_unicos = list(dict.fromkeys(ficheros))[: cm_impacto.MAX_SEMILLAS]
    return nodos_unicos, ficheros_unicos


async def _enriquecer_nodos(
    db: AsyncSession,
    nodos_alcanzados: Sequence[cm_impacto.NodoAlcanzado],
) -> tuple[dict[str, Any], ...]:
    if not nodos_alcanzados:
        return ()

    ids = [n.id for n in nodos_alcanzados]
    detalles: dict[str, dict[str, Any]] = {}

    for sql, extractor in (
        (
            """
            SELECT id, path, language
              FROM cm.files
             WHERE tenant_id = core.current_tenant()
               AND id = ANY(CAST(:ids AS text[]))
            """,
            lambda r: {"path": r["path"], "language": r["language"], "label": r["path"]},
        ),
        (
            """
            SELECT s.id, s.name, s.kind AS symbol_kind, s.role, f.path
              FROM cm.symbols s
              JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
             WHERE s.tenant_id = core.current_tenant()
               AND s.id = ANY(CAST(:ids AS text[]))
            """,
            lambda r: {
                "name": r["name"],
                "symbol_kind": r["symbol_kind"],
                "role": r["role"],
                "path": r["path"],
                "label": f"{r['path']}::{r['name']}",
            },
        ),
        (
            """
            SELECT id, esquema, nombre
              FROM cm.db_tables
             WHERE tenant_id = core.current_tenant()
               AND id = ANY(CAST(:ids AS text[]))
            """,
            lambda r: {
                "esquema": r["esquema"],
                "nombre": r["nombre"],
                "label": f"{r['esquema']}.{r['nombre']}",
            },
        ),
        (
            """
            SELECT c.id, c.nombre, c.tipo, t.esquema, t.nombre AS tabla
              FROM cm.db_columns c
              JOIN cm.db_tables t ON t.tenant_id = c.tenant_id AND t.id = c.table_id
             WHERE c.tenant_id = core.current_tenant()
               AND c.id = ANY(CAST(:ids AS text[]))
            """,
            lambda r: {
                "nombre": r["nombre"],
                "tipo": r["tipo"],
                "tabla": f"{r['esquema']}.{r['tabla']}",
                "label": f"{r['esquema']}.{r['tabla']}.{r['nombre']}",
            },
        ),
        (
            """
            SELECT id, modulo, nombre, kind AS def_kind
              FROM cm.definitions
             WHERE tenant_id = core.current_tenant()
               AND id = ANY(CAST(:ids AS text[]))
            """,
            lambda r: {
                "modulo": r["modulo"],
                "nombre": r["nombre"],
                "def_kind": r["def_kind"],
                "label": f"{r['modulo']}::{r['nombre']}",
            },
        ),
        (
            """
            SELECT id, name_en, name_local
              FROM cm.concepts
             WHERE tenant_id = core.current_tenant()
               AND id = ANY(CAST(:ids AS text[]))
            """,
            lambda r: {
                "name_en": r["name_en"],
                "name_local": r["name_local"],
                "label": r["name_local"] or r["name_en"],
            },
        ),
    ):
        filas = (await db.execute(text(sql), {"ids": ids})).mappings().all()
        for fila in filas:
            detalles[str(fila["id"])] = extractor(fila)

    salida: list[dict[str, Any]] = []
    for nodo in nodos_alcanzados:
        item: dict[str, Any] = {
            "id": nodo.id,
            "kind": nodo.kind,
            "distancia": nodo.distancia,
            "via_tipo": nodo.via_tipo.value if nodo.via_tipo is not None else None,
        }
        item.update(detalles.get(nodo.id, {}))
        salida.append(item)
    return tuple(salida)


async def consultar_subgrafo_ticket(
    db: AsyncSession,
    ticket_id: str,
    *,
    k: int = 2,
) -> Subgrafo:
    """Construye el `Subgrafo` del contrato `GrafoCliente` para un ticket."""
    if not await _tiene_grafo(db):
        return Subgrafo()

    nodos_semilla, ficheros_semilla = await semillas_para_ticket(db, ticket_id)
    if not nodos_semilla and not ficheros_semilla:
        return Subgrafo()

    consulta = ConsultaGrafoSesion(db, tiempo_limitado=True)
    sub = await consulta.subgrafo(
        ConsultaGrafoIn(
            nodos=nodos_semilla,
            ficheros=ficheros_semilla,
            k=min(max(k, 1), cm_impacto.K_MAX),
        )
    )
    if not sub.nodos and not sub.aristas:
        return Subgrafo()

    semillas_set = set(sub.semillas)
    nodos_enriquecidos = await _enriquecer_nodos(db, sub.nodos)
    aristas_dict = tuple(
        {
            "tipo": a.tipo.value,
            "src_kind": a.src_kind,
            "src_id": a.src_id,
            "dst_kind": a.dst_kind,
            "dst_id": a.dst_id,
            "direction": (
                "inbound"
                if a.dst_id in semillas_set and a.src_id not in semillas_set
                else "outbound"
            ),
        }
        for a in sub.aristas
    )
    return Subgrafo(nodos=nodos_enriquecidos, aristas=aristas_dict)


async def _resolver_semillas_nodo_ref(
    db: AsyncSession,
    nodo_ref: str,
) -> tuple[list[str], list[str]]:
    """Resuelve `nodo_ref` como ID de nodo, ruta de archivo o nombre de tabla."""
    ref = nodo_ref.strip()
    if not ref:
        return [], []

    existe_nodo = (
        await db.execute(
            text(
                """
                SELECT id
                  FROM cm.nodes
                 WHERE tenant_id = core.current_tenant()
                   AND id = :ref
                 LIMIT 1
                """
            ),
            {"ref": ref},
        )
    ).scalar()
    if existe_nodo:
        return [str(existe_nodo)], []

    existe_file = (
        await db.execute(
            text(
                """
                SELECT path
                  FROM cm.files
                 WHERE tenant_id = core.current_tenant()
                   AND path = :ref
                 LIMIT 1
                """
            ),
            {"ref": ref},
        )
    ).scalar()
    if existe_file:
        return [], [str(existe_file)]

    # Intentar resolver por nombre de tabla (ej. 'invoices' o 'public.invoices').
    if "." in ref:
        esq, nom = ref.split(".", 1)
        tabla_id = (
            await db.execute(
                text(
                    """
                    SELECT id
                      FROM cm.db_tables
                     WHERE tenant_id = core.current_tenant()
                       AND esquema = :esq
                       AND nombre = :nom
                     LIMIT 1
                    """
                ),
                {"esq": esq, "nom": nom},
            )
        ).scalar()
    else:
        tabla_id = (
            await db.execute(
                text(
                    """
                    SELECT id
                      FROM cm.db_tables
                     WHERE tenant_id = core.current_tenant()
                       AND nombre = :nom
                     LIMIT 1
                    """
                ),
                {"nom": ref},
            )
        ).scalar()
    if tabla_id:
        return [str(tabla_id)], []

    return [], []


async def consultar_impacto(
    db: AsyncSession,
    nodo_ref: str,
    *,
    k: int = 2,
) -> Impacto:
    """Calcula el `Impacto` a `k` saltos al modificar `nodo_ref`."""
    if not await _tiene_grafo(db):
        return Impacto()

    nodos_in, ficheros_in = await _resolver_semillas_nodo_ref(db, nodo_ref)
    if not nodos_in and not ficheros_in:
        return Impacto()

    k_acotado = min(max(k, 1), cm_impacto.K_MAX)
    consulta = ConsultaGrafoSesion(db, tiempo_limitado=True)
    recorrido = await consulta.impacto(
        ConsultaGrafoIn(nodos=nodos_in, ficheros=ficheros_in, k=k_acotado)
    )
    if not recorrido.nodos:
        return Impacto()

    ids_afectados = [n.id for n in recorrido.nodos]
    radio = max((n.distancia for n in recorrido.nodos), default=0)

    # Módulos afectados a partir de archivos y definiciones alcanzados.
    modulos_set: set[str] = set()
    rutas_archivos = (
        (
            await db.execute(
                text(
                    """
                SELECT DISTINCT f.path
                  FROM cm.files f
                 WHERE f.tenant_id = core.current_tenant()
                   AND (
                       f.id = ANY(CAST(:ids AS text[]))
                       OR f.id IN (
                           SELECT s.file_id
                             FROM cm.symbols s
                            WHERE s.tenant_id = core.current_tenant()
                              AND s.id = ANY(CAST(:ids AS text[]))
                       )
                   )
                """
                ),
                {"ids": ids_afectados},
            )
        )
        .scalars()
        .all()
    )
    for ruta in rutas_archivos:
        modulos_set.add(_modulo_de_ruta(str(ruta)))

    mods_def = (
        (
            await db.execute(
                text(
                    """
                SELECT DISTINCT d.modulo
                  FROM cm.definitions d
                 WHERE d.tenant_id = core.current_tenant()
                   AND d.id = ANY(CAST(:ids AS text[]))
                """
                ),
                {"ids": ids_afectados},
            )
        )
        .scalars()
        .all()
    )
    for mod in mods_def:
        if mod:
            modulos_set.add(str(mod))

    # Tablas afectadas: las incluidas directamente en el recorrido más las que usan
    # los nodos afectados vía USES_TABLE / REFERENCES / HAS_COLUMN.
    filas_tablas = (
        (
            await db.execute(
                text(
                    """
                SELECT DISTINCT t.esquema, t.nombre
                  FROM cm.db_tables t
                 WHERE t.tenant_id = core.current_tenant()
                   AND (
                       t.id = ANY(CAST(:ids AS text[]))
                       OR t.id IN (
                           SELECT e.dst_id
                             FROM cm.edges e
                            WHERE e.tenant_id = core.current_tenant()
                              AND e.src_id = ANY(CAST(:ids AS text[]))
                              AND e.dst_kind = 'db_table'
                              AND e.tipo IN ('USES_TABLE', 'REFERENCES')
                       )
                       OR t.id IN (
                           SELECT c.table_id
                             FROM cm.db_columns c
                            WHERE c.tenant_id = core.current_tenant()
                              AND c.id = ANY(CAST(:ids AS text[]))
                       )
                   )
                 ORDER BY t.esquema COLLATE "C", t.nombre COLLATE "C"
                """
                ),
                {"ids": ids_afectados},
            )
        )
        .mappings()
        .all()
    )
    tablas = tuple(
        f"{r['esquema']}.{r['nombre']}" if r["esquema"] != "public" else str(r["nombre"])
        for r in filas_tablas
    )
    modulos = tuple(sorted(modulos_set))

    if recorrido.truncado or radio >= 3 or len(ids_afectados) >= 10 or len(tablas) >= 3:
        riesgo = "alto"
    elif radio >= 2 or len(ids_afectados) >= 4 or len(tablas) >= 1:
        riesgo = "medio"
    else:
        riesgo = "bajo"

    return Impacto(
        radio=radio,
        riesgo=riesgo,
        modulos=modulos,
        tablas=tablas,
    )


async def consultar_inventario_ui(db: AsyncSession) -> list[dict[str, Any]]:
    """Lista los archivos y componentes UI del tenant en el esquema `cm`."""
    if not await _tiene_grafo(db):
        return []

    filas_files = (
        (
            await db.execute(
                text(
                    """
                SELECT f.id, f.path, f.language
                  FROM cm.files f
                 WHERE f.tenant_id = core.current_tenant()
                 ORDER BY f.path COLLATE "C"
                """
                )
            )
        )
        .mappings()
        .all()
    )

    ui_files: list[dict[str, Any]] = []
    ui_file_ids: list[str] = []
    for f in filas_files:
        path = str(f["path"])
        lower = path.lower()
        es_ui = (
            lower.startswith(_UI_PREFIJOS)
            or any(seg in lower for seg in _UI_SUBRUTAS)
            or lower.endswith(_UI_EXTS)
        )
        if es_ui:
            ui_file_ids.append(str(f["id"]))
            ui_files.append(
                {
                    "id": str(f["id"]),
                    "kind": "file",
                    "nombre": PurePosixPath(path).name,
                    "path": path,
                    "modulo": _modulo_de_ruta(path),
                    "language": str(f["language"]),
                }
            )

    if not ui_file_ids:
        return []

    filas_symbols = (
        (
            await db.execute(
                text(
                    """
                SELECT s.id, s.name, s.kind AS symbol_kind, s.start_line, f.path
                  FROM cm.symbols s
                  JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
                 WHERE s.tenant_id = core.current_tenant()
                   AND s.file_id = ANY(CAST(:fids AS text[]))
                 ORDER BY f.path COLLATE "C", s.start_line, s.id COLLATE "C"
                """
                ),
                {"fids": ui_file_ids},
            )
        )
        .mappings()
        .all()
    )

    inventario: list[dict[str, Any]] = list(ui_files)
    for s in filas_symbols:
        path = str(s["path"])
        inventario.append(
            {
                "id": str(s["id"]),
                "kind": "symbol",
                "symbol_kind": str(s["symbol_kind"]),
                "nombre": str(s["name"]),
                "path": path,
                "modulo": _modulo_de_ruta(path),
                "start_line": int(s["start_line"]),
            }
        )
    return inventario


async def estimar_tablas_feature(
    db: AsyncSession,
    *,
    modulos_estimados: Sequence[str],
    embedding: Sequence[float] | None = None,
    k_saltos: int = 3,
) -> list[str]:
    """Deriva las tablas de BD (`cm.db_tables`) afectadas por una nueva feature.

    Combina:
    1. Expansión estructural desde `modulos_estimados` (`cm.files` + `cm.definitions`)
       a través de `cm.edges` (`USES_TABLE`, `REFERENCES`, `HAS_COLUMN`, `REALIZED_BY`, `CALLS`)
       con cláusula `CYCLE` y límite de profundidad.
    2. Búsqueda vectorial sobre `vec.definition_embeddings` con `vec.configurar_busqueda('diseno')`
       (`SET LOCAL hnsw.ef_search`) cuando se dispone de `embedding`.

    Si el tenant aún no tiene grafo ingestado o no hay tablas asociadas, devuelve `[]`.
    """
    if not await _tiene_grafo(db):
        return []

    semillas: list[str] = []
    for raw in modulos_estimados:
        mod = _normalizar_modulo(raw)
        if not mod:
            continue
        filas_f = (
            (
                await db.execute(
                    text(
                        """
                    SELECT f.id
                      FROM cm.files f
                     WHERE f.tenant_id = core.current_tenant()
                       AND (f.path = :m OR starts_with(f.path, :m || '/'))
                    UNION
                    SELECT s.id
                      FROM cm.symbols s
                      JOIN cm.files f ON f.tenant_id = s.tenant_id AND f.id = s.file_id
                     WHERE s.tenant_id = core.current_tenant()
                       AND f.tenant_id = core.current_tenant()
                       AND (f.path = :m OR starts_with(f.path, :m || '/'))
                    UNION
                    SELECT d.id
                      FROM cm.definitions d
                     WHERE d.tenant_id = core.current_tenant()
                       AND (d.modulo = :m OR starts_with(d.modulo, :m || '/'))
                    """
                    ),
                    {"m": mod},
                )
            )
            .scalars()
            .all()
        )
        semillas.extend(str(nid) for nid in filas_f)

    if embedding:
        await configurar_busqueda(db, "diseno")
        vecinos = (
            (
                await db.execute(
                    text(
                        """
                    WITH r AS MATERIALIZED (
                        SELECT d.nodo_ref, d.embedding <=> CAST(:emb AS vector) AS distancia
                          FROM vec.definition_embeddings d
                         WHERE d.tenant_id = core.current_tenant()
                         ORDER BY d.embedding <=> CAST(:emb AS vector)
                         LIMIT 10
                    )
                    SELECT nodo_ref
                      FROM r
                     WHERE (1 - distancia) >= :umbral
                     ORDER BY distancia + 0, nodo_ref COLLATE "C"
                    """
                    ),
                    {"emb": str(list(embedding)), "umbral": _UMBRAL_SIMILITUD_SEMILLA},
                )
            )
            .scalars()
            .all()
        )
        semillas.extend(str(nid) for nid in vecinos)

    semillas_unicas = list(dict.fromkeys(semillas))[: cm_impacto.MAX_SEMILLAS]
    if not semillas_unicas:
        return []

    tipos_recorrido = [
        TipoArista.CALLS.value,
        TipoArista.USES_TABLE.value,
        TipoArista.REFERENCES.value,
        TipoArista.HAS_COLUMN.value,
        TipoArista.REALIZED_BY.value,
    ]
    filas_tablas = (
        (
            await db.execute(
                text(
                    """
                WITH RECURSIVE recorrido(id, profundidad) AS (
                    SELECT s.id, 0
                      FROM cm.nodes s
                     WHERE s.tenant_id = core.current_tenant()
                       AND s.id = ANY(CAST(:semillas AS text[]))
                    UNION ALL
                    SELECT e.dst_id, r.profundidad + 1
                      FROM recorrido r
                      JOIN cm.edges e
                        ON e.tenant_id = core.current_tenant()
                       AND e.src_id = r.id
                       AND e.tipo = ANY(CAST(:tipos AS text[]))
                     WHERE r.profundidad < :k
                )
                CYCLE id SET es_ciclo USING ruta
                SELECT DISTINCT t.esquema, t.nombre
                  FROM recorrido rec
                  JOIN cm.db_tables t
                    ON t.tenant_id = core.current_tenant()
                   AND t.id = rec.id
                 WHERE NOT rec.es_ciclo
                 ORDER BY t.esquema COLLATE "C", t.nombre COLLATE "C"
                """
                ),
                {
                    "semillas": semillas_unicas,
                    "tipos": tipos_recorrido,
                    "k": min(max(k_saltos, 1), cm_impacto.K_MAX),
                },
            )
        )
        .mappings()
        .all()
    )

    return [
        f"{r['esquema']}.{r['nombre']}" if r["esquema"] != "public" else str(r["nombre"])
        for r in filas_tablas
    ]
