"""
Ticket: KC-QA-B2-05
Suite de Aislamiento para el Sandbox de Ejecución (Test Harness).
Garantiza que el código autogenerado por el motor IA no tenga acceso a internet,
secretos del host, ni al sistema de archivos subyacente. Cierra el hallazgo QA-F05.
"""

import subprocess

import pytest

# Habilitamos ejecución asíncrona estándar en la suite de pruebas
pytestmark = pytest.mark.asyncio

# Imagen local Cero-Nube donde se ejecutan los tests generados por IA
RUNNER_IMAGE = "kallicode-test-runner"


def execute_malicious_payload(
    python_code: str, enforce_net_none: bool = True
) -> subprocess.CompletedProcess[str]:
    """
    Spawnea un contenedor efímero inyectando código arbitrario para simular
    el comportamiento de un script autogenerado por el motor de IA.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        # Aislamiento de Red: Desconexión total de la red externa
        "--net=none" if enforce_net_none else "--net=bridge",
        # Aislamiento de Entorno: Evitamos heredar el archivo .env o variables del host
        "--env",
        "IS_SANDBOX_ENV=true",
        # Aislamiento de Privilegios: Ejecutar como usuario no privilegiado (UID 1000)
        "--user",
        "1000:1000",
        # Aislamiento de Kernel: Bloquea escalada de privilegios (ej. sudo, binarios setuid)
        "--security-opt",
        "no-new-privileges",
        RUNNER_IMAGE,
        "python",
        "-c",
        python_code,
    ]

    # Prevenimos que un payload de prueba malicioso cuelgue el pipeline (Timeout 5s)
    # PLW1510: check=False explícito porque queremos evaluar los errores, no lanzar excepción
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)


async def test_sandbox_network_isolation_blocks_outbound() -> None:
    """
    [Criterio de Aceptación] Verifica que cualquier intento de conexión saliente
    desde el código autogenerado falle por 'Network is unreachable' o timeout.
    """
    payload = (
        "import urllib.request\n"
        "import sys\n"
        "try:\n"
        "    urllib.request.urlopen('http://1.1.1.1', timeout=1)\n"
        "except Exception as e:\n"
        "    print(f'SECURE_BLOCKED: {e}')\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )

    result = execute_malicious_payload(payload)

    assert result.returncode == 0, f"Error en la ejecución del contenedor: {result.stderr}"
    assert "SECURE_BLOCKED:" in result.stdout, (
        "Vulnerabilidad: El payload logró abrir un socket de red."
    )
    assert "Network is unreachable" in result.stdout or "urlopen error" in result.stdout, (
        "El tipo de bloqueo de red es incorrecto. Se esperaba Network Unreachable."
    )


async def test_sandbox_environment_secrets_isolation() -> None:
    """
    Garantiza que variables sensibles de la plataforma (PG_DSN, LLM_API_KEY)
    jamás se inyecten en el contenedor de ejecución de las pruebas.
    """
    payload = "import os\nprint('ENV_DUMP:', list(os.environ.keys()))\n"

    result = execute_malicious_payload(payload)

    stdout = result.stdout.upper()
    assert "ENV_DUMP" in stdout, "No se pudo recuperar el volcado del entorno."
    assert "PG_DSN" not in stdout, (
        "Vulnerabilidad Crítica: Cadena de conexión de BD expuesta al sandbox."
    )
    assert "LLM_API_KEY" not in stdout, "Vulnerabilidad Crítica: Token de IA expuesto al sandbox."
    assert "IS_SANDBOX_ENV" in stdout, (
        "El entorno no está inyectando las variables base correctamente."
    )


async def test_sandbox_filesystem_isolation() -> None:
    """
    Confirma que el sandbox no tiene montajes de volúmenes accidentales
    hacia la raíz del host y degrada permisos exitosamente.
    """
    payload = (
        "import os\n"
        "try:\n"
        "    with open('/etc/shadow', 'r') as f:\n"
        "        pass\n"
        "    print('VULN_FS_READ')\n"
        "except PermissionError:\n"
        "    print('SECURE_FS_BLOCKED')\n"
    )

    result = execute_malicious_payload(payload)

    assert "VULN_FS_READ" not in result.stdout, (
        "Vulnerabilidad Crítica: Acceso de lectura autorizado en sistema de archivos."
    )
    assert "SECURE_FS_BLOCKED" in result.stdout, (
        "El sandbox no restringió los permisos adecuadamente."
    )
