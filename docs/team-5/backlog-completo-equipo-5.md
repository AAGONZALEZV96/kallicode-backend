# BACKLOG COMPLETO DE KALLICODE – EQUIPO 5 (PLATAFORMA Y CONFIANZA)
Este documento constituye la fuente de verdad definitiva para el backlog de trabajo del **Equipo 5 – Plataforma y Confianza (Plan E5)** de KalliCode, operando en la fecha actual de **31 de agosto de 2026** (transición del Sprint 4 al Sprint 5). El backlog está estructurado para una célula optimizada de **3 miembros** (derivados de los 4 perfiles teóricos), cubriendo la remediación de los **48 hallazgos heredados de auditoría** (9 bloqueantes, 14 altos, 16 medios, 9 bajos) y la provisión horizontal de servicios seguros, reproducibles y auditables para los otros 5 equipos.

---

## 1. Estructura del Equipo y Asignaciones
Dado que operamos con **3 ingenieros** en lugar de 4, se consolidó la carga técnica redistribuyendo las especialidades teóricas (PLT1 a PLT4) de la siguiente manera:

*   **Miembro A: Líder de Plataforma y Datos (Fusión PLT1 + PLT2)**
    *   **Áreas**: Arquitectura de `packages/core`, gobernanza del esquema relacional PostgreSQL 16, Row-Level Security (RLS), migraciones versionadas (ledger), particionamiento de alto volumen, pgvector, y optimización de índices.
*   **Miembro B: Ingeniero DevOps & SRE (PLT3 Puro)**
    *   **Áreas**: Infraestructura como Código (Bicep/IaC en Azure), pipeline de CI/CD global (`ci.yml` en los 6 repositorios), empaquetado uv, entornos efímeros de QA/Testing, automatización local, telemetría y pruebas de carga (k6).
*   **Miembro C: Oficial de Seguridad y Confianza (PLT4 Puro)**
    *   **Áreas**: Criptografía aplicada, cadena de auditoría inmutable (solo-anexado), boveda de secretos (Key Vault), identidad (JWT/OAuth), denylist de tokens (Redis) y el sandbox de QA efímero y confinado sin red.

---

## 2. Resumen del Cronograma de Hitos y Entregas
El plan de trabajo consta de **12 sprints semanales** (desde el 3 de agosto hasta el 23 de octubre de 2026). Sostiene los dos hitos de los extremos del programa:

```
[M1: Sprint 2 - Base Verificable] ──► [S3-S4: Identidad y Auditoría] ──► [S5-S6: AKV, Blob y Sandbox] ──► [S7-S9: Escala y Purgas] ──► [M6: Sprint 12 - Release GA]
```

*   **M1 (Sprint 2) - Base Ejecutable y Verificable**: Entorno local reproducible (<15 mins), CI/CD en verde para todos los repositorios, contratos OpenAPI v1 congelados, y aislamiento multi-tenant demostrado.
*   **M2 (Sprint 4) - Soporte de Grafo en Postgres**: Revisión y veto de E5 sobre el esquema `cm` y la volumetría del Equipo 1.
*   **M3 (Sprint 6) - Soporte de Pipeline de Fábrica**: Despliegue de los workers con Redis Streams (DLQ, al menos una vez) y red privada interna para la Fábrica del Equipo 2.
*   **M4 (Sprint 8) - Soporte de QA Auditable**: Despliegue de los entornos de prueba efímeros y almacenamiento Blob WORM para el Equipo 3.
*   **M5 (Sprint 10) - Soporte de Consola Visible**: Instrumentación de telemetría y logs correlacionados en Azure Monitor para el Equipo 4.
*   **M6 (Sprint 12) - Release General Availability (GA)**: Firma de release GA conjunta tras pruebas de carga, recuperación, modelo de amenazas y cero hallazgos bloqueantes o altos abiertos.

---

## 3. Backlog Detallado Sprint a Sprint (Sprints 1 al 12)

### SPRINT 1 – QUE EL PROGRAMA PUEDA EMPEZAR
*   **Fechas**: 3 al 7 de agosto de 2026
*   **Capacidad**: 16 Story Points
*   **Foco del Sprint**: Entregar el pipeline de integración continua (CI) inicial para los 6 repositorios para habilitar el Do done de los otros equipos, separar las sesiones de base de datos de sistema y aplicación para el aislamiento multi-tenant real, y empaquetar `packages/core` con dependencias declaradas y reproducibles.

#### Tickets del Sprint 1:

*   **`KC-PLT-01-1`: Servicio a los seis equipos: pipeline de integración continua con todas sus puertas de calidad**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: No existe ningún archivo en `.github/workflows` en el monorepo. Se construye un pipeline compartido en GitHub Actions (`ci.yml`) con 8 puertas de calidad, linter, tipos, compilación y pruebas parametrizadas para Python (ruff, mypy, pytest) y Node/TypeScript (tsc, eslint, vitest).
    *   **Criterios de Aceptación**:
        *   `ci.yml` ejecuta en cada PR de los 6 proyectos y bloquea el merge si falla ruff, mypy, tsc, o pytest.
        *   Instancia PostgreSQL 16 con pgvector real en la puerta utilizando contenedores de servicio de Actions.
        *   Gitleaks, Semgrep y Trivy bloquean ante secretos expuestos o dependencias altamente vulnerables en producción.
    *   **Cierre de Hallazgos**: `BE-F06` (Bloqueante).

*   **`KC-PLT-01-2`: packages/core instalable: dependencias declaradas, uv.lock y verificación de tipos estricta**
    *   **Épica**: `EPL2` (Núcleo, Configuración y Empaquetado)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: `packages/core` carece de dependencias formales declaradas y no hay archivo de bloqueo, lo que rompe la reproducibilidad. Se reestructura el paquete con el gestor `uv`, se declaran dependencias en `pyproject.toml`, se versiona `uv.lock` y se activa mypy estricto.
    *   **Criterios de Aceptación**:
        *   Los pyproject.toml de core, api y workers declaran dependencias con restricción estricta de versión.
        *   `uv sync --frozen` se ejecuta sin error y de forma determinista en el build del CI y Dockerfiles.
        *   mypy en verde sobre `packages/core` en modo estricto. Los helpers de `db.py` no devuelven `Any`.
    *   **Cierre de Hallazgos**: `BE-F07` (Bloqueante), `BE-F34` (Medio).

*   **`KC-PLT-01-3`: Saneamiento del aislamiento RLS inicial en base de datos PostgreSQL**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: La aplicación usa un único usuario privilegiado para el portal, la API y las tareas de sistema, evadiendo las políticas RLS. Se separan los pools y roles creando `kallicode_app` (privilegio RLS forzado) y `kallicode_system_role` (lecturas cross-tenant para login/purgas).
    *   **Criterios de Aceptación**:
        *   Crear el rol de base de datos `kallicode_app` con seguridad RLS activada por default.
        *   La aplicación levanta conexiones con el rol restringido y falla de forma explícita si el rol tiene BYPASSRLS o privilegios administrativos.
        *   Prueba de integración demuestra que un SELECT sobre `core.tickets` sin `app.tenant_id` fijado devuelve cero filas y no falla.
    *   **Cierre de Hallazgos**: `BE-F01` (Bloqueante).

*   **`KC-PLT-01-4`: Entorno local reproducible por debajo de 15 minutos con Docker Compose**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 2 SP
    *   **Descripción**: El compose original es incompleto y poco seguro (Redis corre sin pass expuesto, Dockerfiles corren como root). Se reescriben los Dockerfiles en multi-etapa, sin root, con HEALTHCHECK, y compose con contraseñas seguras y dependencias (Redis, Azurite, Mailpit).
    *   **Criterios de Aceptación**:
        *   Arranque local completo desde máquina limpia en menos de 15 minutos mediante `make bootstrap` o compose.
        *   Imágenes Docker configuradas sin privilegios de root (`USER app`), multi-stage y con HEALTHCHECK de API funcional.
        *   Redis protegido con contraseña y red aislada.
    *   **Cierre de Hallazgos**: `BE-F38` (Bajo).

*   **`KC-PLT-01-5`: Configuración de pre-commit y firma del contrato de sesión con el Equipo 4**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 1 SP
    *   **Descripción**: Se agrega la configuración de `pre-commit` con ruff, black, gitleaks y comprobación de límites de tamaño. Adicionalmente, se congela por escrito el contrato de sesión con E4 (Portal) detallando el ciclo de vida del refresh token, HttpOnly, denylist, y sobre de error.
    *   **Criterios de Aceptación**:
        *   `make bootstrap` instala y ejecuta `pre-commit` en la máquina limpia del desarrollador local sin errores.
        *   Contrato de sesión congelado y firmado en `docs/contrato-sesion.md` por PLT4 y PRD1.
    *   **Cierre de Hallazgos**: `BE-F43` (Bajo).

---

