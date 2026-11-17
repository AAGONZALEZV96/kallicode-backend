// Prueba de carga y de caos de la capa de consulta de CodeMapping (KC-CM-11-5).
//
// 50 usuarios virtuales concurrentes (los agentes del pipeline) consultan el grafo de 200.000 nodos
// por la API interna: POST /internal/v1/codemapping/grafo/<operacion>. El presupuesto p95 de cada
// operación lo declara el contrato (kallicode_core.cm_grafo.OPERACIONES; 120 ms para impacto y
// dependencias a k = 3 según ADR-020) y llega en datos.json, que genera
// `python testing/carga/carga_cm.py preparar`: aquí no se repite ningún número.
//
// Escenarios (ESCENARIO):
//   carga  50 VUs durante DURACION. Umbrales: p95 por operación dentro de su presupuesto, 0
//          respuestas sin catalogar y < 1 % de errores.
//   caos   mismo tráfico mientras el orquestador detiene y arranca PostgreSQL. Umbral: 0
//          respuestas sin catalogar; durante la caída solo se admite 503 DEPENDENCIA_NO_DISPONIBLE.
//          No mide latencia (la caída la dispara a propósito).
//
// Uso (lo lanza carga_cm.py ejecutar, que levanta la API y prepara el token):
//   k6 run -e BASE_URL=http://127.0.0.1:8001 -e TOKEN=... -e DATOS=datos.json \
//          -e RESUMEN=resumen.json testing/carga/cm_consulta.js

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8001';
const TOKEN = __ENV.TOKEN;
const ESCENARIO = __ENV.ESCENARIO || 'carga';
const VUS = parseInt(__ENV.VUS || '50', 10);
const DURACION = __ENV.DURACION || '3m';
const RESUMEN = __ENV.RESUMEN || 'resumen.json';
const DATOS = JSON.parse(open(__ENV.DATOS || './datos.json'));

const PREFIJO = '/internal/v1/codemapping/grafo';
const CODIGOS_CATALOGO = new Set(DATOS.codigos_error);

// Mezcla de operaciones de un agente antes de actuar: sobre todo impacto y dependencias.
const MEZCLA = [
  ['impacto', 25],
  ['dependencias', 15],
  ['semillas', 10],
  ['resolver_nodos', 10],
  ['subgrafo', 10],
  ['vecinos', 10],
  ['dependencias_ocultas', 5],
  ['buscar_hibrido', 5],
  ['resolver_nuggets', 5],
  ['resolver_incidentes', 5],
];
const PESO_TOTAL = MEZCLA.reduce((a, [, p]) => a + p, 0);

const noCatalogadas = new Counter('respuestas_no_catalogadas');
const errores = new Rate('errores');
const dependenciaCaida = new Counter('respuestas_dependencia_no_disponible');
// Solo las primeras de cada VU van al log: diagnóstico sin inundar la salida.
let noCatalogadasDelVU = 0;

function umbrales() {
  const t = {
    respuestas_no_catalogadas: ['count==0'],
  };
  if (ESCENARIO === 'carga') {
    t.errores = ['rate<0.01'];
    for (const [op, ms] of Object.entries(DATOS.presupuestos_p95_ms)) {
      t[`http_req_duration{op:${op}}`] = [`p(95)<${ms}`];
    }
  }
  return t;
}

export const options = {
  scenarios: {
    agentes: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURACION,
      gracefulStop: '10s',
    },
  },
  thresholds: umbrales(),
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
};

function elegirOperacion() {
  let r = Math.random() * PESO_TOTAL;
  for (const [op, peso] of MEZCLA) {
    r -= peso;
    if (r < 0) return op;
  }
  return MEZCLA[0][0];
}

function azar(lista) {
  return lista[Math.floor(Math.random() * lista.length)];
}

function cuerpo(op) {
  const s = azar(DATOS.semillas);
  switch (op) {
    case 'resolver_nodos':
      return { ids: [s.nodo, 'no_existe'] };
    case 'semillas':
      return { nodos: [s.nodo], ficheros: [s.fichero] };
    case 'impacto':
    case 'dependencias':
    case 'dependencias_ocultas':
      return { nodos: [s.nodo], k: 3 };
    case 'subgrafo':
      return { nodos: [s.nodo], k: 2 };
    case 'vecinos':
      return { nodo: s.nodo };
    case 'buscar_hibrido':
      return { embedding: azar(DATOS.vectores), k: 10 };
    case 'resolver_nuggets':
      return { variable: 'nuggets', nodos: [s.nodo] };
    case 'resolver_incidentes':
      return { nodos: [s.nodo], saltos: 2 };
    default:
      throw new Error(`operación desconocida: ${op}`);
  }
}

function catalogada(res) {
  // Una respuesta de error está catalogada si trae el sobre estándar con un código del catálogo.
  try {
    const error = res.json('error');
    return error !== null && error !== undefined && CODIGOS_CATALOGO.has(error.codigo);
  } catch (_) {
    return false;
  }
}

export default function () {
  const op = elegirOperacion();
  const res = http.post(`${BASE_URL}${PREFIJO}${DATOS.rutas[op]}`, JSON.stringify(cuerpo(op)), {
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': `carga-${__VU}-${__ITER}`,
    },
    tags: { op },
    timeout: '30s',
  });

  const ok = res.status === 200;
  errores.add(!ok);
  if (!ok) {
    if (res.status >= 400 && catalogada(res)) {
      if (res.json('error.codigo') === 'DEPENDENCIA_NO_DISPONIBLE') dependenciaCaida.add(1, { op });
    } else {
      // 500 sin sobre, timeout del cliente (status 0) o código fuera del catálogo.
      noCatalogadas.add(1, { op, status: String(res.status) });
      if (noCatalogadasDelVU++ < 2) {
        console.warn(
          `sin catalogar: ${op} status=${res.status} error=${res.error} ` +
            `cuerpo=${String(res.body).slice(0, 200)}`,
        );
      }
    }
  }
  check(res, {
    'respuesta 200 o error del catálogo': (r) => r.status === 200 || catalogada(r),
    'una caída de la base trae Retry-After': (r) =>
      r.status !== 503 || r.headers['Retry-After'] !== undefined,
  });
}

export function handleSummary(data) {
  return { [RESUMEN]: JSON.stringify(data, null, 2) };
}
