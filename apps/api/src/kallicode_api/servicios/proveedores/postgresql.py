"""Verificación real de conexión a PostgreSQL del cliente con validación de privilegios mínimos."""

from __future__ import annotations

import time
from urllib.parse import urlparse

import asyncpg

from ._base import ResultadoPrueba, medir_ms


async def probar_postgresql(config: dict, credenciales: dict) -> ResultadoPrueba:
    # El portal manda puerto y base en config, y en credenciales `db_url` (la que lee
    # CodeMapping); se aceptan junto a los nombres en inglés.
    dsn = str(
        credenciales.get("dsn") or credenciales.get("url") or credenciales.get("db_url") or ""
    ).strip()
    host = str(config.get("host") or credenciales.get("host") or "").strip()
    port = int(
        config.get("port")
        or config.get("puerto")
        or credenciales.get("port")
        or credenciales.get("puerto")
        or 5432
    )
    database = str(
        config.get("database")
        or config.get("base")
        or credenciales.get("database")
        or credenciales.get("base")
        or ""
    ).strip()
    usuario = str(credenciales.get("usuario") or credenciales.get("user") or "").strip()
    password = str(credenciales.get("password") or credenciales.get("contrasena") or "")

    if not dsn and not (host and database and usuario):
        return ResultadoPrueba(
            ok=False,
            mensaje="Faltan parámetros de conexión a PostgreSQL (host, database y usuario, o dsn).",
        )

    if dsn and not usuario:
        usuario = urlparse(dsn).username or ""

    inicio = time.perf_counter()
    conn = None

    try:
        if dsn:
            conn = await asyncpg.connect(dsn=dsn, timeout=5.0)
        else:
            conn = await asyncpg.connect(
                host=host,
                port=port,
                database=database,
                user=usuario,
                password=password,
                timeout=5.0,
            )

        # 1. Verificar permisos privilegiados del rol en pg_roles
        rol_row = await conn.fetchrow(
            """
            SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
              FROM pg_roles
             WHERE rolname = current_user
            """
        )
        if rol_row and (
            rol_row["rolsuper"]
            or rol_row["rolcreaterole"]
            or rol_row["rolcreatedb"]
            or rol_row["rolbypassrls"]
        ):
            atributos = [
                nombre
                for nombre in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolbypassrls")
                if rol_row[nombre]
            ]
            return ResultadoPrueba(
                ok=False,
                permisos_excesivos=True,
                mensaje=(
                    f"Privilegios excesivos detectados en PostgreSQL para '{usuario}' ({', '.join(atributos)}). "
                    "Crea un usuario de solo metadatos restringido a information_schema: "
                    "CREATE ROLE kallicode_meta LOGIN PASSWORD '...'; "
                    "GRANT CONNECT ON DATABASE <db> TO kallicode_meta; "
                    "GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO kallicode_meta;"
                ),
                latencia_ms=medir_ms(inicio),
            )

        # 2. Verificar que no tenga permisos de escritura/lectura de datos de negocio sobre tablas de usuario
        grants_datos = await conn.fetchval(
            """
            SELECT count(*)
              FROM information_schema.role_table_grants
             WHERE grantee = current_user
               AND table_schema NOT IN ('pg_catalog', 'information_schema')
               AND privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
            """
        )
        if grants_datos and int(grants_datos) > 0:
            return ResultadoPrueba(
                ok=False,
                permisos_excesivos=True,
                mensaje=(
                    f"Privilegios excesivos: el usuario '{usuario}' tiene acceso directo a tablas de datos "
                    f"({grants_datos} permisos otorgados en esquemas de negocio). "
                    "Revoca los permisos sobre tablas de negocio con: "
                    f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {usuario};"
                ),
                latencia_ms=medir_ms(inicio),
            )

        # 3. Verificar consulta real a information_schema
        total_tablas = await conn.fetchval(
            """
            SELECT count(*)
              FROM information_schema.tables
             WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            """
        )
        if total_tablas is None:
            return ResultadoPrueba(
                ok=False,
                mensaje=(
                    f"Permiso faltante en PostgreSQL: '{usuario}' no puede consultar information_schema.tables. "
                    f"Concede acceso con: GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO {usuario};"
                ),
                latencia_ms=medir_ms(inicio),
            )

        return ResultadoPrueba(
            ok=True,
            mensaje=f"Conexión con PostgreSQL verificada ({total_tablas} tablas visibles en information_schema).",
            permisos=["information_schema:read"],
            latencia_ms=medir_ms(inicio),
        )

    except asyncpg.InvalidPasswordError:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"PostgreSQL rechazó la autenticación para el usuario '{usuario}' (credencial inválida).",
            latencia_ms=medir_ms(inicio),
        )
    except asyncpg.InsufficientPrivilegeError as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=(
                f"Permiso insuficiente en PostgreSQL para '{usuario}': {exc}. "
                f"Ejecuta: GRANT CONNECT ON DATABASE {database} TO {usuario}; "
                f"GRANT SELECT ON ALL TABLES IN SCHEMA information_schema TO {usuario};"
            ),
            latencia_ms=medir_ms(inicio),
        )
    except (TimeoutError, asyncpg.PostgresConnectionError, OSError) as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"No se pudo conectar con PostgreSQL ({type(exc).__name__}). Verifica host, puerto y red.",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=True,
        )
    except asyncpg.PostgresError as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"Error de PostgreSQL al verificar metadatos: {exc}.",
            latencia_ms=medir_ms(inicio),
        )
    finally:
        if conn is not None:
            await conn.close()