### SPRINT 2 – QUE EL AISLAMIENTO SEA DEMOSTRABLE (HITO M1)
*   **Fechas**: 10 al 14 de agosto de 2026
*   **Capacidad**: 20 Story Points
*   **Foco del Sprint**: Consolidar y declarar formalmente el **Hito M1: Base verificable**. Implementar el framework de migraciones versionadas de la base de datos (con control transaccional de Alembic y verificación de hashes), asegurar que las tablas nuevas que nazcan de otros equipos hereden RLS automáticamente vía triggers DDL, y automatizar la suite de aislamiento multi-tenant.

#### Tickets del Sprint 2:

*   **`KC-PLT-02-1`: Servicio a los seis equipos: framework de migraciones versionadas con ledger transaccional**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 8 SP
    *   **Descripción**: No existe framework de migraciones. El Makefile aplica scripts directos con `|| true` tragándose errores. Se despliega Alembic adaptando la tabla `core.schema_migrations` para auditar el nombre del archivo, hash SHA-256 y fecha. Bloquea el arranque si se edita una migración ya aplicada.
    *   **Criterios de Aceptación**:
        *   `core.schema_migrations` guarda las migraciones con su hash SHA-256 verificado por el runner `migrate.py`.
        *   El runner aborta con código de error si el hash de un script SQL ya aplicado diverge en la base de datos.
        *   Las migraciones se aplican transaccionalmente y usan Advisory Locks de Postgres para evitar ejecuciones concurrentes en paralelo.
    *   **Cierre de Hallazgos**: `DB-F02` (Bloqueante).

*   **`KC-PLT-02-2`: RLS garantizado también para las tablas que todavía no existen en migraciones futuras**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: El barrido de RLS inicial es estático. Cualquier tabla nueva que los equipos modelen nace sin RLS por defecto. Se implementa un trigger de eventos de sistema sobre `ddl_command_end` que intercepta DDLs de tablas en los esquemas `core`, `vec`, `audit` y `cm` y les inyecta automáticamente `FORCE ROW LEVEL SECURITY` y la política.
    *   **Criterios de Aceptación**:
        *   Trigger de eventos DDL funcional en Postgres.
        *   Prueba crea una tabla de test sintética e intenta leerla como `kallicode_app` sin tenant fijado; debe arrojar error explícito y no devolver nada.
        *   La política se crea con la cláusula `TO` explícita para evitar bypass manual.
    *   **Cierre de Hallazgos**: `DB-F03` (Bloqueante), `DB-F21` (Medio), `DB-F22` (Medio).

*   **`KC-PLT-02-3`: Fuente de verdad única del esquema e higiene de los scripts SQL**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Hay duplicados redundantes del esquema en el repositorio, e inconsistencias en la base de datos (como bytes nulos al final de `01_schema.sql` que rompen greps). Se unifica en una única ruta canónica en `kallicode-backend/db/`, se limpia la sintaxis SQL haciéndola idempotente y se corrigen discrepancias del modelo.
    *   **Criterios de Aceptación**:
        *   Eliminar la carpeta duplicada `database/` del raíz; la única fuente de verdad es `kallicode-backend/db/`.
        *   `01_schema.sql` se verifica como texto plano de Postgres sin caracteres binarios nulos.
        *   Los scripts son idempotentes: el catálogo de planes usa `ON CONFLICT` y las sentencias DDL usan `IF NOT EXISTS`.
    *   **Cierre de Hallazgos**: `DB-F12` (Alto), `DB-F15` (Alto).

*   **`KC-PLT-02-4`: Servicio a los seis equipos: suite de aislamiento multi-tenant automatizada tabla por tabla**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 3 SP
    *   **Descripción**: Se construye una suite de pruebas automatizada de aislamiento multi-tenant transversal. Se conecta al CI para correr contra PostgreSQL real con el esquema aplicado, recorriendo todas las tablas operativas de la plataforma y validando la estricta no-fuga de datos entre inquilinos diferentes.
    *   **Criterios de Aceptación**:
        *   El test lee el catálogo `pg_policies` dinámicamente; cualquier tabla de core, vec, audit o cm sin RLS activo hace fallar el test en el CI de forma automática.
        *   Por cada tabla realiza 4 aserciones: cero filas legibles sin tenant, rechazo de escritura cruzada entre tenants, denegación de accesos directos sin rol `kallicode_app`, e inmutabilidad de logs.
    *   **Cierre de Hallazgos**: Parte transversal del hito `M1`.

*   **`KC-PLT-02-5`: Comprobación de viabilidad de pgvector iterativo en el servicio gestionado de Azure**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 1 SP
    *   **Descripción**: La búsqueda vectorial iterativa multi-tenant con buen recall depende de la versión exacta de pgvector disponible en Azure Database for PostgreSQL Flexible Server. Se audita el entorno cloud gestionado temprano para levantar un ADR y alertar a E1 y E4 de restricciones de particionado antes de S8.
    *   **Criterios de Aceptación**:
        *   Comprobación documentada y guardada como `docs/adr/003-pgvector-filtrado-por-tenant.md`.
        *   Se define si se utilizará la extensión canónica o se planifica el particionamiento por tenant como mitigación puente.

---

### SPRINT 3 – QUE LA TENENCIA Y LA SESIÓN NO SE PUEDAN BURLAR
*   **Fechas**: 17 al 21 de agosto de 2026
*   **Capacidad**: 24 Story Points
*   **Foco del Sprint**: Blindar las claves ajenas del sistema mediante el patrón de claves compuestas con `tenant_id` obligatorio para evitar inyecciones referenciales, corregir el algoritmo de verificación de la cadena de hash de auditoría para validar el encadenamiento real de hashes, y desplegar la denylist de tokens JWT sobre Redis y el logout real para cerrar las brechas bloqueantes de sesión.

#### Tickets del Sprint 3:

*   **`KC-PLT-03-1`: Servicio a los seis equipos: integridad referencial compuesta de tenant**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 8 SP
    *   **Descripción**: La base de datos tiene claves ajenas sencillas (ej: `ticket_id` de tipo integer). Si un atacante inyecta un ID de ticket de otro tenant, la base de datos lo asocia sin chistar porque RLS solo valida el tenant propio del registro hijo, permitiendo exfiltraciones cruzadas referenciales. Se fuerza que todas las claves ajenas y primarias de las tablas de datos sean compuestas e incluyan `tenant_id`.
    *   **Criterios de Aceptación**:
        *   Ninguna tabla de datos del sistema puede referenciar un padre sin incluir `tenant_id` compuesto en la FK.
        *   Un trigger de validación corre en la CI verificando que el total de relaciones huérfanas de tenant en el DDL sea cero.
        *   Prueba de integración intenta insertar un registro hijo vinculándolo a un ID padre de un tenant diferente; la base de datos debe abortar la transacción con un error referencial compuesto.
    *   **Cierre de Hallazgos**: `DB-F01` (Bloqueante).

*   **`KC-PLT-03-2`: Cadena de auditoría inmutable: verificación real del encadenamiento de hash**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: La función `audit.verify_chain` calcula de forma inútil el hash de cada registro de auditoría de forma aislada. Si un atacante elimina un registro intermedio o reordena los eventos, la verificación devuelve "verde" porque no valida que el hash actual incorpore criptográficamente el hash del evento anterior. Se reescribe la lógica en base de datos.
    *   **Criterios de Aceptación**:
        *   `audit.verify_chain` recorre los registros de forma secuencial, validando que `hash_actual = SHA256(payload + hash_anterior)`.
        *   Prueba de regresión simula la supresión de un evento de auditoría intermedio en la base de datos; la función debe devolver un fallo rotundo detectando la ruptura de la secuencia.
    *   **Cierre de Hallazgos**: `DB-F04` (Bloqueante).

*   **`KC-PLT-03-3`: El runner de workers deja de perder mensajes: confirmación en Redis Streams solo tras éxito**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: El worker original llama a `XACK` sobre Redis en el manejador global de excepciones, confirmando la lectura del mensaje incluso si la tarea de fondo explotó con un error o falló. Esto provoca pérdida de trabajos sin traza en invitaciones, restablecimientos o subidas. Se reestructura el runner para confirmar solo si la ejecución fue 100% exitosa, implementando reintentos con retraso y cola de mensajes fallidos (DLQ) en Redis.
    *   **Criterios de Aceptación**:
        *   El worker ejecuta `XACK` únicamente en el bloque `success` del try-except.
        *   Los mensajes fallidos se reintentan hasta un máximo configurable de veces y luego se mueven con `XADD` al stream de cola de mensajes fallidos `kc:dlq`.
        *   Prueba de integración inyecta un worker simulado que explota; el mensaje debe terminar en `kc:dlq` y no ser confirmado.
    *   **Cierre de Hallazgos**: `BE-F04` (Bloqueante).

*   **`KC-PLT-03-4`: Bóveda de secretos in-memory protegida y autenticada en Redis**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 3 SP
    *   **Descripción**: El prototipo de la bóveda guarda credenciales en claro en Redis sin contraseña. Se implementa el backend de cifrado con AES-GCM local sobre Redis, protegiendo las credenciales mediante contraseña de Redis configurada y limitando el alcance por tenant.
    *   **Criterios de Aceptación**:
        *   Redis configurado con contraseña segura en compose y producción.
        *   Los secretos se persisten cifrados con AES-256-GCM utilizando una llave maestra de entorno no versionada.
        *   Prueba unitaria demuestra que un dump directo de la memoria de Redis no revela credenciales de cliente legibles.
    *   **Cierre de Hallazgos**: `BE-F05` (Bloqueante).

