# Revisión de seguridad de CodeMapping para GA (KC-CM-12-6)

| | |
|---|---|
| **Ticket** | KC-CM-12-6 · Sprint 12 · ECM7 · CM4 |
| **Alcance** | `packages/codemapping/` y su imagen (`apps/workers/Dockerfile.codemapping`) |
| **Fecha de la revisión** | 2026-11-18 |
| **Estado** | Corregido y verificado en local · **pendiente:** verificación en staging (§5, §6) y firmas (§8) |

CodeMapping es la superficie más sensible del programa: clona el repositorio del cliente con un
token, se conecta a su base de datos productiva y procesa su código fuente. Esta revisión cubre
los cuatro criterios del ticket: escaneos en verde, logs sin datos del cliente, acceso de solo
lectura con tiempo límite y firma.

## 1. Superficie y amenazas

| Activo del cliente | Dónde entra | Amenaza | Control (tras esta revisión) |
|---|---|---|---|
| Token del repositorio | `repo_url` → `git clone` | Fuga en logs, motivos de snapshot o DLQ (git repite la URL en su stderr) | `redaccion` en el formateador, en `GitSyncError`, en los motivos persistidos y en la DLQ |
| Credencial de la base | `database_url` → `extract_sql` | Escritura accidental o inducida; sesión colgada contra producción | Transacción `READ ONLY` fijada en cada `begin`, cuatro tiempos límite, pool acotado, aviso si el rol escribe |
| Código fuente | `walk_sources`, parser, prompts del LLM | Fragmentos en logs o mensajes de error (pydantic/jsonschema copian la entrada) | Detección de código y `input_value` en `redaccion`; prompts con delimitadores (KC-CM-05-4/07-4) |
| Estructura de rutas | `workdir/<tenant>` | Rutas absolutas en logs | Sustitución por `<ruta>/<archivo>` |
| Imagen del worker | build | Dependencias vulnerables, secretos en capas, escritura del código en ejecución | Escaneo de la imagen en CI, `.dockerignore` propio, código de root y de solo lectura |

## 2. Hallazgos

| # | Hallazgo | Severidad | Estado |
|---|---|---|---|
| H1 | `ingest_repo.sync_repo` escribía el stderr de git en el log y en `GitSyncError`. Ese stderr incluye la URL del remoto (con el token) y la ruta absoluta del workdir | Alta | Corregido |
| H2 | El acceso de solo lectura dependía solo de `default_transaction_read_only`, que se desactiva con un `SET`. Además, PostgreSQL acepta `SET TRANSACTION READ WRITE` hasta la primera consulta de la transacción | Alta | Corregido: el evento `begin` fija `READ ONLY` y toma el snapshot (`SELECT 1`). Después de eso, `READ WRITE` falla con 25001 |
| H3 | La imagen del worker no arrancaba porque `codemapping` no declaraba la dependencia de `kallicode-core` y `uv sync --package codemapping` no lo instalaba. La imagen tampoco se construía en CI, así que nadie lo veía | Alta | Corregido: dependencia declarada, `uv.lock` regenerado y build en CI |
| H4 | `JsonFormatter` descartaba todos los `extra=` salvo tres claves y no redactaba nada. Cuatro módulos (`ingest_docs`, `nuggets`, `ontology_mapreduce`, `realimentacion`) registraban fuera del logger `codemapping`, sin formato JSON. Cada logger hijo tenía su propio handler, así que algunas líneas salían duplicadas | Media | Corregido |
| H5 | Los motivos de fallo que se guardan en `cm.repo_snapshots` y en las stats documentales eran `str(exc)` sin redactar. La DLQ solo enmascaraba credenciales en URLs | Media | Corregido (`mensaje_de_error`) |
| H6 | Faltaban `connect_timeout`, `lock_timeout` e `idle_in_transaction_session_timeout` contra la base del cliente | Media | Corregido (configurables, `CM_CLIENT_DB_*`) |
| H7 | Con un rol de mínimo privilegio, la extracción abortaba al llegar a un esquema sin `USAGE`. Eso empujaba a pedir al cliente un rol con más privilegios | Media | Corregido: esos esquemas se omiten y se registra cuántos son |
| H8 | Imagen: `uv` sin versión fijada; código y venv propiedad del usuario de ejecución; pruebas con secretos falsos dentro de la imagen; HEALTHCHECK con un DSN por defecto que llevaba contraseña; sin parches del sistema | Media | Corregido |
| H9 | Los dobles del benchmark de ingesta (`tests/bench_ingesta_escala.py`) tenían la firma anterior al fix D2, así que el benchmark nocturno fallaba | Baja | Corregido (lo reutiliza la prueba de logs) |

