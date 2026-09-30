import socket
import sys
import time

PORTS = {
    "PostgreSQL (pgvector)": 5432,
    "Azurite (Blob Storage)": 10000,
    "Mailpit (UI)": 8025,
    "TEI (Embeddings)": 8080,
    "Stub LLM": 8081,
}
TIMEOUT = 20
start_time = time.time()

print(f"⌛ Esperando disponibilidad de servicios efímeros (timeout: {TIMEOUT}s)...")


def check_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


while True:
    all_up = True
    for name, port in PORTS.values():
        if not check_port("127.0.0.1", port):
            all_up = False
            break

    if all_up:
        elapsed = time.time() - start_time
        print(f"✅ Todos los servicios están listos en {elapsed:.2f} segundos.")
        sys.exit(0)

    if time.time() - start_time > TIMEOUT:
        print("❌ Error: Tiempo de espera agotado. Los servicios no levantaron en < 20s.")
        sys.exit(1)

    time.sleep(1)
