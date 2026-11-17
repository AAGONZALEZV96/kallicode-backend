#!/usr/bin/env python3
"""Informe de la auditoría de RLS, privilegios y roles (KC-DB-12-3), generado del catálogo.

Lee core.informe_aislamiento() y core.informe_politicas_rls() (migración 069) y los atributos de los roles, y escribe
docs/auditoria_rls.md. El documento versionado se compara en CI con el generado (test_auditoria_rls.py): si cambia el
aislamiento y nadie regeneró el informe, la prueba falla.

Uso:
    python db/tools/auditoria_rls.py                 # escribe docs/auditoria_rls.md
    python db/tools/auditoria_rls.py --comprobar     # sale con 1 si el documento versionado no coincide
Requiere KC_DATABASE_URL_MIGRATIONS (rol administrador). Es determinista: no lleva fechas ni recuentos de particiones.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
DOCUMENTO = RAIZ / "docs" / "auditoria_rls.md"
ROLES = ("kallicode_app", "kallicode_worker", "kallicode_admin")


def _dsn() -> str:
    url = os.environ.get("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        raise SystemExit("falta KC_DATABASE_URL_MIGRATIONS (rol administrador)")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _si(valor: bool | None) -> str:
    return "sí" if valor else "no"


def generar(conn: Any) -> str:
    tablas = conn.execute(
        "SELECT tabla, tiene_tenant_id, rls, forzada, politicas, politicas_sin_to, priv_app, priv_worker, "
        "particiones_con_acceso, probatoria, estado, justificacion FROM core.informe_aislamiento() ORDER BY tabla"
    ).fetchall()
    politicas = conn.execute(
        "SELECT tabla, politica, comando, roles, filtra_por_tenant, estado, justificacion FROM core.informe_politicas_rls() "
        "ORDER BY tabla, politica"
    ).fetchall()
    roles = conn.execute(
        "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin FROM pg_roles "
        "WHERE rolname = ANY(%s) ORDER BY rolname",
        (list(ROLES),),
    ).fetchall()
    bypass_otros = [
        r[0]
        for r in conn.execute(
            "SELECT rolname FROM pg_roles WHERE rolbypassrls AND NOT rolsuper AND rolname <> ALL(%s) ORDER BY 1",
            (["kallicode_admin"],),
        ).fetchall()
    ]
    estados: dict[str, int] = {}
    for t in tablas:
        k = "INCUMPLE" if str(t[10]).startswith("INCUMPLE") else str(t[10])
        estados[k] = estados.get(k, 0) + 1
    incumple_p = sum(1 for p in politicas if str(p[5]).startswith("INCUMPLE"))
    con_tenant = [t for t in tablas if t[1]]

    o: list[str] = [
        "# Auditoría de RLS, privilegios y roles (KC-DB-12-3)",
        "",
        "> **Documento generado** por `python db/tools/auditoria_rls.py` desde `core.informe_aislamiento()` y",
        "> `core.informe_politicas_rls()` (migración `069_auditoria_rls.sql`). No se edita a mano: una prueba lo compara",
        "> con el catálogo. Pruebas: `packages/core/tests/integracion/test_auditoria_rls.py` y",
        "> `test_penetracion_tenants.py`.",
        "",
        "## 1. Resultado",
        "",
        f"* **{len(tablas)} tablas** en `core`, `audit`, `vec` y `cm` (sin contar particiones hijas): "
        f"**{estados.get('aislada', 0)} aisladas** (RLS + FORCE + política con cláusula TO), "
        f"**{estados.get('excepcion_justificada', 0)} excepciones justificadas** (sin RLS, con su motivo) y "
        f"**{estados.get('INCUMPLE', 0)} que incumplen**.",
        f"* **Todas las {len(con_tenant)} tablas con `tenant_id`** están en RLS + FORCE: "
        f"{_si(all(t[2] and t[3] for t in con_tenant))}.",
        f"* **{len(politicas)} políticas**: {incumple_p} que incumplen (sin cláusula `TO` o sin filtrar por tenant sin justificar).",
        f"* **BYPASSRLS fuera de `kallicode_admin`** (descontado el superusuario de la instalación): "
        f"{', '.join(bypass_otros) if bypass_otros else 'ninguno'}.",
        "* **Particiones hijas accesibles** para la aplicación o el worker: "
        f"{sum(int(t[8] or 0) for t in tablas)}.",
        "",
        "## 2. Qué se encontró y se corrigió",
        "",
        "La auditoría sobre el esquema real (66 migraciones) encontró cuatro cosas; la migración `069_auditoria_rls.sql` las corrige:",
        "",
        "1. **Fuga entre tenants por una partición hija.** `vec.definition_embeddings_default` daba `SELECT`, `INSERT`, `UPDATE` y `DELETE` a",
        "   `kallicode_app` y `kallicode_worker`. Una partición **no hereda las políticas RLS** del padre: con permiso sobre ella, la aplicación leía",
        "   las filas de todos los tenants saltándose el aislamiento. La causa fue `GRANT ... ON ALL TABLES IN SCHEMA vec` de la migración 037,",
        "   ejecutado después de crear la partición; el event trigger `kc_particiones_sin_acceso` solo actúa en el momento del DDL.",
        "   Se revoca el acceso a **todas** las particiones y una prueba lo vigila.",
        "2. **`core.retention_purgas` tenía `tenant_id` y no tenía RLS.** Ahora tiene RLS + FORCE + política por tenant (la aplicación no tiene",
        "   privilegios sobre ella: es defensa en profundidad).",
        "3. **66 políticas sin cláusula `TO`** (valían para `PUBLIC`): `tenant_isolation` en 65 tablas y `plan_prices_solo_lectura`. Ahora todas van",
        "   `TO kallicode_app, kallicode_worker` (`kallicode_dev` hereda de `kallicode_app`).",
        "4. **Escrituras de la aplicación sobre catálogos globales** (`core.user_roles`, `core.pipeline_stages`, `vec.acta_conversion_capa`): venían del",
        "   `DEFAULT PRIVILEGES`, el código no las usa y permitían a un tenant alterar un catálogo compartido. Se revocan.",
        "",
        "## 3. Roles",
        "",
        "| Rol | Superusuario | BYPASSRLS | CREATEROLE | CREATEDB | Login |",
        "|---|---|---|---|---|---|",
    ]
    for r in roles:
        o.append(
            f"| `{r[0]}` | {_si(r[1])} | {_si(r[2])} | {_si(r[3])} | {_si(r[4])} | {_si(r[5])} |"
        )
    o += [
        "",
        "Solo `kallicode_admin` (el rol de las migraciones) tiene `BYPASSRLS`. Ni la aplicación ni el worker pueden crear objetos en ningún esquema, ni",
        "tienen `TRUNCATE`, `TRIGGER` ni `REFERENCES` sobre ninguna tabla, ni son dueños de ninguna.",
        "",
        "## 4. Tablas",
        "",
        "`probatoria` = ni la aplicación ni el worker pueden modificarla ni borrarla (KC-DB-05-2). Los privilegios son los de `kallicode_app` y",
        "`kallicode_worker` sobre la tabla (`-` = ninguno).",
        "",
        "| Tabla | tenant_id | RLS | FORCE | Políticas | Sin TO | Privilegios app | Privilegios worker | Probatoria | Estado |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in tablas:
        o.append(
            f"| `{t[0]}` | {_si(t[1])} | {_si(t[2])} | {_si(t[3])} | {t[4]} | {t[5]} | {t[6]} | {t[7]} | {_si(t[9])} | {t[10]} |"
        )
    o += [
        "",
        "## 5. Políticas",
        "",
        "| Tabla | Política | Comando | Roles (cláusula TO) | Filtra por tenant | Estado |",
        "|---|---|---|---|---|---|",
    ]
    for p in politicas:
        o.append(f"| `{p[0]}` | `{p[1]}` | {p[2]} | {p[3]} | {_si(p[4])} | {p[5]} |")
    exc = [t for t in tablas if t[11]]
    o += [
        "",
        "## 6. Excepciones justificadas",
        "",
        "### Tablas sin RLS",
        "",
        "| Tabla | Por qué es seguro |",
        "|---|---|",
    ]
    for t in exc:
        o.append(f"| `{t[0]}` | {t[11]} |")
    o += [
        "",
        "### Políticas que no filtran por tenant",
        "",
        "| Política | Por qué es seguro |",
        "|---|---|",
    ]
    for p in politicas:
        if p[6]:
            o.append(f"| `{p[0]}#{p[1]}` | {p[6]} |")
    o += [
        "",
        "## 7. Límites de esta auditoría",
        "",
        "* **Las dos políticas de `pg_cron`** (`cron.job` y `cron.job_run_details`, filtran por `CURRENT_USER`) pertenecen a la extensión y están",
        "  fuera de `core`, `audit`, `vec` y `cm`: no se tocan ni se incluyen aquí.",
        "* **El aislamiento protege contra errores de consulta, no contra un cliente con SQL arbitrario.** El tenant de la sesión lo fija la propia",
        "  aplicación (`app.tenant_id`); quien pudiera ejecutar SQL libre con el rol de la aplicación podría fijarlo a otro valor. La defensa es no",
        "  exponer SQL libre (inyección) y `kallicode_admin`/`postgres` fuera del alcance de la aplicación.",
        "* **El superusuario `postgres` de la instalación** tiene `BYPASSRLS` por ser superusuario: no se usa desde la aplicación.",
        "* **Una tabla nueva en `core` o `vec` recibe `SELECT/INSERT/UPDATE/DELETE` para la aplicación por `DEFAULT PRIVILEGES`.** Por eso una tabla",
        "  nueva sin RLS y sin excepción aparece aquí como `INCUMPLE` y la prueba falla hasta que se decida.",
        "* Es una auditoría automática sobre un esquema de demostración, **no una revisión externa**; su aprobación por el responsable de",
        "  seguridad no está dada por este documento.",
        "",
    ]
    return "\n".join(o)


def main(argv: list[str] | None = None) -> int:
    import psycopg

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--comprobar",
        action="store_true",
        help="falla si docs/auditoria_rls.md no coincide con el catálogo",
    )
    args = ap.parse_args(argv)
    with psycopg.connect(_dsn()) as conn:
        texto = generar(conn)
    if args.comprobar:
        actual = (
            DOCUMENTO.read_text(encoding="utf-8").replace("\r\n", "\n")
            if DOCUMENTO.exists()
            else ""
        )
        if actual != texto:
            print(
                "docs/auditoria_rls.md no coincide con el catálogo: regenéralo con python db/tools/auditoria_rls.py",
                file=sys.stderr,
            )
            return 1
        print("OK: docs/auditoria_rls.md coincide con el catálogo")
        return 0
    DOCUMENTO.write_text(texto, encoding="utf-8", newline="\n")
    print(f"escrito {DOCUMENTO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
