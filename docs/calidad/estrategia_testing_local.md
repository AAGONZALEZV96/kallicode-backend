# Estrategia de Testing Local (Cero-Nube)

Esta guía define el estándar del equipo para ejecutar la suite completa de pruebas E2E, integración y unitarias de KalliCode en entornos locales, garantizando la privacidad y evitando el uso de credenciales o costos en la nube.

## 1. Prerrequisitos del Entorno
Asegúrate de contar con las siguientes herramientas en tu laptop (compatible con macOS, Linux y WSL2):
- **Docker Engine** o **Docker Desktop** ejecutándose.
- **uv** instalado (gestor de paquetes de Python).
- **Make** disponible en tu terminal.

## 2. Arquitectura "Cero-Nube"
En lugar de depender de servicios gestionados o scripts manuales, la infraestructura efímera se orquesta mediante `docker-compose.test.yml` con volúmenes en memoria (`tmpfs`) para una ejecución ultra rápida sin persistencia:
- **Base de Datos:** PostgreSQL 16 + pgvector (`kallicode_test`).
- **Almacenamiento (Blobs):** Azurite emulando Azure Blob Storage.
- **Caché y Colas:** Redis 7.
- **IA y Embeddings:** LLM Stub local (puerto 8081) y contenedor TEI (HuggingFace BGE-M3).
- **Correo:** Mailpit para intercepción SMTP.

---

## 3. Guía de Ejecución Rápida (Recomendada)
El equipo ha consolidado el levantamiento de dependencias y la ejecución en un solo comando automatizado a través del `Makefile`. Desde la raíz del monorepo, ejecuta:

```bash
make test-local
# Este comando orquesta el ciclo de vida completo de la prueba de forma hermética:
# 1. Levanta la infraestructura efímera (make test-env-up).
# 2. Puebla la base de datos de pruebas aislada con los roles y esquemas oficiales (make db KC_DB_NAME=kallicode_test).
# 3. Ejecuta la suite de pruebas local con uv run pytest.
# 4. Destruye los contenedores y volúmenes al finalizar para liberar recursos (make test-env-down).
```
## 4. Ejecución Paso a Paso (Flujo de Depuración / TDD)
Si necesitas mantener la infraestructura levantada en segundo plano para depurar un test específico iterativamente, utiliza el flujo manual dividido:

- **Paso A: Levantar Infraestructura Emulada** 
Levanta todos los servicios de apoyo en segundo plano:

```bash
make test-env-up
```
- **Paso B: Preparar la Base de Datos**
Inyecta los roles (kallicode_app, kallicode_admin), extensiones y el esquema estructural en el entorno efímero:
```bash
make db KC_DB_NAME=kallicode_test
```
- **Paso C: Ejecutar Pruebas**
Corre pytest directamente usando el entorno virtual gestionado por uv. Puedes apuntar a archivos o carpetas específicas:
```bash
uv run pytest packages/testing/tests/ -v
```
- **Paso D: Limpieza y Teardown**
Una vez finalizada tu sesión de desarrollo local, destruye los contenedores huérfanos:
```bash
make test-env-down
```