## 3. Cambios

- `redaccion.py` (nuevo) es la única definición de qué es sensible: credenciales en URLs, tokens con formato conocido, `clave=valor`, cabeceras `Authorization`, claves privadas, rutas POSIX, Windows, UNC y `file://`, `input_value` de pydantic e indicios de código. Las reglas son idempotentes, de modo que el auditor detecta un hallazgo cuando aplicar una regla cambiaría el texto.
- `logging_setup.py`: cada línea pasa por la redacción. Se emiten todos los `extra`. Una excepción se resume en tipo, mensaje redactado y `archivo:línea`, nunca con la traza. Solo la raíz `codemapping` tiene handler.
- `revision_logs.py` (nuevo) es el auditor de logs JSONL. Admite literales prohibidos y no repite en su informe el valor que encuentra.
- `extract_sql.py`: solo lectura por transacción, cuatro tiempos límite, `application_name`, aviso de privilegios de escritura (decisión del Tech Lead: avisar, no bloquear) y omisión de los esquemas sin `USAGE`.
- `verificacion_acceso_cliente.py` (nuevo) es el verificador que se ejecuta en staging. Usa el mismo engine que la ingesta.
- `worker.error_estructurado`, `build_graph`, `refresh`, `fase_documental` e `ingest_repo` usan la redacción.
- `Dockerfile.codemapping` y `Dockerfile.codemapping.dockerignore`: cambios descritos en H3 y H8.
- CI: nuevo job `seguridad_codemapping` en `.github/workflows/ci.yml`.

## 4. Criterio 1 — semgrep, trivy y gitleaks en verde

Job `seguridad_codemapping`, en cada PR:

| Escaneo | Alcance | Resultado local (2026-11-18) |
|---|---|---|
| Semgrep `p/python` + `p/secrets` + `p/dockerfile` | `packages/codemapping/src` y el Dockerfile | 0 hallazgos (187 reglas; el Dockerfile se escanea con 7 reglas de Dockerfile) |
| Gitleaks `dir` | `packages/codemapping` completo, pruebas incluidas | no leaks found |
| Trivy `image`, vuln + secret, CRITICAL/HIGH, `--ignore-unfixed` | `kallicode-codemapping:ci` (Debian 13.7, 64 paquetes Python) | 0 |
| Trivy `config` | `Dockerfile.codemapping` | 0 en **todas** las severidades |
| Contenido de la imagen | uid ≠ 0, sin `tests/`, código y venv no escribibles, workdir escribible | OK |

El job `seguridad` (semgrep `p/ci`, gitleaks del rango del PR y `trivy fs`) sigue cubriendo el
resto del repositorio.

## 5. Criterio 2 — 1.000 líneas de log sin credenciales, rutas ni código

**Prueba de CI:** `tests/test_logs_ingesta_pg.py` ejecuta el pipeline real (`full_build` y
`run_refresh`) sobre el repositorio sintético. Siembra secretos en los sitios por donde se filtrarían:
- la clave del LLM;
- la contraseña de la base del cliente;
- la ruta absoluta del workdir;
- un archivo del cliente que lleva un token en su código.

La prueba captura todo lo que registra el proceso y lo pasa por el auditor, con esos valores como
literales prohibidos. Las pruebas unitarias (`test/test_redaccion_unit.py`) cubren cada regla, los
falsos positivos y el fallo de git con un token en la URL.

