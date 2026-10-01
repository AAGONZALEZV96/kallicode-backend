# Estrategia de Testing Local (Cero-Nube)

Esta guía define el estándar del equipo para ejecutar la suite completa de pruebas E2E, integración y unitarias de KalliCode en entornos locales, garantizando la privacidad y evitando el uso de credenciales o costos en la nube de Azure.

## 1. Prerrequisitos del Entorno
Asegúrate de contar con las siguientes herramientas en tu laptop (compatible con macOS, Linux y WSL2):
- **Docker Engine** o **Docker Desktop** ejecutándose.
- **uv** instalado (gestor de paquetes de Python).
- Clon del monorepo KalliCode actualizado en tu rama de trabajo.

## 2. Arquitectura "Cero-Nube"
En lugar de depender de servicios gestionados, levantamos dobles de prueba (mocks) mediante contenedores ligeros:
- **Almacenamiento (Blobs):** Emulado localmente a través del contenedor oficial **Azurite**.
- **Motor de Pruebas (E2E):** Ejecutado de forma aislada en la imagen hermética `kallicode/test-runner` que pre-empaqueta Python 3.12, Node.js y los binarios de Playwright.

---

## 3. Guía de Ejecución Paso a Paso

### Paso A: Levantar Infraestructura Emulada
Antes de lanzar las pruebas, inicializa los servicios base. Abre una terminal en la raíz del monorepo y ejecuta:

```bash
# Levantar emulador de Azure Blob Storage (Azurite)
docker run -d -p 10000:10000 --name azurite-qa-local [mcr.microsoft.com/azure-storage/azurite](https://mcr.microsoft.com/azure-storage/azurite) azurite-blob --blobHost 0.0.0.0
```
### Paso B: Construir el Test Runner (Imagen Hermética)
Si es la primera vez que ejecutas pruebas o si el archivo uv.lock cambió recientemente, reconstruye la imagen local:

```bash
DOCKER_BUILDKIT=1 docker build -t kallicode/test-runner:local -f packages/testing/Dockerfile.runner .
```
### Paso C: Ejecutar la Suite de Pruebas
Utilizamos la imagen hermética montando el código en tiempo real (-v). El comando varía ligeramente según tu sistema operativo para resolver las rutas absolutas.
```bash
# Para Linux y macOS:
docker run --rm \
  --network host \
  -v "$(pwd):/app" \
  -e KC_STORAGE_EMULATOR=true \
  kallicode/test-runner:local uv run python -m pytest packages/testing/tests/ -v

  # Para Windows (WSL2):
  docker run --rm \
  --network host \
  -v "${PWD}:/app" \
  -e KC_STORAGE_EMULATOR=true \
  kallicode/test-runner:local uv run python -m pytest packages/testing/tests/ -v
  ```
### Paso D: Limpieza y Teardown
Una vez finalizadas las pruebas y emitidos los reportes de cobertura, apaga y destruye el contenedor de almacenamiento para liberar recursos:

```bash
docker rm -f azurite-qa-local
```