*   **`KC-PLT-03-5`: Revocación real de JWT mediante Denylist en Redis y endpoint de Logout**
    *   **Épica**: `EPL3` (Identidad, Sesiones y RBAC)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 3 SP
    *   **Descripción**: Los tokens JWT del portal no se pueden revocar (salir de la aplicación solo limpia localmente pero el token sigue activo en el servidor hasta vencer). Se implementa el endpoint `POST /auth/logout` que registra el identificador de token `jti` en una denylist con TTL sobre Redis, y se endurece la validación de tokens.
    *   **Criterios de Aceptación**:
        *   El backend valida la denylist de `jti` en Redis ante cada petición autenticada.
        *   El endpoint `/auth/logout` escribe exitosamente el `jti` en Redis con expiración automática.
        *   Prueba unitaria intenta usar un token JWT revocado; la API responde inmediatamente con 401.
    *   **Cierre de Hallazgos**: `BE-F11` (Alto), `BE-F12` (Alto).

---

### SPRINT 4 – QUE LA EVIDENCIA AGUANTE UNA AUDITORÍA
*   **Fechas**: 24 al 28 de agosto de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Cierre técnico del Hito M2. Asegurar que el rol de aplicación pierda permisos de UPDATE/DELETE en las tablas probatorias de auditoría y calidad (con virtudes append-only estrictas), inyectar metadatos de auditoría en todos los logs de base de datos, depurar payloads de logs sensibles para no registrar secretos en texto plano, y optimizar el plan de índices de las claves compuestas para evitar recorridos secuenciales masivos.

#### Tickets del Sprint 4:

*   **`KC-PLT-04-1`: Endurecimiento de privilegios de solo anexado (append-only) en tablas probatorias**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: Aunque use RLS, el rol de base de datos de la API tiene permisos de UPDATE y DELETE en todas las tablas. Un atacante con acceso a la API podría manipular o borrar registros de firmas humanas (`core.gate_signatures`), resultados de QA (`core.qa_results`) o auditoría. Se revocan los privilegios de UPDATE/DELETE en Postgres para el rol `kallicode_app` sobre estas tablas de cumplimiento.
    *   **Criterios de Aceptación**:
        *   El rol `kallicode_app` tiene solo privilegios de SELECT e INSERT en las tablas probatorias especificadas.
        *   Prueba unitaria inicia sesión como el rol restringido e intenta ejecutar un DELETE o UPDATE; la base de datos aborta con error de privilegios insuficientes (`42501`).
    *   **Cierre de Hallazgos**: `DB-F05` (Alto).

*   **`KC-PLT-04-2`: Registro estructurado de origen y actor en la tabla de eventos de auditoría**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 3 SP
    *   **Descripción**: `audit.audit_events` no guarda la traza de red (IP de origen, User Agent) ni el ID de correlación de la petición, impidiendo saber desde dónde se firmó un gate. Se extiende la tabla, se inyectan estos campos del middleware a nivel de contexto y se sella de forma inmutable.
    *   **Criterios de Aceptación**:
        *   La tabla de auditoría incluye columnas `ip_origen`, `user_agent` y `trace_id`.
        *   Cada log de evento de negocio sella de forma correcta y obligatoria estos metadatos.
    *   **Cierre de Hallazgos**: `DB-F19` (Medio).

*   **`KC-PLT-04-3`: Depuración y censura de payloads sensibles en la tabla de auditoría**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Los logs de auditoría guardan a veces payloads JSON completos de las APIs, exfiltrando código sensible de clientes, contraseñas de usuarios en claro, o tokens JWT. Se implementa un sanitizador recursivo de payloads antes de la persistencia para filtrar claves sensibles a un formato enmascarado.
    *   **Criterios de Aceptación**:
        *   El helper de auditoría aplica un filtro restrictivo de claves prohibidas (como `password`, `token`, `secret`, `key`, `code`).
        *   Los valores de las claves prohibidas se reemplazan con el literal `[REDACTED]`.
        *   Prueba unitaria pasa un JSON con credenciales simuladas al auditor y valida que la base de datos guarde la versión sanitizada.
    *   **Cierre de Hallazgos**: `BE-F31` (Medio).

*   **`KC-PLT-04-4`: Verificación por rango de la cadena de auditoría del expediente**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Al abrir un expediente de ticket, la plataforma recalcula y verifica la cadena de hashes del inquilino desde el principio de los tiempos, lo que degrada el performance linealmente. Se rediseña el módulo para validar la integridad acotándola transaccionalmente al rango del ticket activo (desde el primer evento de creación hasta la firma final).
    *   **Criterios de Aceptación**:
        *   La validación del expediente se restringe al subconjunto de eventos del ticket, amarrando el hash inicial al evento previo del rango.
        *   El API expone el endpoint `/audit/integrity` para el barrido completo en segundo plano asíncrono.
        *   La latencia de apertura del expediente permanece constante e inferior a 300 ms en percentil 95 con volúmenes grandes.
    *   **Cierre de Hallazgos**: `BE-F32` (Medio).

*   **`KC-PLT-04-5`: Resolución inequívoca de tenant en Login y protección tras proxies de confianza**
    *   **Épica**: `EPL3` (Identidad, Sesiones y RBAC)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: El login busca usuarios de forma global por correo; si un mismo correo existe en dos organizaciones (un staff externo de soporte), el login es aleatorio e imprevisible. Además, la IP del cliente se lee directo del socket sin validar los encabezados `X-Forwarded-For` de los proxies de confianza (Azure Ingress), lo que permite saltarse rate limits y bloqueos de seguridad exfiltrando IPs.
    *   **Criterios de Aceptación**:
        *   El flujo de Login exige indicar el tenant explícitamente (vía subdominio, payload de organización o selección limpia) antes de validar contraseña.
        *   La IP se lee de forma segura validando solo proxies de confianza declarados en configuración (`KC_TRUSTED_PROXIES`).
        *   El rate limit y el bloqueo por fallos se aplican a la IP real del cliente y no al balanceador.
    *   **Cierre de Hallazgos**: `BE-F13` (Alto), `BE-F14` (Alto).

*   **`KC-PLT-04-6`: Optimización del plan de índices de las claves compuestas y claves ajenas**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: 38 de las 80 claves ajenas compuestas creadas para el aislamiento carecen de índices Postgres. Al borrar un usuario o ticket, se disparan recorridos secuenciales completos (`Seq Scan`) en tablas transaccionales bloqueando la base de datos. Se indexan todas las FKs respetando la convención multi-tenant: el primer campo de todo índice compuesto debe ser `tenant_id`.
    *   **Criterios de Aceptación**:
        *   Todas las claves ajenas compuestas del esquema Postgres están cubiertas por un índice equivalente.
        *   El linter de base de datos valida que ningún índice empiece por campos transaccionales antes del `tenant_id`.
        *   Los deletes de registros no generan Seq Scans en cascada en Postgres.
    *   **Cierre de Hallazgos**: `DB-F06` (Alto), `DB-F26` (Medio).

*   **`KC-PLT-04-7`: Endurecimiento de la política de contraseñas de plataforma (Tope, Argon2 y Rehash)**
    *   **Épica**: `EPL3` (Identidad, Sesiones y RBAC)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 2 SP
    *   **Descripción**: Se endurece la política de contraseñas de la plataforma. Se implementa un tope estricto de longitud de entrada para evitar ataques de denegación de servicio por cálculo pesado de hash, se fuerza el hashing robusto con `Argon2id` en lugar de bcrypt viejo, y se implementa la comprobación de rehash si cambian los parámetros de seguridad.
    *   **Criterios de Aceptación**:
        *   Longitud de contraseña validada en frontera (máx 128 caracteres para evitar DoS de hash).
        *   Fuerza mínima obligatoria de 12 caracteres, mezclando mayúsculas, números y símbolos.
        *   Hashing persistido mediante `Argon2id` con rehash proactivo en login.
    *   **Cierre de Hallazgos**: `BE-F33` (Medio).

---

### SPRINT 5 – QUE LOS SECRETOS Y LOS FICHEROS SEAN REALES
*   **Fechas**: 31 de agosto al 4 de septiembre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Integrar el backend real de secretos con **Azure Key Vault** y Managed Identity para cerrar la brecha de secretos de producción, desplegar las firmas SAS de corta duración reales contra **Azure Blob Storage** para habilitar la subida y descarga de evidencias de calidad, endurecer el Ingress de red mediante cabeceras rígidas y límites de cuerpo (CORS strict), y orquestar el middleware de logging centralizado con trace de correlación.

#### Tickets del Sprint 5:

