#!/usr/bin/env python3
"""Instantánea congelada del catálogo del esquema v2 (KC-DB-12-1).

Extrae de pg_catalog una forma canónica (ordenada, sin OIDs ni fechas) del esquema que las migraciones
construyen y la compara con la copia committeada en ``db/esquema_v2.snapshot.json``. La prueba
``packages/core/tests/integracion/test_esquema_congelado.py`` falla ante cualquier divergencia que no venga
de una migración declarada (docs/politica-cambios-esquema.md).

Qué entra (esquemas core, vec, audit, cm y los objetos propios de public):
  esquemas (dueño y ACL), tablas y particionadas padre (dueño, RLS/FORCE, clave de partición, reloptions, ACL),
  columnas (tipo, NOT NULL, default, identity, generada, collation, estadísticas), constraints, índices, vistas,
  secuencias, funciones (firma, retorno, lenguaje, volatilidad, SECURITY DEFINER, proconfig, hash del cuerpo,
  ACL), triggers, políticas RLS, tipos (enum, dominio, compuesto), privilegios por defecto y event triggers.

Qué NO entra, a propósito:
  * Particiones hijas y sus índices: pg_partman y la partición por tenant las crean con el tiempo y con los
    datos; congelarlas haría la prueba inestable. Sí entra la tabla padre con su clave de partición.
  * El esquema retencion_archivo por dentro: recibe en ejecución las particiones desenganchadas
    (054/055). Solo se congela el esquema y su ACL.
  * Objetos de extensiones (pg_depend 'e') y las propias extensiones: pg_stat_statements y pg_cron son
    opcionales según el servidor (db/00_extensiones.sql), y su versión depende de la imagen.
  * Constraints clonadas (conparentid <> 0): una FK hacia una tabla particionada se replica en cada partición
    referenciada; se congela solo la del padre.
  * attoptions de columna: el repo no fija ninguna y el servidor puede añadir las suyas.
  * COMMENT: es documentación, no estructura; su cobertura la vigila KC-DB-12-2.

Uso:
  python db/tools/congelar_esquema.py                 # regenera la instantánea (KC_DATABASE_URL_ADMIN)
  python db/tools/congelar_esquema.py --comprobar     # compara y sale con 1 si diverge
  python db/tools/congelar_esquema.py --dsn postgresql://... --salida otra.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
INSTANTANEA = RAIZ / "db" / "esquema_v2.snapshot.json"
MIGRACIONES = RAIZ / "db" / "migrations"
VERSION_FORMATO = 1

ESQUEMAS = ("core", "vec", "audit", "cm", "public")
# Esquemas que se congelan solo como contenedor (dueño y ACL), no por dentro.
ESQUEMAS_CONTENEDOR = ("retencion_archivo",)

Catalogo = dict[str, dict[str, Any]]

# Relaciones congelables: en los esquemas del alcance, que no son partición hija ni de una extensión.
_FILTRO_REL = """
    n.nspname = ANY(%(esquemas)s)
    AND NOT c.relispartition
    AND NOT EXISTS (SELECT 1 FROM pg_depend d
                     WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
"""

_ACL = """
    COALESCE((SELECT jsonb_agg(x ORDER BY x)
                FROM (SELECT CASE a.grantee WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END
                             || ':' || a.privilege_type || CASE WHEN a.is_grantable THEN '*' ELSE '' END AS x
                        FROM aclexplode({acl}) a) s), '[]'::jsonb)
"""


def _acl(columna: str) -> str:
    """ACL ordenada como lista «rol:PRIVILEGIO». NULL (privilegios por defecto) se congela como 'default'."""
    return (
        f"CASE WHEN {columna} IS NULL THEN '\"default\"'::jsonb ELSE {_ACL.format(acl=columna)} END"
    )


CONSULTAS: dict[str, str] = {
    "esquemas": f"""
        SELECT n.nspname AS clave,
               jsonb_build_object('dueno', pg_get_userbyid(n.nspowner),
                                  'acl', {_acl("n.nspacl")}) AS valor
          FROM pg_namespace n
         WHERE n.nspname = ANY(%(esquemas)s) OR n.nspname = ANY(%(contenedores)s)
    """,
    "tablas": f"""
        SELECT n.nspname || '.' || c.relname AS clave,
               jsonb_build_object(
                   'tipo', c.relkind::text,
                   'dueno', pg_get_userbyid(c.relowner),
                   'persistencia', c.relpersistence::text,
                   'rls', c.relrowsecurity,
                   'rls_force', c.relforcerowsecurity,
                   'particion', CASE WHEN c.relkind = 'p' THEN pg_get_partkeydef(c.oid) END,
                   'opciones', COALESCE(to_jsonb(c.reloptions), '[]'::jsonb),
                   'acl', {_acl("c.relacl")}) AS valor
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p') AND {_FILTRO_REL}
    """,
    "columnas": """
        SELECT n.nspname || '.' || c.relname || '.' || a.attname AS clave,
               jsonb_strip_nulls(jsonb_build_object(
                   'tipo', format_type(a.atttypid, a.atttypmod),
                   'not_null', a.attnotnull,
                   'default', pg_get_expr(ad.adbin, ad.adrelid),
                   'identity', NULLIF(a.attidentity::text, ''),
                   'generada', NULLIF(a.attgenerated::text, ''),
                   'collation', CASE WHEN a.attcollation <> t.typcollation
                                     THEN (SELECT collname FROM pg_collation WHERE oid = a.attcollation) END,
                   'estadisticas', NULLIF(a.attstattarget, -1))) AS valor
          FROM pg_attribute a
          JOIN pg_class c ON c.oid = a.attrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_type t ON t.oid = a.atttypid
          LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
         WHERE a.attnum > 0 AND NOT a.attisdropped
           AND c.relkind IN ('r', 'p', 'v', 'm', 'c') AND """
    + _FILTRO_REL,
    "constraints": """
        SELECT n.nspname || '.' || c.relname || '.' || k.conname AS clave,
               jsonb_build_object('definicion', pg_get_constraintdef(k.oid, true),
                                  'validada', k.convalidated,
                                  'diferible', k.condeferrable,
                                  'diferida', k.condeferred) AS valor
          FROM pg_constraint k
          JOIN pg_class c ON c.oid = k.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE k.conparentid = 0 AND """
    + _FILTRO_REL,
    "indices": """
        SELECT n.nspname || '.' || i.relname AS clave,
               jsonb_build_object('tabla', n.nspname || '.' || c.relname,
                                  'definicion', pg_get_indexdef(i.oid)) AS valor
          FROM pg_index x
          JOIN pg_class i ON i.oid = x.indexrelid
          JOIN pg_class c ON c.oid = x.indrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'm') AND """
    + _FILTRO_REL,
    "vistas": f"""
        SELECT n.nspname || '.' || c.relname AS clave,
               jsonb_build_object('tipo', c.relkind::text,
                                  'dueno', pg_get_userbyid(c.relowner),
                                  'opciones', COALESCE(to_jsonb(c.reloptions), '[]'::jsonb),
                                  'definicion', pg_get_viewdef(c.oid, true),
                                  'acl', {_acl("c.relacl")}) AS valor
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('v', 'm') AND {_FILTRO_REL}
    """,
    "secuencias": f"""
        SELECT n.nspname || '.' || c.relname AS clave,
               jsonb_build_object('tipo', format_type(s.seqtypid, NULL),
                                  'inicio', s.seqstart, 'incremento', s.seqincrement,
                                  'minimo', s.seqmin, 'maximo', s.seqmax, 'ciclo', s.seqcycle,
                                  'dueno', pg_get_userbyid(c.relowner),
                                  'acl', {_acl("c.relacl")}) AS valor
          FROM pg_sequence s
          JOIN pg_class c ON c.oid = s.seqrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE {_FILTRO_REL}
    """,
    "funciones": f"""
        SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
                   AS clave,
               jsonb_build_object(
                   'tipo', p.prokind::text,
                   'retorno', pg_get_function_result(p.oid),
                   'lenguaje', l.lanname,
                   'volatilidad', p.provolatile::text,
                   'estricta', p.proisstrict,
                   'security_definer', p.prosecdef,
                   'config', COALESCE(to_jsonb(p.proconfig), '[]'::jsonb),
                   'dueno', pg_get_userbyid(p.proowner),
                   'cuerpo', p.prosrc,
                   'acl', {_acl("p.proacl")}) AS valor
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_language l ON l.oid = p.prolang
         WHERE n.nspname = ANY(%(esquemas)s)
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')
    """,
    "triggers": """
        SELECT n.nspname || '.' || c.relname || '.' || t.tgname AS clave,
               jsonb_build_object('definicion', pg_get_triggerdef(t.oid, true),
                                  'habilitado', t.tgenabled::text) AS valor
          FROM pg_trigger t
          JOIN pg_class c ON c.oid = t.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE NOT t.tgisinternal AND """
    + _FILTRO_REL,
    "politicas": """
        SELECT n.nspname || '.' || c.relname || '.' || p.polname AS clave,
               jsonb_build_object(
                   'comando', p.polcmd::text,
                   'permisiva', p.polpermissive,
                   'roles', (SELECT jsonb_agg(CASE r WHEN 0 THEN 'PUBLIC' ELSE pg_get_userbyid(r) END
                                              ORDER BY 1)
                               FROM unnest(p.polroles) r),
                   'using', pg_get_expr(p.polqual, p.polrelid),
                   'with_check', pg_get_expr(p.polwithcheck, p.polrelid)) AS valor
          FROM pg_policy p
          JOIN pg_class c ON c.oid = p.polrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE """
    + _FILTRO_REL,
    "tipos": """
        SELECT n.nspname || '.' || t.typname AS clave,
               jsonb_strip_nulls(jsonb_build_object(
                   'tipo', t.typtype::text,
                   'dueno', pg_get_userbyid(t.typowner),
                   'etiquetas', (SELECT jsonb_agg(e.enumlabel ORDER BY e.enumsortorder)
                                   FROM pg_enum e WHERE e.enumtypid = t.oid),
                   'base', CASE WHEN t.typtype = 'd' THEN format_type(t.typbasetype, t.typtypmod) END,
                   'not_null', CASE WHEN t.typtype = 'd' THEN t.typnotnull END,
                   'checks', (SELECT jsonb_agg(pg_get_constraintdef(k.oid, true) ORDER BY k.conname)
                                FROM pg_constraint k WHERE k.contypid = t.oid))) AS valor
          FROM pg_type t
          JOIN pg_namespace n ON n.oid = t.typnamespace
          LEFT JOIN pg_class c ON c.oid = t.typrelid
         WHERE n.nspname = ANY(%(esquemas)s)
           AND t.typtype IN ('e', 'd', 'c')
           AND (t.typrelid = 0 OR c.relkind = 'c')
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
    """,
    "privilegios_por_defecto": f"""
        SELECT pg_get_userbyid(d.defaclrole) || ':' || COALESCE(n.nspname, '*') || ':'
                   || d.defaclobjtype::text AS clave,
               {_acl("d.defaclacl")} AS valor
          FROM pg_default_acl d
          LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
         WHERE n.nspname IS NULL OR n.nspname = ANY(%(esquemas)s) OR n.nspname = ANY(%(contenedores)s)
    """,
    "event_triggers": """
        SELECT e.evtname AS clave,
               jsonb_build_object('evento', e.evtevent,
                                  'funcion', e.evtfoid::regproc::text,
                                  'habilitado', e.evtenabled::text,
                                  'etiquetas', COALESCE(to_jsonb(e.evttags), '[]'::jsonb)) AS valor
          FROM pg_event_trigger e
         WHERE NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_event_trigger'::regclass AND d.objid = e.oid
                              AND d.deptype = 'e')
    """,
}


# ----------------------------------------------------------------------------
# Normalización
# ----------------------------------------------------------------------------
def _normalizar(seccion: str, valor: Any) -> Any:
    """Quita lo que depende del checkout y no del esquema: el cuerpo de una función cargada con psql desde un
    archivo con CRLF (Windows) lleva '\\r'. Se congela su sha256, no el texto, para que el JSON sea legible."""
    if seccion == "funciones" and isinstance(valor, dict) and "cuerpo" in valor:
        cuerpo = str(valor.pop("cuerpo")).replace("\r\n", "\n")
        valor["cuerpo_sha256"] = hashlib.sha256(cuerpo.encode("utf-8")).hexdigest()
    return valor


# ----------------------------------------------------------------------------
# Extracción
# ----------------------------------------------------------------------------
def extraer_catalogo(conn: Any) -> Catalogo:
    """Catálogo canónico de la base a la que apunta ``conn`` (psycopg, cualquier rol que vea pg_catalog).

    Se ejecuta en la transacción en curso de ``conn``, así que ve también los cambios no confirmados (la
    prueba de deriva los hace en una transacción que revierte)."""
    parametros = {"esquemas": list(ESQUEMAS), "contenedores": list(ESQUEMAS_CONTENEDOR)}
    catalogo: Catalogo = {}
    for seccion, sql in CONSULTAS.items():
        filas = conn.execute(sql, parametros).fetchall()
        entradas: dict[str, Any] = {}
        for fila in filas:
            clave, valor = (fila["clave"], fila["valor"]) if isinstance(fila, dict) else fila
            entradas[clave] = _normalizar(seccion, valor)
        catalogo[seccion] = dict(sorted(entradas.items()))
    return catalogo


def leer_ledger(conn: Any) -> dict[str, str]:
    """{archivo: sha256} de core.schema_migrations (requiere un rol que pueda leer el ledger)."""
    filas = conn.execute(
        "SELECT archivo, sha256 FROM core.schema_migrations ORDER BY archivo"
    ).fetchall()
    return {
        (f["archivo"] if isinstance(f, dict) else f[0]): (
            f["sha256"] if isinstance(f, dict) else f[1]
        )
        for f in filas
    }


def leer_etiqueta(conn: Any) -> str | None:
    fila = conn.execute("SELECT etiqueta FROM core.version_esquema()").fetchone()
    if fila is None:
        return None
    valor = fila["etiqueta"] if isinstance(fila, dict) else fila[0]
    return str(valor) if valor is not None else None


def migraciones_del_repo(directorio: Path = MIGRACIONES) -> dict[str, str]:
    """{archivo: sha256} de los archivos de db/migrations con el mismo cálculo que el runner (LF)."""
    sys.path.insert(0, str(RAIZ / "packages" / "core" / "src"))
    from kallicode_core.migraciones import leer_migraciones

    return {m.archivo: m.sha256 for m in leer_migraciones(directorio)}


def construir_instantanea(conn: Any) -> dict[str, Any]:
    return {
        "version_formato": VERSION_FORMATO,
        "etiqueta": leer_etiqueta(conn),
        "migraciones": leer_ledger(conn),
        "catalogo": extraer_catalogo(conn),
    }


def serializar(instantanea: dict[str, Any]) -> str:
    return json.dumps(instantanea, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def cargar(ruta: Path = INSTANTANEA) -> dict[str, Any]:
    datos: dict[str, Any] = json.loads(ruta.read_text(encoding="utf-8"))
    return datos


# ----------------------------------------------------------------------------
# Comparación
# ----------------------------------------------------------------------------
def _breve(valor: Any, limite: int = 160) -> str:
    texto = json.dumps(valor, ensure_ascii=False, sort_keys=True)
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def comparar_catalogos(congelado: Catalogo, actual: Catalogo) -> list[str]:
    """Diferencias legibles entre dos catálogos, una por objeto: «+» aparece, «-» desaparece, «~» cambia."""
    diferencias: list[str] = []
    for seccion in sorted(set(congelado) | set(actual)):
        antes = congelado.get(seccion, {})
        despues = actual.get(seccion, {})
        for clave in sorted(set(antes) | set(despues)):
            if clave not in antes:
                diferencias.append(f"+ {seccion}: {clave} = {_breve(despues[clave])}")
            elif clave not in despues:
                diferencias.append(f"- {seccion}: {clave}")
            elif antes[clave] != despues[clave]:
                diferencias.append(
                    f"~ {seccion}: {clave}: {_breve(antes[clave])} -> {_breve(despues[clave])}"
                )
    return diferencias


def comparar_migraciones(congeladas: dict[str, str], declaradas: dict[str, str]) -> list[str]:
    """Migraciones del repo que la instantánea no recoge (o con otro sha256), y al revés."""
    diferencias: list[str] = []
    for archivo in sorted(set(congeladas) | set(declaradas)):
        if archivo not in congeladas:
            diferencias.append(f"+ {archivo}: migración nueva que la instantánea no recoge")
        elif archivo not in declaradas:
            diferencias.append(f"- {archivo}: está en la instantánea pero no en db/migrations")
        elif congeladas[archivo] != declaradas[archivo]:
            diferencias.append(f"~ {archivo}: sha256 distinto al de la instantánea")
    return diferencias


def _lineas(titulo: str, diferencias: Iterable[str]) -> str:
    return titulo + "\n  " + "\n  ".join(diferencias)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _dsn_por_defecto() -> str | None:
    url = os.getenv("KC_DATABASE_URL_ADMIN") or os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        return None
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dsn", default=_dsn_por_defecto(), help="por defecto KC_DATABASE_URL_ADMIN"
    )
    parser.add_argument("--salida", type=Path, default=INSTANTANEA)
    parser.add_argument("--comprobar", action="store_true", help="compara con --salida; no escribe")
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("indica --dsn o define KC_DATABASE_URL_ADMIN")

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        actual = construir_instantanea(conn)

    declaradas = migraciones_del_repo()
    pendientes = comparar_migraciones(actual["migraciones"], declaradas)
    if pendientes:
        print(
            _lineas("La base no tiene aplicadas exactamente las migraciones del repo:", pendientes)
        )
        return 1

    if not args.comprobar:
        args.salida.write_text(serializar(actual), encoding="utf-8", newline="\n")
        total = sum(len(v) for v in actual["catalogo"].values())
        print(f"Instantánea {actual['etiqueta']} escrita en {args.salida} ({total} objetos).")
        return 0

    congelada = cargar(args.salida)
    diferencias = comparar_migraciones(congelada["migraciones"], declaradas) + comparar_catalogos(
        congelada["catalogo"], actual["catalogo"]
    )
    if diferencias:
        print(_lineas(f"El esquema diverge de {args.salida}:", diferencias))
        return 1
    print(f"El esquema coincide con {args.salida} ({congelada['etiqueta']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