**Ensayo local (2026-11-18):** una ingesta completa más 75 refrescos.

```
KC_CM_LOGS_CICLOS=75 KC_CM_LOGS_SALIDA=ingesta.jsonl uv run pytest packages/codemapping/tests/test_logs_ingesta_pg.py
python -m kallicode_codemapping.revision_logs ingesta.jsonl --minimo-lineas 1000 --prohibido kccm126
→ {"lineas": 1078, "lineas_no_json": 0, "hallazgos": 0, "ok": true}
```

**Pendiente en staging:** el criterio pide una ingesta real. Pasos:
1. Lanzar la ingesta de un tenant de staging.
2. Volcar sus últimas 1.000 líneas o más, con `docker logs` o `az containerapp logs show`.
3. Ejecutar dentro de la imagen:

```
python -m kallicode_codemapping.revision_logs ingesta.jsonl --minimo-lineas 1000 \
    --prohibido "<token del repo>" --prohibido "<contraseña de la base>" --prohibido /app/workdir
```

4. Adjuntar el JSON resultante aquí. El informe no repite los valores encontrados.

| Fecha | Tenant | Líneas | Hallazgos | Responsable |
|---|---|---|---|---|
| _pendiente_ | | | | |

## 6. Criterio 3 — base del cliente de solo lectura y con tiempo límite

**Controles:**

| Control | Valor por defecto |
|---|---|
| `default_transaction_read_only` | on (en las opciones de la conexión) |
| `SET TRANSACTION READ ONLY` y snapshot | en cada `begin` |
| `statement_timeout` | 30 s |
| `lock_timeout` | 5 s |
| `idle_in_transaction_session_timeout` | 60 s |
| `connect_timeout` | 10 s |
| `application_name` | `kallicode-codemapping` |
| Pool | 2 conexiones como máximo, sin overflow |

**Pruebas en PostgreSQL real** (`tests/test_acceso_cliente_pg.py`):
- INSERT y CREATE rechazados con 25006, también después de reabrir la sesión en escritura;
- `SET TRANSACTION READ WRITE` rechazado con 25001;
- `pg_sleep` cancelado con 57014;
- espera por bloqueo cortada con 55P03;
- verificador en verde con un rol de solo lectura y sin avisos;
- aviso con un superusuario;
- extracción completa con un rol de mínimo privilegio.

**Ensayo local del verificador (2026-11-18):** 10 de 10 comprobaciones OK. Aviso esperado: el rol
local es superusuario (164 tablas con escritura).

**Pendiente en staging:** ejecutar en el contenedor del worker, con la configuración del tenant:

```
python -m kallicode_codemapping.verificacion_acceso_cliente > acceso_cliente.json   # sale con 1 si algo falla
```

| Fecha | Tenant | Comprobaciones OK | Avisos | Responsable |
|---|---|---|---|---|
| _pendiente_ | | | | |

## 7. Riesgos residuales

| Riesgo | Por qué se acepta / mitigación |
|---|---|
| La transacción de solo lectura vive en el engine de SQLAlchemy. Código que abriera el driver directamente la saltaría | Solo `extract_sql` toca la base del cliente y una prueba lo vigila. La defensa de fondo es un rol de solo lectura del cliente: el aviso queda en el log y en el verificador, y el onboarding debe pedirlo |
| La detección de código es heurística: un fragmento de una sola línea sin indicios puede pasar | Por diseño ningún log escribe el contenido de un archivo; la heurística cubre los mensajes de error, y el auditor corre en CI |
| `python:3.12-slim` sin fijar por digest | Decisión deliberada: recibir los parches de la base. Trivy de la imagen corre en cada PR y bloquea CRITICAL/HIGH con arreglo disponible |
| Escáneres con la etiqueta `latest` en CI | Igual que el resto del CI: reglas y base de CVE al día. Un PR puede fallar por un CVE nuevo, que es lo que se quiere |

## 8. Firmas

| Rol | Nombre | Fecha | Firma |
|---|---|---|---|
| Tech Lead | | | |
| Tech Lead de Testing | | | |