*   **`KC-PLT-05-1`: Migración de secretos de Redis a la bóveda real Azure Key Vault**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 8 SP
    *   **Descripción**: El backend de secretos lee en claro o desde variables de Redis sin autenticar. Se migra el motor para integrar de forma real **Azure Key Vault** en producción/staging utilizando Managed Identity de Azure, conservando la firma de la interfaz local del Sprint 3 para desarrollo.
    *   **Criterios de Aceptación**:
        *   Los secretos trans transaccionados en la API se resuelven en Key Vault por tenant.
        *   Autenticación de plataforma mediante Managed Identity (identidad administrada sin secretos estáticos en configuración).
        *   Prueba en staging demuestra lectura exitosa de secretos de Key Vault con fallback local documentado.
    *   **Cierre de Hallazgos**: `BE-F05` (Bloqueante - Cierre de producción).

*   **`KC-PLT-05-2`: Servicio a E1 y E3: almacenamiento de objetos Azure Blob real con firmas SAS**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: El generador de evidencias y adjuntos usa una función local que firma URLs de descarga utilizando una clave local estática hardcodeada de 32 caracteres que no valida Azure. Se implementa el servicio real en `servicios/blob.py` que genera URLs firmadas con SAS (Shared Access Signature) de corta duración (máx. 15 minutos) contra Azure Blob real.
    *   **Criterios de Aceptación**:
        *   Subidas y descargas resueltas mediante Azure Blob Storage real (con Azurite como doble local en compose).
        *   La URL firmada SAS expira exactamente a los 15 minutos cronometrados y se genera bajo validación de sesión de tenant.
        *   Prueba unitaria demuestra que intentar descargar con una URL SAS vencida responde con error de acceso denegado de Azure.
    *   **Cierre de Hallazgos**: `BE-F20` (Alto - Mitad transversal de E5).

*   **`KC-PLT-05-3`: Exportación de auditoría inmutable firmada contra Azure Blob**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: El worker de exportación de auditorías marca el expediente como "listo" pero escribe un payload vacío en disco sin subir nada, provocando que el portal descargue un archivo fantasma. Se reescribe el worker para generar el CSV del log, calcular su hash, subir el archivo cifrado a Azure Blob e inmutabilizarlo.
    *   **Criterios de Aceptación**:
        *   El worker genera y sube el archivo real de exportación respetando el filtro por ticket o rango de fechas.
        *   La descarga del reporte se firma con SAS y valida el hash SHA-256 del contenido contra el registrado.
        *   Filtros asíncronos para rangos superiores a 31 días implementados sin colgar el worker.
    *   **Cierre de Hallazgos**: `BE-F19` (Alto).

