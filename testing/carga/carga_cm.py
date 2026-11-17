"""Orquestador de la prueba de carga y de caos de la capa de consulta (KC-CM-11-5).

    uv run python testing/carga/carga_cm.py preparar            # siembra y datos.json
    uv run python testing/carga/carga_cm.py ejecutar            # API + k6 (carga)
    uv run python testing/carga/carga_cm.py ejecutar --caos --contenedor-pg <nombre>

`preparar` carga el grafo de 200.000 nodos y ~300.000 aristas con el generador y el cargador del
benchmark de KC-DB-11-3 (`packages/core/tests/bench_cm_khop.py`, perfil `proyeccion_200k`: forma de
cola pesada con nodos centrales), publica el snapshot (refresca cm.reachability) y añade
embeddings de símbolos e incidentes para que búsqueda e {{incidents}} tengan datos. Escribe
`datos.json` con las semillas de las consultas, los presupuestos p95 (del contrato, nunca copiados
a mano), las rutas HTTP y el catálogo de errores.

`ejecutar` levanta la API interna (un worker de uvicorn, como apps/api/Dockerfile) con un par de
claves JWT de servicio generado para la corrida, emite el token del servicio `orquestador` y lanza
k6 (el binario si está en el PATH; si no, la imagen grafana/k6). Con `--caos` detiene PostgreSQL a
mitad de la carga y lo vuelve a arrancar; al final comprueba que la API se recupera sola. Escribe
el resumen de k6 y un informe Markdown en --salida.

Requiere KC_DATABASE_URL_MIGRATIONS (superusuario: siembra), KC_DATABASE_URL (la API) y
KC_REDIS_URL, con las migraciones aplicadas.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import psycopg

RAIZ = Path(__file__).resolve().parents[2]
DIR_CARGA = Path(__file__).resolve().parent
TENANT = "carga_cm"
PERFIL = "proyeccion_200k"
N_SEMILLAS = 500
N_EMBEDDINGS = 20_000
N_VECTORES_CONSULTA = 20
N_INCIDENTES = 2_000
DIM = 1024
PUERTO_API = 8001
IMAGEN_K6 = "grafana/k6:0.54.0"
SEMILLA = 20261116


def _bench_khop() -> ModuleType:
    """El benchmark de KC-DB-11-3 como módulo: su generador y su cargador son la fuente única."""
    ruta = RAIZ / "packages" / "core" / "tests" / "bench_cm_khop.py"
    spec = importlib.util.spec_from_file_location("bench_cm_khop", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["bench_cm_khop"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _dsn_admin() -> str:
    url = os.environ["KC_DATABASE_URL_MIGRATIONS"]
    return url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


# ---------------------------------------------------------------------------
# preparar
# ---------------------------------------------------------------------------
def _vector_binario(v: Any) -> bytes:
    """Formato binario de pgvector: dim int16, 0 int16, float32 big-endian."""
    return struct.pack(">HH", DIM, 0) + v.astype(">f4").tobytes()


def _limpiar_extra(admin: psycopg.Connection[Any]) -> None:
    for tabla in (
        "vec.incident_embeddings",
        "cm.incident_nodes",
        "cm.incidents",
        "vec.definition_embeddings",
    ):
        admin.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (TENANT,))


def _sembrar_vectores_e_incidentes(admin: psycopg.Connection[Any], grafo: Any) -> list[list[float]]:
    """Embeddings de símbolos alrededor de 100 centroides e incidentes enlazados al grafo."""
    rng = np.random.default_rng(SEMILLA)
    centroides = rng.standard_normal((100, DIM)).astype(np.float32)
    centroides /= np.linalg.norm(centroides, axis=1, keepdims=True)
    simbolos = [s for s, _ in grafo.simbolos]
    elegidos = random.Random(SEMILLA).sample(simbolos, N_EMBEDDINGS)
    grupos = rng.integers(0, 100, N_EMBEDDINGS)
    vecs = centroides[grupos] + rng.standard_normal((N_EMBEDDINGS, DIM)).astype(np.float32) * 0.03
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    with admin.transaction(), admin.cursor() as cur:
        with cur.copy(
            "COPY vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, descripcion,"
            " modulo, embedding, texto_hash) FROM STDIN (FORMAT BINARY)"
        ) as cp:
            cp.set_types(["text"] * 7 + ["bytea", "text"])
            for i, sid in enumerate(elegidos):
                cp.write_row(
                    (f"e_{sid}", TENANT, sid, "symbol", sid, "carga", f"src/m{i % 40:02d}",
                     _vector_binario(vecs[i]), "h")
                )  # fmt: skip
        rnd = random.Random(SEMILLA + 1)
        incidentes = [
            (f"inc_{i:05d}", sorted(set(rnd.sample(simbolos, rnd.randint(1, 3)))))
            for i in range(N_INCIDENTES)
        ]
        with cur.copy(
            "COPY cm.incidents (id, tenant_id, origen, resumen, nodos_afectados) FROM STDIN"
        ) as cp:
            for iid, nodos in incidentes:
                lista = "[" + ",".join(f'"{n}"' for n in nodos) + "]"
                cp.write_row((iid, TENANT, "manual", f"Fallo {iid}", lista))
        with cur.copy("COPY cm.incident_nodes (tenant_id, incident_id, nodo_ref) FROM STDIN") as cp:
            for iid, nodos in incidentes:
                for n in nodos:
                    cp.write_row((TENANT, iid, n))
    for tabla in ("vec.definition_embeddings", "cm.incidents", "cm.incident_nodes"):
        admin.execute(f"ANALYZE {tabla}")
    return [[round(float(x), 6) for x in c] for c in centroides[:N_VECTORES_CONSULTA]]


def datos_contrato() -> dict[str, Any]:
    """Presupuestos, rutas y catálogo de errores, leídos de su fuente única."""
    from kallicode_api.routers.codemapping_grafo import RUTAS_OPERACIONES
    from kallicode_core.cm_grafo import OPERACIONES
    from kallicode_core.errors import CATALOGO_ERRORES

    bench = _bench_khop()
    presupuestos = {o.nombre: o.presupuesto_p95_ms for o in OPERACIONES}
    # ADR-020: el recorrido a tres saltos sobre 200.000 nodos tiene un presupuesto más estricto.
    for op in bench.KHOP:
        presupuestos[op] = min(presupuestos[op], int(bench.PRESUPUESTO_KHOP_MS))
    return {
        "presupuestos_p95_ms": presupuestos,
        "rutas": RUTAS_OPERACIONES,
        "codigos_error": sorted(CATALOGO_ERRORES),
    }


def preparar(salida: Path) -> dict[str, Any]:
    bench = _bench_khop()
    grafo = bench.generar(bench.PERFILES[PERFIL])
    with psycopg.connect(_dsn_admin(), autocommit=True) as admin:
        _limpiar_extra(admin)
        carga = bench.cargar(admin, TENANT, grafo)
        vectores = _sembrar_vectores_e_incidentes(admin, grafo)
    semillas = [
        {"nodo": s.nodo, "fichero": s.fichero, "estrato": s.estrato}
        for s in bench.semillas(grafo, N_SEMILLAS)
    ]
    datos = {
        "tenant": TENANT,
        "perfil": PERFIL,
        "carga": {
            "nodos": carga.nodos,
            "aristas": carga.aristas,
            "max_grado_entrada": carga.max_grado_entrada,
            "reachability_estado": carga.reachability_estado,
            "reachability_filas": carga.reachability_filas,
            "embeddings": N_EMBEDDINGS,
            "incidentes": N_INCIDENTES,
        },
        "semillas": semillas,
        "vectores": vectores,
        **datos_contrato(),
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(datos), encoding="utf-8")
    print(
        f"grafo {PERFIL}: {carga.nodos} nodos, {carga.aristas} aristas, "
        f"reachability {carga.reachability_estado} ({carga.reachability_filas} filas) -> {salida}"
    )
    return datos


# ---------------------------------------------------------------------------
# ejecutar
# ---------------------------------------------------------------------------
def _par_claves() -> tuple[str, str]:
    """Par RSA de la corrida: la API verifica con la pública y el orquestador firma el token."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    privada = clave.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    publica = (
        clave.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return privada, publica