*   **`KC-PLT-05-4`: Endurecimiento HTTP: cabeceras de seguridad, CORS restrictivo y límite global de cuerpo**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 3 SP
    *   **Descripción**: El middleware de CORS permite de forma insegura cualquier origen, método o cabecera con credenciales. No hay HSTS, CSP, límites de tamaño ni protección contra clickjacking. Se endurece el middleware FastAPI forzando cabeceras CORS estrictas vinculadas al subdominio, HSTS en producción y un límite estricto de cuerpo de petición (máx 50MB transacciones, 1MB webhooks).
    *   **Criterios de Aceptación**:
        *   El CORS rechaza orígenes comodín `*` cuando se solicitan con credenciales.
        *   Inyección de cabeceras de seguridad: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` y `Content-Security-Policy`.
        *   La API responde con error `413 Payload Too Large` ante cuerpos mayores al límite configurado.
    *   **Cierre de Hallazgos**: `BE-F25` (Alto).

*   **`KC-PLT-05-5`: Servicio a los seis equipos: middleware de logging centralizado estructurado**
    *   **Épica**: `EPL2` (Núcleo, Configuración y Empaquetado)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Los logs de la aplicación no tienen correlación, ya que el trace_id o tenant_id solo viven en variables locales del manejador sin propagarse. Se construye un middleware FastAPI que inyecta en el contexto de hilo (`ContextVar`) el `trace_id` (UUID), `tenant_id` y `actor_id` en todas las colas, llamadas y routers.
    *   **Criterios de Aceptación**:
        *   Toda línea de log emitida en la consola o archivo sigue el formato JSON estructurado conteniendo trace_id, tenant_id y actor.
        *   El sobre de error devuelto por la API inyecta el `trace_id` correcto de la petición para auditoría.
    *   **Cierre de Hallazgos**: `BE-F26` (Medio), `BE-F27` (Medio).

*   **`KC-PLT-05-6`: Sondas de salud realistas (PostgreSQL y Redis) y cierre ordenado de ciclo de vida**
    *   **Épica**: `EPL2` (Núcleo, Configuración y Empaquetado)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 2 SP
    *   **Descripción**: La sonda `/health/ready` devuelve `200` plano a Azure Container Apps aunque la base de datos PostgreSQL esté caída. Se reescribe la sonda para ejecutar consultas dummy reales contra la BD y Redis antes de responder, e implementar el cierre ordenado de conexiones (`lifespan` de FastAPI) para evitar fugas en redespliegues.
    *   **Criterios de Aceptación**:
        *   `/health/ready` responde `503 Service Unavailable` si PostgreSQL o Redis fallan, detallando la dependencia caída.
        *   El apagado de la aplicación corre de forma controlada (`lifespan`) cerrando el pool de SQLAlchemy y las conexiones Redis de inmediato.
    *   **Cierre de Hallazgos**: `BE-F29` (Medio), `BE-F37` (Bajo).

---

### SPRINT 6 – QUE NINGÚN MENSAJE NI NINGUNA COLA SE PIERDA
*   **Fechas**: 7 al 11 de septiembre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Consolidar la robustez asíncrona de los workers desplegando el reclamo de mensajes huérfanos con `XAUTOCLAIM` para réplicas caídas, asegurar el aislamiento multi-tenant de las colas mediante políticas de inquilino para los workers, aislar la API interna (`/internal/v1`) en red privada virtual (Azure VNet) para no exponerla a internet, y provisionar el sandbox de confinamiento de código QA.

#### Tickets del Sprint 6:

*   **`KC-PLT-06-1`: Reclamo de mensajes huérfanos con XAUTOCLAIM y líder de tareas programadas**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 8 SP
    *   **Descripción**: Los mensajes leídos y no confirmados por una réplica que muere quedan perdidos en el limbo de la cola. Se implementa una tarea periódica en el worker que utiliza `XAUTOCLAIM` con un `min-idle-time` de 60 segundos para recuperar y reasignar mensajes huérfanos, y se despliega un bloqueo distribuido en Redis para que las tareas recurrentes corran exactamente en una sola réplica (líder).
    *   **Criterios de Aceptación**:
        *   Los mensajes de Redis Streams huérfanos se reclaman automáticamente por otras réplicas sanas tras un minuto inactivas.
        *   Las tareas programadas (como la renovación de ciclos) se ejecutan solo por el líder que sostiene el bloqueo de Redis.
        *   Prueba simula la muerte del worker en pleno procesamiento; otra réplica debe tomar y terminar la tarea de forma transparente.
    *   **Cierre de Hallazgos**: Continuación y cierre funcional de `BE-F04`.

*   **`KC-PLT-06-2`: Rol de worker y aislamiento multi-tenant de colas cross-tenant**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: Las tareas asíncronas transversales (workers, purgas) deben barrer múltiples tenants a la vez, pero las políticas RLS de PostgreSQL les bloquean la lectura de colas, ya que carecen de un rol con bypass seguro. Se crea el rol `kallicode_worker` en Postgres con políticas RLS específicas que habilitan la lectura solo de colas pendientes respetando el aislamiento.
    *   **Criterios de Aceptación**:
        *   Creación y despliegue del rol `kallicode_worker` con políticas RLS para tareas de fondo.
        *   El worker lee de forma masiva colas pendientes, pero al transicionar el contexto de un job específico, se auto-restringe al `tenant_id` del job en sesión de base de datos de forma obligatoria.
    *   **Cierre de Hallazgos**: `DB-F11` (Alto).

*   **`KC-PLT-06-3`: API interna /internal/v1 en red privada aislada**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: La API interna de la plataforma que expone las transiciones de estado, subidas de evidencia y firmas cuelga del mismo ingress público que el portal cliente. Se reestructura la infraestructura en Azure para montar `/internal/v1` en un puerto y contenedor separado accesible únicamente a través de la red virtual privada (VNet) y prohibido para internet.
    *   **Criterios de Aceptación**:
        *   La API interna corre en un sub-dominio y puerto privado aislados.
        *   El ingress público rechaza con un 404/403 cualquier intento de llamada externa a `/internal/v1`.
        *   Prueba de integración demuestra que solo las Container Apps del monorepo dentro de la red privada pueden llamar a los endpoints internos.
    *   **Cierre de Hallazgos**: `BE-F22` (Alto).

*   **`KC-PLT-06-4`: Helper de actualización de base de datos con lista explícita de campos (allowlist)**
    *   **Épica**: `EPL2` (Núcleo, Configuración y Empaquetado)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 3 SP
    *   **Descripción**: Varios endpoints actualizan registros interpolando de forma dinámica los campos JSON enviados en el cuerpo, abriendo el riesgo de inyección de parámetros. Se desarrolla un helper de actualización relacional SQLAlchemy centralizado en `packages/core` que restringe el guardado a una allowlist explícita por tabla.
    *   **Criterios de Aceptación**:
        *   Las actualizaciones de tickets, organizaciones y usuarios pasan obligatoriamente por el helper de allowlist.
        *   Intentar actualizar un campo no permitido (ej. plan de la organización, rol de usuario) es rechazado con error `422 Unprocessable Entity` o se depura de forma segura.
    *   **Cierre de Hallazgos**: `BE-F36` (Medio).

*   **`KC-PLT-06-5`: Retención y purgado con TTL de las claves de idempotencia**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: La tabla de claves de idempotencia del backend guarda el payload JSON completo memorizado de respuestas previas para evitar duplicados, pero las filas no tienen expiración y crecen infinitamente. Se migra el modelo para forzar un TTL (Time-To-Live) de 24 horas y purgas recurrentes automáticas.
    *   **Criterios de Aceptación**:
        *   La tabla `idempotency_keys` implementa un ciclo de vida con eliminación de registros con antigüedad superior a 24 horas.
        *   La tarea programada de purga corre de forma automática vía `pg_cron` en PostgreSQL.
        *   Prueba unitaria demuestra que el consumo de disco por idempotencias permanece acotado y estable bajo carga.
    *   **Cierre de Hallazgos**: `DB-F20` (Medio).

*   **`KC-PLT-06-6`: Saneamiento de las semillas de base de datos locales (seeds/dev.sql)**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 2 SP
    *   **Descripción**: El archivo `dev.sql` que inicializa el entorno local de desarrollo comparte la misma contraseña y hash bcrypt entre tenants y expone contraseñas en claro. Se corrige el script para generar contraseñas con salts deterministas seguras por tenant y se inyecta una guarda de entorno que impide su ejecución en entornos de staging o producción.
    *   **Criterios de Aceptación**:
        *   `dev.sql` usa hashes seguros individuales generados con sales dinámicas.
        *   El script arroja error y aborta inmediatamente si detecta que la variable de entorno es de producción.
    *   **Cierre de Hallazgos**: `DB-F24` (Medio).

---

### SPRINT 7 – QUE LA BASE DE DATOS AGUANTE EL VOLUMEN
*   **Fechas**: 14 al 18 de septiembre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Rediseñar la clave primaria de las tablas transaccionales de alto volumen (`audit_events`, `job_transitions`, `notifications`, etc.) para incluir el timestamp y pre-crear sus particiones mensuales por rango, aplicar las restricciones CHECKS en PostgreSQL para blindar la integridad del dominio, y desplegar la ruta de borrado físico compatible con GDPR.

#### Tickets del Sprint 7:

*   **`KC-PLT-07-1`: Particionado mensual por rango de las cinco tablas de alto volumen**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 8 SP
    *   **Descripción**: Las tablas operacionales (`audit.audit_events`, `core.job_transitions`, `core.usage_tokens`, `core.notifications` y `core.webhook_inbox`) crecen infinitamente. Su clave primaria simple impide el particionado en Postgres. Se modifica el esquema para usar claves primarias compuestas conteniendo el timestamp de creación, y se configuran particiones mensuales administradas por `pg_partman`.
    *   **Criterios de Aceptación**:
        *   Esquema de base de datos migrado y particionado de forma nativa por rangos de fecha mensuales.
        *   `pg_partman` pre-crea automáticamente las particiones del mes siguiente.
        *   Prueba demuestra que los inserts de datos del mes en curso caen en la partición adecuada y no en default.
        *   Ensayo de migración sobre volumen sintético de un millón de registros se ejecuta sin exceder el tiempo límite de bloqueo del gremio de datos.
    *   **Cierre de Hallazgos**: `DB-F09` (Alto - Excepción técnica justificada y planificada).

*   **`KC-PLT-07-2`: Plan de índices transaccionales optimizados para el pipeline (E2, E3 y E4)**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: Una vez indexadas las FKs, se acuerda el plan definitivo de índices para responder a las consultas relacionales reales de la Fábrica, la matriz de QA y el Kanban del Portal. Se definen e implementan índices optimizados (ej. índices btree compuestos y funcionales) analizando latencias en el gremio de datos.
    *   **Criterios de Aceptación**:
        *   Índices críticos desplegados e incorporados al DDL canónico de base de datos.
        *   Se publica el reporte de planes de consulta Postgres (`EXPLAIN ANALYZE`) demostrando que las consultas transaccionales de volumen usan los índices y evitan lecturas secuenciales.
        *   Presupuesto de latencia por endpoint de consulta acordado con los Tech Leads.

*   **`KC-PLT-07-3`: Implementación de restricciones CHECK de dominio e integridad relacional**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: La base de datos permite guardar estados de negocio lógicamente inválidos (especificaciones con versión negativa, corridas de QA negativas, tickets cancelados sin motivo, o suscripciones que terminan antes de iniciar). Se implementa un barrido transaccional inyectando restricciones `CHECK` en todas las columnas transaccionales del esquema.
    *   **Criterios de Aceptación**:
        *   Se aplican restricciones `CHECK` para forzar monotonicidad positiva en versiones y ordinales.
        *   `motivo_cancelacion` es estrictamente requerido si la etapa es `cancelado`.
        *   Prueba de integración intenta insertar estados inválidos y verifica que la transacción sea abortada por Postgres.
    *   **Cierre de Hallazgos**: `DB-F25` (Medio).

*   **`KC-PLT-07-4`: Extensiones Postgres con esquema explícito y consolidación de enumerados**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Las extensiones de la base de datos (pgvector, uuid-ossp, pg_trgm) se inicializan sin un esquema explícito, dependiendo del `search_path` de la sesión del usuario. Además, hay CHECKs de enumeraciones duplicados que divergen. Se reescribe la instalación forzando esquemas fijos (`CREATE EXTENSION ... WITH SCHEMA pg_catalog`) y se unifican las enumeraciones en tipos Postgres comunes.
    *   **Criterios de Aceptación**:
        *   La extensión vectorial pgvector se asocia estrictamente al esquema `public` o `pg_catalog`.
        *   Enumeraciones duplicadas de roles, etapas y veredictos unificadas bajo tipos estructurados en base de datos.
    *   **Cierre de Hallazgos**: `DB-F27` (Bajo), `DB-F28` (Bajo).

*   **`KC-PLT-07-5`: Ruta de supresión física (GDPR) y borrado lógico coherente**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: El sistema usa borrado lógico (`eliminado_en`) pero las políticas RLS y los índices lo omiten en la práctica (devolviendo datos lógicamente eliminados). Además, las referencias FK bloquean la eliminación física de un inquilino, impidiendo cumplir solicitudes legales de borrado de datos. Se corrigen las FKs con cascada controlada y se endurece el filtrado del borrado lógico.
    *   **Criterios de Aceptación**:
        *   Las consultas operacionales y las políticas RLS filtran por default los registros con `eliminado_en IS NOT NULL`.
        *   Script de supresión física (GDPR) probado en base de datos de test, eliminando registros de inquilinos en cascada y conservando sólo lo estrictamente exigido por la retención legal de auditoría.
        *   El Product Owner ratifica la política de supresión física de la plataforma.
    *   **Cierre de Hallazgos**: `DB-F30` (Bajo).

*   **`KC-PLT-07-6`: Índices GIN sobre datos JSONB de alto uso**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 2 SP
    *   **Descripción**: La columna `datos` de `audit_events` y otras columnas JSONB operacionales se consultan sin índices de ruta rápida, provocando Seq Scans lentos al filtrar claves internas. Se despliegan índices GIN (`jsonb_path_ops`) sobre las claves JSON consolidadas en el inventario.
    *   **Criterios de Aceptación**:
        *   Despliegue de índices GIN Postgres sobre las columnas JSONB altamente consultadas.
        *   El plan de consulta de las búsquedas internas JSONB demuestra el uso de index scans y evita Seq Scans en Postgres.
    *   **Cierre de Hallazgos**: `DB-F29` (Bajo).

---

### SPRINT 8 – QUE LA BÚSQUEDA VECTORIAL Y LA EXPORTACIÓN ESCALEN
*   **Fechas**: 21 al 25 de septiembre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Resolver la pérdida de recall de pgvector multi-tenant activando la búsqueda iterativa o particionado por tenant para el Hito M4, evaluar y desplegar la cuantización de vectores (`halfvec`) para optimizar el almacenamiento, construir la retención inmutable y el sellado de la exportación de auditoría en almacenamiento WORM, y proveer entornos efímeros de QA para el Equipo 3.

#### Tickets del Sprint 8:

*   **`KC-PLT-08-1`: Búsqueda vectorial pgvector multi-tenant con recall garantizado y HNSW concurrentes**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 8 SP
    *   **Descripción**: Con RLS activo filtrando por tenant, la búsqueda HNSW realiza post-filtrado: el índice HNSW recupera los k vecinos globales de todos los clientes y luego RLS descarta los que no pertenecen al inquilino activo, devolviendo menos vecinos de los solicitados o ninguno (Recall decrece a cero). Se configura la búsqueda iterativa pgvector o el particionado por tenant de las tablas vectoriales con un índice local HNSW por partición.
    *   **Criterios de Aceptación**:
        *   La búsqueda de vectores devuelve el 100% de los vecinos solicitados del tenant activo (recall = 1.0) sin pérdida.
        *   Los índices HNSW se crean concurrentemente (`CREATE INDEX CONCURRENTLY`) fuera de bloques de transacción.
        *   Prueba unitaria demuestra que la búsqueda entre dos tenants con vectores idénticos devuelve resultados limpios y aislados.
    *   **Cierre de Hallazgos**: `DB-F10` (Alto - Excepción técnica justificada y planificada).

*   **`KC-PLT-08-2`: Servicio a E3: aprovisionamiento automático de entornos de prueba efímeros por job**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 8 SP
    *   **Descripción**: El motor de QA del Equipo 3 corre pruebas contra URLs y bases de datos compartidas locales, arriesgando colisiones y contaminación. Se construye la infraestructura Azure Bicep para levantar un entorno efímero por cada corrida de job de QA, aprovisionando una base de datos PostgreSQL aislada con RLS y Azurite efímero.
    *   **Criterios de Aceptación**:
        *   Los entornos efímeros se despliegan automáticamente en Azure Container Apps al arrancar el job de QA.
        *   Cada entorno efímero lleva su identificador único en sesión y se destruye físicamente de forma garantizada al terminar el ciclo de QA.
        *   El flujo móvil y web corre aislado de internet con red cerrada.

*   **`KC-PLT-08-3`: Exportación de auditoría con retención inmutable (WORM) y verificación de integridad**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: Las evidencias e informes exportados se suben a Azure Blob plano donde pueden borrarse. Se configura el versionado del contenedor de Azure Blob Storage y se activa el bloqueo de objetos en modo cumplimiento (WORM - Write Once, Read Many), garantizando la inmutabilidad legal de las evidencias de cumplimiento del portal.
    *   **Criterios de Aceptación**:
        *   El contenedor Azure Blob de evidencias probatorias tiene activado el Object Lock en modo cumplimiento (Retention configurada a un año).
        *   Intentar sobrescribir o borrar un CSV de auditoría o screenshot de QA subido es bloqueado a nivel de Azure API.
        *   La firma SAS de descarga verifica el hash SHA-256 inmutable del expediente.

*   **`KC-PLT-08-4`: Cuantización de vectores y presupuesto de almacenamiento pgvector**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Los embeddings de CodeMapping se guardan en float de 4 bytes por dimensión, consumiendo excesiva memoria RAM de Postgres. Se analiza el recall vectorial antes y después del uso de precisión media (`halfvec` introducido en pgvector 0.5) y se decide el despliegue del almacenamiento comprimido.
    *   **Criterios de Aceptación**:
        *   Se evalúa la precisión y recall de `halfvec` contra la base de referencia publicando el reporte de recall en `docs/adr/004-cuantizacion-vectores.md`.
        *   Si se aprueba, se despliega la migración relacional de conversión en base de datos.
    *   **Cierre de Hallazgos**: `DB-F31` (Bajo).

*   **`KC-PLT-08-5`: Identificadores ULID estrictamente monótonos dentro del mismo milisegundo**
    *   **Épica**: `EPL2` (Núcleo, Configuración y Empaquetado)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 2 SP
    *   **Descripción**: El generador de IDs (ULID) de la plataforma carece de monotonicidad estricta para IDs generados en el mismo milisegundo por hilos paralelos, provocando que la ordenación temporal de los registros de la cadena de auditoría sea aleatoria. Se corrige el helper para usar el generador monótono de ULID.
    *   **Criterios de Aceptación**:
        *   `packages/core/src/kallicode_core/ids.py` implementa el factory monótono `new_monotonic_ulid`.
        *   Prueba unitaria genera 100,000 IDs concurrentes en el mismo milisegundo y comprueba que la secuencia resultante es estrictamente creciente lexicográficamente.
    *   **Cierre de Hallazgos**: `BE-F40` (Bajo).

---

### SPRINT 9 – QUE LOS DATOS NO CREZCAN SIN CONTROL
*   **Fechas**: 28 de septiembre al 2 de octubre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Cierre del último hallazgo heredado de auditoría. Desplegar la lógica de retención y purgas periódicas automáticas de datos obsoletos mediante `pg_cron` (Postgres partitions drop), construir el flujo de archivamiento inmutable de auditoría previo al descarte, y aprovisionar el sandbox de ejecución de Playwright súper-confinado y seguro para el Equipo 3.

#### Tickets del Sprint 9:

*   **`KC-PLT-09-1`: Retención y purgas periódicas de base de datos con pg_cron**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 8 SP
    *   **Descripción**: La base de datos no tiene implementadas políticas de retención ni purgas, lo que satura el espacio. Se crea la tabla de configuración `core.retention_policies` y se despliegan funciones SQL automatizadas en Postgres que descartan particiones viejas o borran registros caducos de tokens, webhooks, notificaciones y logs de idempotencia de forma periódica.
    *   **Criterios de Aceptación**:
        *   Los descartes de registros obsoletos se ejecutan de forma automática programados con `pg_cron` o `pg_partman`.
        *   Prueba simula el paso del tiempo y valida que las particiones vencidas se descarten limpiamente liberando espacio en disco de inmediato.
        *   Se publica el runbook de retención detallando cómo pausar el purgado si se abre un ticket legal de retención de datos.
    *   **Cierre de Hallazgos**: `DB-F23` (Medio).

*   **`KC-PLT-09-2`: Archivamiento inmutable de auditorías previo al descarte de particiones**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: Para liberar espacio de Postgres, se descartan las particiones viejas del esquema `audit`. Sin embargo, borrar un log transaccional de cumplimiento rompe la inmutabilidad si no se conserva en otro lado. Se implementa el proceso que empaqueta la partición antes de borrarla, calcula su hash SHA-256, sella el hash en la cabecera de la cadena y lo sube al Blob Storage WORM.
    *   **Criterios de Aceptación**:
        *   El worker de purgas archiva la partición en Azure Blob de forma WORM antes de destruirla transaccionalmente en base de datos.
        *   La verificación de la cadena de auditoría en el CI sigue en verde validando el hash de los tramos históricos archivados en Blob Storage.
        *   Prueba demuestra que es imposible descartar una partición transaccional sin que su CSV consolidado esté verificado arriba.

*   **`KC-PLT-09-3`: Servicio a E3: sandbox de ejecución de pruebas TypeScript súper-confinado**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: La etapa de QA del Equipo 3 corre código TypeScript autogenerado por la IA. Si el modelo es inyectado con prompts maliciosos, podría exfiltrar secretos de plataforma (DSN, Minio pass) u hostear malware. Se construye el sandbox de ejecución seguro en Azure Container Apps: contenedor efímero, sin red saliente, sin secretos en las variables de entorno, y con un token defixtures efímero.
    *   **Criterios de Aceptación**:
        *   El sandbox se monta en un contenedor ultra-restringido sin acceso a red saliente salvo el host seguro permitido del tenant.
        *   El proceso hereda variables de entorno vacías; no puede leer credenciales master de la base de datos ni bóvedas.
        *   Prueba adversarial con 40 exploits simulados (child_process, exfiltración, fetch, path traversal) demuestra que el sandbox los bloquea y mata el proceso de inmediato de forma controlada.

*   **`KC-PLT-09-4`: Generador de volumen sintético y andamiaje para pruebas de carga**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: Para asegurar que las pruebas de carga del Sprint 11 sean realistas y no teóricas, se construye un script generador de datos sintéticos que puebla la base con volúmenes masivos equivalentes a un cliente enterprise real (más de un millón de eventos transaccionales, mil tickets, y millones de nodos en cm). Se configuran los escenarios de prueba k6 iniciales.
    *   **Criterios de Aceptación**:
        *   El script `volumen_sintetico.py` puebla de forma reproducible y con semillas la base Postgres local en menos de 10 minutos.
        *   Andamiaje de k6 configurado en `/tests/carga/` con endpoints críticos seleccionados (Tablero, Kanban, Evidencias, API interna).
        *   Criterio de tiempo y objetivos de recuperación DR firmados por el Product Owner.

*   **`KC-PLT-09-5`: Servicio a E3: imagen de runner de QA unificada y publicada**
    *   **Épica**: `EPL1` (CI/CD, Entornos e Infraestructura)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 3 SP
    *   **Descripción**: El motor de pruebas necesita navegadores de Playwright, binarios de node y emuladores Maestro que tardan en instalarse en cada ejecución. Se pre-construye y publica la imagen Docker del runner de QA en Azure Container Registry con su cadena de suministro firmada, escaneada por Trivy sin vulnerabilidades altas.
    *   **Criterios de Aceptación**:
        *   Imagen del runner pre-construida y publicada en el ACR corporativo.
        *   El tiempo de arranque e inicialización del runner efímero del pipeline QA de E3 baja de 30 segundos cronometrados.

---

### SPRINT 10 – QUE SE PUEDA VER QUÉ ESTÁ PASANDO (CONTRIBUCIÓN A M5)
*   **Fechas**: 5 al 9 de octubre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Desplegar la instrumentación de observabilidad de la plataforma correlacionando peticiones con `trace_id` en Azure Monitor, configurar alertas inteligentes de seguridad y operativas, proveer el pool analítico separado de base de datos para no colapsar el portal con consultas recursivas del Equipo 1, e implementar el rol de acceso cross-tenant auditado para el Equipo 6.

#### Tickets del Sprint 10:

*   **`KC-PLT-10-1`: Métricas expuestas de plataforma e instrumentación de telemetría en Azure Monitor**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 8 SP
    *   **Descripción**: La plataforma carece de monitoreo en vivo; diagnosticar caídas exige leer logs planos. Se implementa un router de métricas FastAPI que expone series transaccionales Prometheus (`/metrics`) y se instrumenta la telemetría unificada (`trace_id` propagado) hacia Azure Monitor App Insights.
    *   **Criterios de Aceptación**:
        *   Métricas en vivo expuestas: latencia, tasa de errores por endpoint, mensajes en cola de Redis, y costos proyectados por tokens LLM consumidos.
        *   Azure Monitor correlaciona llamadas de la API, logs de workers y llamadas internas con el mismo `trace_id` de auditoría.
        *   Muestreo configurado para evitar costos excesivos de ingesta en telemetría cloud.

*   **`KC-PLT-10-2`: Configuración de paneles de salud de plataforma y alertas accionables**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: Se construyen los dashboards de control de salud en Azure Portal para el soporte del cliente, y se configuran alertas de umbral críticas. Cada alerta configurada debe enlazar obligatoriamente un runbook de resolución en su payload de notificación.
    *   **Criterios de Aceptación**:
        *   Paneles funcionales en Azure Portal mostrando: CPU/RAM Container Apps, latencia Postgres, errores de API (5xx) y streams caídos.
        *   Alertas de seguridad dedicadas (ej. fallas en la integridad de la cadena de hash, inyección de prompt confirmada, accesos denegados excesivos).
        *   Prueba simula falla y verifica que la notificación llegue al canal Slack/Teams con el enlace a su runbook de operación.

*   **`KC-PLT-10-3`: Pool analítico e hilos de consulta de Postgres separados de la base operacional**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: Las consultas recursivas del grafo de CodeMapping consumen CPU de forma masiva compitiendo con las transiciones rápidas de la API y el portal cliente. Se separa la arquitectura de base de datos creando un pool de conexiones analítico Postgres de solo lectura, con límites de timeout estrictos y rol propio.
    *   **Criterios de Aceptación**:
        *   Las lecturas complejas de grafo (E1) y búsquedas vectoriales corren sobre el pool analítico de solo lectura Postgres.
        *   Las peticiones del portal cliente conservan el pool de escritura operacional rápido de 10 conexiones.
        *   Prueba de carga demuestra que saturar con consultas pesadas recursivas el pool analítico no incrementa la latencia de login de la API.

*   **`KC-PLT-10-4`: Servicio a E6: Rol de acceso cross-tenant auditado y actor de personal**
    *   **Épica**: `EPL3` (Identidad, Sesiones y RBAC)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: La consola de administración de E6 necesita leer métricas consolidadas entre clientes, pero RLS restringe las consultas a un solo inquilino. Se crea un rol relacional Postgres `kallicode_staff` con políticas RLS condicionales que habilitan la lectura cross-tenant de métricas, y se extiende la cadena de auditoría para sellar el acceso de personal con justificación obligatoria.
    *   **Criterios de Aceptación**:
        *   `kallicode_staff` lee métricas globales cruzadas pero no puede modificar datos de clientes (espectáculo de sólo lectura).
        *   Cada acceso cross-tenant genera un evento `staff_concesion` de auditoría inmutable sellando el motivo declarado y trace_id.
        *   El cliente puede ver en su propio visor de auditoría qué personal de soporte de KalliCode vio sus datos y con qué motivo.

*   **`KC-PLT-10-5`: Consolidación de runbooks de operación de la plataforma y ADRs**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Para mitigar el "factor de autobús" de 1 por disciplina, se consolidan todos los runbooks de infraestructura y base de datos construidos en los sprints anteriores en un índice canónico y auditable de operaciones de plataforma.
    *   **Criterios de Aceptación**:
        *   Estructura en `/docs/operacion/README.md` indexando runbooks de colas, boves, particionado, Key Vault y recuperaciones.
        *   Cada runbook es probado y ejecutado por un miembro del equipo diferente a quien lo redactó.
        *   ADRs técnicos del backend consolidados en `/docs/adr/`.

---

### SPRINT 11 – QUE SE PUEDA RECUPERAR Y QUE RESISTA LA CARGA
*   **Fechas**: 12 al 16 de octubre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Ejecutar el simulacro de recuperación ante desastres (DR) cronometrando tiempos de restauración frente a los objetivos, correr las pruebas de carga con k6 sobre el volumen sintético para optimizar cuellos de botella con RLS, realizar la auditoría final de seguridad del modelo de amenazas, y cerrar la deuda técnica de los 48 hallazgos.

#### Tickets del Sprint 11:

*   **`KC-PLT-11-1`: Ensayo real de recuperación ante desastres (Disaster Recovery) con tiempo medido**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 8 SP
    *   **Descripción**: No existen simulacros de recuperación de la plataforma; confiar en backups automáticos sin medirlos es un riesgo crítico de compliance. Se ejecuta una simulación real de desastre en staging, destruyendo base de datos y contenedores de prueba, y se cronometra el tiempo real de restauración de extremo a extremo frente a los objetivos de RPO/RTO.
    *   **Criterios de Aceptación**:
        *   Se ejecuta la restauración completa del sistema desde backups cifrados e inmutables usando el runbook de DR.
        *   El tiempo de restauración es menor a 2 horas (RTO) y la pérdida de datos es cero (RPO), documentado formalmente en un acta de DR.
        *   El ensayo es ejecutado por un miembro de E5 sin permisos de superusuario en Azure.

*   **`KC-PLT-11-2`: Pruebas de carga k6 de la plataforma con Row-Level Security activo**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: Postgres con pgvector y RLS se comporta diferente bajo carga de concurrencia masiva. Se corren los escenarios de k6 con volumen sintético de un millón de registros para medir p95 de latencias en endpoints críticos (Tablero, Kanban, API interna) y optimizar cuellos de botella.
    *   **Criterios de Aceptación**:
        *   Pruebas de carga de k6 corren de forma automática en el CI nocturno.
        *   La latencia p95 de las peticiones en concurrencia masiva cumple con el presupuesto del Sprint 7 (ej. portal login < 200 ms, expediente < 300 ms).
        *   Se verifica que Postgres no recurra a `Seq Scan` bajo carga analizando la cola de queries lentas.

*   **`KC-PLT-11-3`: Pasada de seguridad final y endurecimiento de la superficie frente a modelo de amenazas**
    *   **Épica**: `EPL6` (Bóveda, Almacenamiento y Endurecimiento)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: Se realiza una auditoría interna de seguridad final de la superficie de la plataforma construida (sandbox, Key Vault, Ingress, cookies HttpOnly, denylist). Se actualiza el modelo de amenazas de KalliCode y se validan defensas de OWASP Top 10.
    *   **Criterios de Aceptación**:
        *   Modelo de amenazas de la plataforma actualizado y documentado en `docs/seguridad/modelo-amenazas.md`.
        *   Escaneo final de dependencias Docker y código por Trivy/Semgrep muestra cero vulnerabilidades altas o críticas abiertas sin mitigación aprobada.
        *   Los tests adversarios del sandbox siguen pasando en el build de QA.

*   **`KC-PLT-11-4`: Optimización del performance de recorridos de grafo recursivos (D18)**
    *   **Épica**: `EPL7` (Rendimiento, Retención y Recuperación)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: El cambio de Neo4j a Postgres (D18) exige que las consultas recursivas a cm sean rápidas en SQL. Se audita el performance de las CTEs recursivas de la capa de consulta de E1 bajo volumetría masiva, y se despliega la tabla de cierre transitivo materializada para optimizar latencias en el ciclo de ingesta.
    *   **Criterios de Aceptación**:
        *   La latencia de consultas recursivas de grafo a 3 saltos permanece inferior a 100 ms bajo volumen enterprise de CodeMapping.
        *   Se implementa el trigger Postgres que actualiza de forma automática el cierre transitivo materializado en cada ingesta de cm. edges.
        *   Se documenta el veredicto de performance validando el hito D18.

*   **`KC-PLT-11-5`: Barrido final de deuda técnica y verificación de los 48 hallazgos cerrados**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Se audita individualmente el estado de remediación de los 48 hallazgos heredados en el monorepo. Se comprueba que cada uno cuente con su respectiva prueba de regresión automática y que ninguna vulnerabilidad bloqueante o alta quede abierta antes de la GA.
    *   **Criterios de Aceptación**:
        *    Checklist de auditoría de cierre verificado y publicado en `docs/informes/cierre-auditoria.md`.
        *   Las pruebas de regresión asociadas corren en el CI y demuestran el bloqueo correcto de las fallas heredadas.
        *   Cero bloqueantes o altos abiertos. Deuda técnica menor documentada con ticket asignado para fase 2.

---

### SPRINT 12 – QUE EL RELEASE SEA AUDITABLE (HITO M6)
*   **Fechas**: 19 al 23 de octubre de 2026
*   **Capacidad**: 26 Story Points
*   **Foco del Sprint**: Sellar el **Hito M6: Release GA** de KalliCode. Congelar el esquema final de base de datos relacional multi-tenant, ensayar de forma real el pipeline de despliegue productivo y el plan de reversión (rollback), verificar el funcionamiento de la cadena de hashes de auditoría en el entorno real, y firmar el acta de GA de KalliCode.

#### Tickets del Sprint 12:

*   **`KC-PLT-12-1`: Congelación del esquema de base de datos relacional y documentación del modelo**
    *   **Épica**: `EPL4` (Aislamiento Multi-tenant y Gobierno)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: Se congela el esquema final de Postgres (claves compuestas, particionamiento, pgvector, triggers RLS, cm, etc.) bloqueando cualquier DDL posterior sin una migración Alembic formalmente revisada. Se actualiza el documento de especificación técnica del modelo físico relacional de producción.
    *   **Criterios de Aceptación**:
        *   Esquema Postgres consolidado y congelado como versión 1.0 GA.
        *   Documento de modelo físico de base de datos actualizado cubriendo las 41 tablas con sus políticas e índices.
        *   El pipeline CI bloquea cualquier alteración de esquema sin script de migración versionado Alembic.

*   **`KC-PLT-12-2`: Runbook de release productivo y plan de reversión (Rollback), ensayado**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro B (DevOps & SRE)
    *   **Story Points**: 5 SP
    *   **Descripción**: Se redacta y ensaya el runbook de despliegue a producción de KalliCode, incluyendo el plan de reversión (rollback) automático de base de datos y contenedores si se detectan anomalías post-deploy.
    *   **Criterios de Aceptación**:
        *   Runbook de release y rollback escrito y ensayado en directo en el entorno de pre-producción.
        *   La reversión automática de esquema Postgres (Alembic `down` transaccional) corre exitosamente sin pérdida de consistencia.
        *   Ensayo ejecutado por un miembro del equipo diferente a quien lo escribió.

*   **`KC-PLT-12-3`: Verificación final de la cadena de auditoría inmutable en producción**
    *   **Épica**: `EPL5` (Integridad Probatoria: Auditoría y Exportación)
    *   **Asignado a**: Miembro C (Seguridad y Confianza)
    *   **Story Points**: 5 SP
    *   **Descripción**: Se corre la verificación completa de la cadena de hashes de auditoría sobre el entorno productivo real (incluyendo tramos de particiones archivadas en Blob Storage WORM) para demostrar científicamente la inmutabilidad de los expedientes previos al hito de GA.
    *   **Criterios de Aceptación**:
        *   El verificador de cadena asíncrono corre en verde sobre la base productiva sin reportar fallas.
        *   Se programa la alerta automática en Azure Monitor ante fallas de encadenamiento de hash o mutación prohibida.
        *   Publicación de la verificación en `docs/informes/verificacion-cadena.md`.

*   **`KC-PLT-12-4`: Criterio de M6 verificado y firma del hito canónico de release GA**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 5 SP
    *   **Descripción**: Se recogen los informes de calidad y UAT de los seis equipos, se validan frente a los criterios del Product Owner y se unifican en el acta de GA de KalliCode, coordinando la firma inmutable de los seis Tech Leads del programa.
    *   **Criterios de Aceptación**:
        *   Informe de GA publicado con la evidencia de los seis equipos en `docs/informes/release-ga.md`.
        *   Cierre de deudas técnicas conocidas declarado.
        *   Acta de release GA firmada digitalmente por los líderes del Architecture Board y el Product Owner.

*   **`KC-PLT-12-5`: Traspaso del runbook de operaciones al equipo de soporte del cliente**
    *   **Épica**: `EPL8` (Observabilidad y Release)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Se realiza el traspaso de operaciones de la plataforma al equipo de soporte permanente del cliente, dictando una capacitación sobre resolución de incidentes basándose en los runbooks indexados en S10.
    *   **Criterios de Aceptación**:
        *   Traspaso documentado en `docs/operacion/traspaso.md` cubriendo la matriz de escalación de soporte.
        *   Soporte del cliente ejecuta de forma exitosa 3 runbooks en simulacros reales.
        *   Calendario de tareas periódicas de mantenimiento publicado.

*   **`KC-PLT-12-6`: Congelación de contratos de plataforma y guía para desarrolladores externos**
    *   **Épica**: `EPL2` (Núcleo, Configuración y Empaquetado)
    *   **Asignado a**: Miembro A (Plataforma y Datos)
    *   **Story Points**: 3 SP
    *   **Descripción**: Se congelan en versión 1.0 GA las APIs, el paquete núcleo compartible, los roles Postgres y las configuraciones de la plataforma de soporte de KalliCode. Se redacta la guía de adopción técnica para desarrolladores externos que pretendan extender la plataforma.
    *   **Criterios de Aceptación**:
        *   Guía técnica de la plataforma publicada en `docs/plataforma/guia.md`.
        *   Los Tech Leads firman de conformidad que el andamiaje es suficiente para extender funcionalidad sin soporte directo de E5.

---

##  4. Matriz de Cobertura de Épicas y Story Points (DoD del PM)

Para garantizar la consistencia aritmética de este backlog y certificar que cumple con el Plan E5 original sin desviaciones, se detalla la siguiente matriz de cuadre:

| Épica | Nombre de la Épica | SP Comprometidos | % del Total | Tickets Asociados |
| :--- | :--- | :---: | :---: | :--- |
| **EPL1** | CI/CD, Entornos e Infraestructura | 33 SP | 11.2% | `01-1`, `01-4`, `01-5`, `03-3`, `06-1`, `08-2`, `09-5` |
| **EPL2** | Núcleo, Configuración y Empaquetado | 18 SP | 6.1% | `01-2`, `04-3`, `05-5`, `05-6`, `08-5`, `12-6` |
| **EPL3** | Identidad, Sesiones y RBAC | 17 SP | 5.8% | `03-5`, `04-5`, `10-4` |
| **EPL4** | Aislamiento Multi-tenant y Gobierno | 48 SP | 16.3% | `01-3`, `02-1`, `02-2`, `02-3`, `02-4`, `03-1`, `06-2`, `07-3`, `07-4`, `12-1` |
| **EPL5** | Integridad Probatoria: Auditoría y Exp. | 37 SP | 12.6% | `03-2`, `04-1`, `04-2`, `04-4`, `05-3`, `08-3`, `09-2`, `12-3` |
| **EPL6** | Bóveda, Almacenamiento y Endurecimiento | 34 SP | 11.6% | `03-4`, `05-1`, `05-2`, `05-4`, `06-3`, `09-3`, `11-3` |
| **EPL7** | Rendimiento, Retención y Recuperación | 70 SP | 23.8% | `04-6`, `06-5`, `07-1`, `07-2`, `07-5`, `07-6`, `08-1`, `08-4`, `09-1`, `10-3`, `11-1`, `11-2`, `11-4` |
| **EPL8** | Observabilidad y Release | 37 SP | 12.6% | `09-4`, `10-1`, `10-2`, `10-5`, `11-5`, `12-2`, `12-4`, `12-5` |
| **TOTAL**| **8 Épicas de Plataforma** | **294 SP** | **100%** | **66 Tickets Completos** |

---

## 🏁 5. Definición de Terminado (DoD) Aplicada a cada PR del Equipo 5
Para que cualquiera de los tickets del backlog sea declarado cerrado transaccionalmente, se exige que su Pull Request cumpla de forma irrefutable con las siguientes aserciones automáticas:

1.  **Aislamiento RLS verificado**: Toda consulta SQL nueva corre sobre una sesión con `app.tenant_id` fijado. Pasa con éxito la prueba de aserción multi-tenant (cero fugas de datos).
2.  **Seguridad estricta**: Semgrep, Gitleaks y Trivy devuelven cero hallazgos abiertos de severidad alta o crítica.
3.  **Higiene del Ledger**: Las migraciones relacionales se enumeran secuencialmente sin saltos y registran de forma obligatoria el hash SHA-256 de su DDL; se aplican de manera idempotente.
4.  **Cobertura de pruebas**: Cobertura de código unitaria mínima del **80% global** medido por el CI, y del **95% obligatoria** para los módulos sensibles (seguridad, cookies, firma de gates, aislamiento, y sellado criptográfico).
5.  **Cero literales hardcodeados**: Los secretos se resuelven en Key Vault por referencia, y todo texto de la interfaz reside en el catálogo de traducción común.