def _esperar(url: str, segundos: float = 60) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"{url} no respondió en {segundos} s")


def _levantar_api(entorno: dict[str, str], workers: int) -> subprocess.Popen[bytes]:
    proceso = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "kallicode_api.main_interna:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(PUERTO_API),
            "--log-level",
            "warning",
        ],
        env=entorno,
        cwd=RAIZ,
        # Grupo de procesos propio (POSIX) para poder parar también a los workers.
        start_new_session=sys.platform != "win32",
    )
    _esperar(f"http://127.0.0.1:{PUERTO_API}/health")
    return proceso


def _parar_api(api: subprocess.Popen[bytes]) -> None:
    """Para la API con sus workers: con --workers > 1, terminar solo al padre deja huérfanos
    escuchando en el puerto, y la siguiente corrida hablaría con ellos."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(api.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        os.killpg(api.pid, signal.SIGTERM)
    api.wait(timeout=30)


def _puerto_libre() -> None:
    try:
        with socket.create_connection(("127.0.0.1", PUERTO_API), timeout=1):
            pass
    except OSError:
        return
    raise SystemExit(f"el puerto {PUERTO_API} ya está en uso: para la API anterior antes de medir")


def _comando_k6(datos: Path, resumen: Path, entorno: dict[str, str]) -> list[str]:
    script = DIR_CARGA / "cm_consulta.js"
    variables = {
        "TOKEN": entorno["TOKEN"],
        "ESCENARIO": entorno["ESCENARIO"],
        "VUS": entorno["VUS"],
        "DURACION": entorno["DURACION"],
    }
    if shutil.which("k6"):
        variables |= {
            "BASE_URL": f"http://127.0.0.1:{PUERTO_API}",
            "DATOS": str(datos),
            "RESUMEN": str(resumen),
        }
        return ["k6", "run", "--quiet", *[f"-e{k}={v}" for k, v in variables.items()], str(script)]
    # Sin k6 instalado: la imagen oficial, con el script, los datos y la salida montados.
    variables |= {
        "BASE_URL": f"http://host.docker.internal:{PUERTO_API}",
        "DATOS": f"/datos/{datos.name}",
        "RESUMEN": f"/salida/{resumen.name}",
    }
    return [
        "docker", "run", "--rm", "--add-host=host.docker.internal:host-gateway",
        "-v", f"{DIR_CARGA}:/carga:ro", "-v", f"{datos.parent}:/datos:ro",
        "-v", f"{resumen.parent}:/salida",
        *[a for k, v in variables.items() for a in ("-e", f"{k}={v}")],
        IMAGEN_K6, "run", "--quiet", "/carga/cm_consulta.js",
    ]  # fmt: skip


def _sondear(token: str, datos: dict[str, Any], n: int = 20) -> int:
    """Consultas de impacto secuenciales tras el caos: cuántas responden 200."""
    ok = 0
    for s in datos["semillas"][:n]:
        peticion = urllib.request.Request(
            f"http://127.0.0.1:{PUERTO_API}/internal/v1/codemapping/grafo/impacto",
            data=json.dumps({"nodos": [s["nodo"]], "k": 3}).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"sondeo-{s['nodo']}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=30) as r:
                ok += r.status == 200
        except urllib.error.HTTPError:
            pass
    return ok


def _caos(contenedor: str, inicio_s: float, caida_s: float) -> subprocess.Popen[bytes]:
    """Detiene PostgreSQL a los `inicio_s` segundos durante `caida_s` y lo vuelve a arrancar."""
    guion = (
        f"import subprocess, time; time.sleep({inicio_s}); "
        f"subprocess.run(['docker', 'stop', '-t', '2', {contenedor!r}], check=True); "
        f"time.sleep({caida_s}); "
        f"subprocess.run(['docker', 'start', {contenedor!r}], check=True)"
    )
    return subprocess.Popen([sys.executable, "-c", guion])


def ejecutar(args: argparse.Namespace) -> int:
    # Rutas absolutas: docker run solo monta directorios del host con ruta absoluta.
    datos_ruta: Path = args.datos.resolve()
    args.salida = args.salida.resolve()
    datos = json.loads(datos_ruta.read_text(encoding="utf-8"))
    args.salida.mkdir(parents=True, exist_ok=True)
    escenario = "caos" if args.caos else "carga"
    resumen = args.salida / f"resumen_{escenario}.json"

    privada, publica = _par_claves()
    entorno = dict(os.environ)
    entorno |= {
        "KC_JWT_PRIVATE_KEY_SVC": privada,
        "KC_JWT_PUBLIC_KEY_SVC": publica,
        "KC_SVC_TOKEN_TTL_MIN": "120",
        "KC_ALLOWED_HOSTS": "localhost,127.0.0.1,host.docker.internal",
    }
    # El token se firma en este proceso con la misma clave que la API usará para verificarlo.
    for clave in ("KC_JWT_PRIVATE_KEY_SVC", "KC_JWT_PUBLIC_KEY_SVC", "KC_SVC_TOKEN_TTL_MIN"):
        os.environ[clave] = entorno[clave]
    from kallicode_core.config import get_settings
    from kallicode_core.seguridad import emitir_svc_token

    get_settings.cache_clear()
    token = emitir_svc_token("orquestador", datos["tenant"])

    _puerto_libre()
    api = _levantar_api(entorno, args.workers)
    caos: subprocess.Popen[bytes] | None = None
    try:
        if args.caos:
            if not args.contenedor_pg:
                raise SystemExit("--caos necesita --contenedor-pg")
            caos = _caos(args.contenedor_pg, args.caos_inicio, args.caos_duracion)
        entorno_k6 = {
            "TOKEN": token,
            "ESCENARIO": escenario,
            "VUS": str(args.vus),
            "DURACION": args.duracion,
        }
        k6 = subprocess.run(_comando_k6(datos_ruta, resumen, entorno_k6), check=False)
        if caos is not None:
            caos.wait(timeout=300)
            _esperar(f"http://127.0.0.1:{PUERTO_API}/health/ready", 120)
        recuperadas = _sondear(token, datos)
    finally:
        _parar_api(api)

    resumen_k6 = json.loads(resumen.read_text(encoding="utf-8"))
    datos["workers"] = args.workers
    informe = informe_markdown(escenario, datos, resumen_k6, recuperadas)
    (args.salida / f"informe_{escenario}.md").write_text(informe, encoding="utf-8")
    print(informe)
    if recuperadas < 20:
        print(f"la API no se recuperó: {recuperadas}/20 consultas tras la carga", file=sys.stderr)
        return 1
    return k6.returncode


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------
def _valor(metricas: dict[str, Any], nombre: str, campo: str) -> float | None:
    m = metricas.get(nombre)
    if not m:
        return None
    v = m.get("values", {}).get(campo)
    return None if v is None else float(v)


def informe_markdown(
    escenario: str, datos: dict[str, Any], resumen: dict[str, Any], recuperadas: int
) -> str:
    metricas = resumen.get("metrics", {})
    carga = datos["carga"]
    peticiones = _valor(metricas, "http_reqs", "count") or 0
    duracion_s = (resumen.get("state", {}).get("testRunDurationMs") or 0) / 1000
    lineas = [
        f"## Escenario `{escenario}`",
        "",
        f"Grafo `{datos['perfil']}`: {carga['nodos']:,} nodos, {carga['aristas']:,} aristas, "
        f"grado de entrada máximo {carga['max_grado_entrada']:,}; cm.reachability "
        f"{carga['reachability_estado']} ({carga['reachability_filas']:,} filas); "
        f"{carga['embeddings']:,} embeddings y {carga['incidentes']:,} incidentes.",
        "",
        f"API: {datos.get('workers', 1)} worker(s) de uvicorn. "
        f"{peticiones:,.0f} peticiones en {duracion_s:,.0f} s "
        f"({peticiones / duracion_s if duracion_s else 0:,.1f} req/s).",
        "",
    ]
    if escenario == "carga":
        lineas += [
            "| Operación | Peticiones | p50 (ms) | p95 (ms) | p99 (ms) | Presupuesto p95 | Cumple |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
        for op, presupuesto in datos["presupuestos_p95_ms"].items():
            nombre = f"http_req_duration{{op:{op}}}"
            p95 = _valor(metricas, nombre, "p(95)")
            fila = [
                f"`{op}`",
                f"{_valor(metricas, nombre, 'count') or 0:,.0f}",
                f"{_valor(metricas, nombre, 'med') or 0:,.1f}",
                f"{p95 or 0:,.1f}",
                f"{_valor(metricas, nombre, 'p(99)') or 0:,.1f}",
                f"{presupuesto}",
                "sí" if p95 is not None and p95 < presupuesto else "**no**",
            ]
            lineas.append("| " + " | ".join(fila) + " |")
        lineas.append("")
    lineas += [
        f"- Respuestas sin catalogar: {_valor(metricas, 'respuestas_no_catalogadas', 'count') or 0:.0f}.",
        f"- 503 DEPENDENCIA_NO_DISPONIBLE: "
        f"{_valor(metricas, 'respuestas_dependencia_no_disponible', 'count') or 0:.0f}.",
        f"- Tasa de error: {(_valor(metricas, 'errores', 'rate') or 0) * 100:.2f} %.",
        f"- Consultas de impacto correctas tras la prueba (recuperación): {recuperadas}/20.",
        "",
    ]
    return "\n".join(lineas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("preparar")
    p.add_argument("--datos", type=Path, default=DIR_CARGA / "salida" / "datos.json")
    e = sub.add_parser("ejecutar")
    e.add_argument("--datos", type=Path, default=DIR_CARGA / "salida" / "datos.json")
    e.add_argument("--salida", type=Path, default=DIR_CARGA / "salida")
    e.add_argument("--vus", type=int, default=50)
    # Procesos de uvicorn de la API: 1 = una réplica de apps/api/Dockerfile.
    e.add_argument("--workers", type=int, default=1)
    e.add_argument("--duracion", default="3m")
    e.add_argument("--caos", action="store_true")
    e.add_argument("--contenedor-pg")
    e.add_argument("--caos-inicio", type=float, default=30)
    e.add_argument("--caos-duracion", type=float, default=20)
    args = parser.parse_args()
    if args.orden == "preparar":
        preparar(args.datos)
        return 0
    return ejecutar(args)


if __name__ == "__main__":
    sys.exit(main())
