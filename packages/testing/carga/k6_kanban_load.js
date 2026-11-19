/**
 * Script de Pruebas de Carga de Módulos Core con k6 (KC-QA-B6-01).
 *
 * Épica: EQA8 — Bloque 6 (Carga, Resiliencia & Release)
 * Decisión Arquitectónica: D16 (Presupuestos de carga y límite comercial 120 req/min)
 *
 * Uso completo (100 VUs, 6 minutos totales):
 *   k6 run -e BASE_URL=http://localhost:8000 -e AUTH_TOKEN=... packages/testing/carga/k6_kanban_load.js
 *
 * Uso sintético de validación rápida:
 *   k6 run --vus 5 --duration 10s -e BASE_URL=http://localhost:8000 packages/testing/carga/k6_kanban_load.js
 */

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Configuración por entorno (Cero credenciales hardcodeadas)
// ---------------------------------------------------------------------------
const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';
const RATE_LIMIT_TOKEN = __ENV.RATE_LIMIT_TOKEN || AUTH_TOKEN;
const TENANT_ID = __ENV.TENANT_ID || 'tenant-qa-load-001';
const TICKET_ID = __ENV.TICKET_ID || 'KC-1001';
const GATE_ID = __ENV.GATE_ID || '1';
const GATE_ACTION = __ENV.GATE_ACTION || 'firma';
const SUMMARY_PATH = __ENV.SUMMARY_PATH || 'k6-summary.json';
const ENABLE_D16_BURST = (__ENV.ENABLE_D16_BURST || 'true').toLowerCase() === 'true';
const D16_LIMIT_PER_MIN = 120;

// ---------------------------------------------------------------------------
// Métricas personalizadas por endpoint y cumplimiento D16
// ---------------------------------------------------------------------------
const http5xxErrors = new Rate('http_5xx_errors');
const kanbanDuration = new Trend('http_req_duration_kanban_board', true);
const ticketDetailDuration = new Trend('http_req_duration_ticket_detail', true);
const gateSignDuration = new Trend('http_req_duration_gate_sign', true);

const kanban5xxRate = new Rate('http_5xx_errors_kanban_board');
const ticketDetail5xxRate = new Rate('http_5xx_errors_ticket_detail');
const gateSign5xxRate = new Rate('http_5xx_errors_gate_sign');

const d16RateLimitVerified = new Rate('d16_rate_limit_verified');
const d16429Hits = new Counter('d16_429_hits');
const d16RetryAfterPresent = new Counter('d16_retry_after_present');

// ---------------------------------------------------------------------------
// Opciones k6: Stages compatibles con override CLI (--vus / --duration)
// ---------------------------------------------------------------------------
export const options = {
  stages: [
    { duration: '2m', target: 100 }, // Ramp-up a 100 VUs en 2 minutos
    { duration: '3m', target: 100 }, // Sostenimiento de carga durante 3 minutos
    { duration: '1m', target: 0 },   // Ramp-down durante 1 minuto
  ],
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: {
    'http_req_duration{endpoint:kanban_board}': ['p(95)<200', 'p(99)<500'],
    http_req_duration: ['p(99)<500'],
    http_5xx_errors: ['rate==0'],
  },
};

function buildHeaders(token, extraHeaders = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'X-KalliCode-Tenant-ID': TENANT_ID,
    ...extraHeaders,
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function getHeaderCaseInsensitive(headers, targetName) {
  if (!headers) {
    return undefined;
  }
  const lower = targetName.toLowerCase();
  for (const [k, v] of Object.entries(headers)) {
    if (k.toLowerCase() === lower) {
      return v;
    }
  }
  return undefined;
}

function record5xx(res, endpointRateMetric) {
  const isServerError = res.status >= 500 || res.status === 0;
  http5xxErrors.add(isServerError);
  endpointRateMetric.add(isServerError);
  return !isServerError;
}

// ---------------------------------------------------------------------------
// Setup: Verificación de límite de tasa comercial D16 (120 req/min/usuario)
// ---------------------------------------------------------------------------
export function setup() {
  if (!ENABLE_D16_BURST) {
    return {
      d16Executed: false,
      d16Verified: false,
      req121Status: null,
      retryAfterPresent: false,
    };
  }

  let preLimitClean = true;
  let req121Status = 0;
  let retryAfterPresent = false;
  const headers = buildHeaders(RATE_LIMIT_TOKEN, {
    'X-KalliCode-User-ID': 'qa-d16-burst-user',
  });

  // Ejecutar exactamente 120 peticiones dentro de la ventana de 1 minuto
  for (let i = 1; i <= D16_LIMIT_PER_MIN; i += 1) {
    const res = http.get(`${BASE_URL}/api/v1/tickets?vista=kanban`, {
      headers,
      tags: { endpoint: 'rate_limit_d16', method: 'GET', fase: 'd16_within_limit' },
      timeout: '10s',
    });
    if (res.status === 429 || res.status >= 500 || res.status === 0) {
      preLimitClean = false;
    }
  }

  // Petición 121 debe recibir HTTP 429 Too Many Requests con cabecera Retry-After
  const res121 = http.get(`${BASE_URL}/api/v1/tickets?vista=kanban`, {
    headers,
    tags: { endpoint: 'rate_limit_d16', method: 'GET', fase: 'd16_exceeded' },
    timeout: '10s',
  });

  req121Status = res121.status;
  const retryAfterVal = getHeaderCaseInsensitive(res121.headers, 'Retry-After');
  retryAfterPresent = retryAfterVal !== undefined && String(retryAfterVal).trim() !== '';

  if (req121Status === 429) {
    d16429Hits.add(1);
  }
  if (retryAfterPresent) {
    d16RetryAfterPresent.add(1);
  }

  const d16Verified = preLimitClean && req121Status === 429 && retryAfterPresent;
  d16RateLimitVerified.add(d16Verified);

  check(res121, {
    'D16: primeras 120 req/min aceptadas': () => preLimitClean,
    'D16: petición 121 recibe HTTP 429': (r) => r.status === 429,
    'D16: respuesta 429 incluye cabecera Retry-After': () => retryAfterPresent,
  });

  return {
    d16Executed: true,
    d16Verified,
    req121Status,
    retryAfterPresent,
  };
}

// ---------------------------------------------------------------------------
// Flujo principal de cada VU: Operaciones Core del Tablero Kanban
// ---------------------------------------------------------------------------
export default function () {
  const headers = buildHeaders(AUTH_TOKEN, {
    'X-KalliCode-User-ID': `qa-vu-${__VU}`,
  });

  // 1. Consulta del tablero Kanban
  group('kanban_board', () => {
    const res = http.get(`${BASE_URL}/api/v1/tickets?vista=kanban`, {
      headers,
      tags: { endpoint: 'kanban_board', method: 'GET' },
      timeout: '10s',
    });

    kanbanDuration.add(res.timings.duration);
    record5xx(res, kanban5xxRate);

    check(res, {
      'Kanban: sin error 5xx': (r) => r.status > 0 && r.status < 500,
      'Kanban: no devuelve 404': (r) => r.status !== 404,
      'Kanban: status 200 o 429 controlado': (r) => r.status === 200 || r.status === 429,
      'Kanban: latencia < 200ms': (r) => r.timings.duration < 200,
    });
  });

  // 2. Detalle de ticket
  group('ticket_detail', () => {
    const res = http.get(`${BASE_URL}/api/v1/tickets/${TICKET_ID}`, {
      headers,
      tags: { endpoint: 'ticket_detail', method: 'GET' },
      timeout: '10s',
    });

    ticketDetailDuration.add(res.timings.duration);
    record5xx(res, ticketDetail5xxRate);

    check(res, {
      'Ticket Detail: sin error 5xx': (r) => r.status > 0 && r.status < 500,
      'Ticket Detail: no devuelve 404': (r) => r.status !== 404,
      'Ticket Detail: status 200 o 429 controlado': (r) => r.status === 200 || r.status === 429,
    });
  });

  // 3. Firma de Gate
  group('gate_sign', () => {
    const payload = JSON.stringify({
      comentario: 'Aprobación automática bajo prueba de carga KC-QA-B6-01',
    });
    const postHeaders = buildHeaders(AUTH_TOKEN, {
      'X-KalliCode-User-ID': `qa-vu-${__VU}`,
      'Idempotency-Key': `k6-gate-${__VU}-${__ITER}`,
    });

    const res = http.post(
      `${BASE_URL}/api/v1/tickets/${TICKET_ID}/gates/${GATE_ID}/${GATE_ACTION}`,
      payload,
      {
        headers: postHeaders,
        tags: { endpoint: 'gate_sign', method: 'POST' },
        timeout: '10s',
      },
    );

    gateSignDuration.add(res.timings.duration);
    record5xx(res, gateSign5xxRate);

    check(res, {
      'Gate Sign: sin error 5xx': (r) => r.status > 0 && r.status < 500,
      'Gate Sign: no devuelve 404': (r) => r.status !== 404,
      'Gate Sign: status esperado (200/201/409/429)': (r) =>
        r.status === 200 || r.status === 201 || r.status === 409 || r.status === 429,
    });
  });

  // Pacing para respetar ~120 req/min por usuario virtual cuando se simula carga sostenida
  sleep(1.5);
}

// ---------------------------------------------------------------------------
// Exportación del resumen estructurado k6-summary.json para LoadReporter
// ---------------------------------------------------------------------------
export function handleSummary(data) {
  const d16Metric = data.metrics && data.metrics.d16_rate_limit_verified;
  const d16Passes = d16Metric && d16Metric.values ? d16Metric.values.passes || 0 : 0;
  const d16Fails = d16Metric && d16Metric.values ? d16Metric.values.fails || 0 : 0;
  const d16Verified = d16Passes > 0 && d16Fails === 0;

  const enrichedSummary = {
    ...data,
    kallicode_metadata: {
      ticket: 'KC-QA-B6-01',
      decision: 'D16',
      rate_limit_per_min: D16_LIMIT_PER_MIN,
      rate_limit_verified: d16Verified,
      endpoints: [
        {
          key: 'kanban_board',
          endpoint: '/api/v1/tickets?vista=kanban',
          method: 'GET',
        },
        {
          key: 'ticket_detail',
          endpoint: `/api/v1/tickets/${TICKET_ID}`,
          method: 'GET',
        },
        {
          key: 'gate_sign',
          endpoint: `/api/v1/tickets/${TICKET_ID}/gates/${GATE_ID}/${GATE_ACTION}`,
          method: 'POST',
        },
      ],
    },
  };

  return {
    [SUMMARY_PATH]: JSON.stringify(enrichedSummary, null, 2),
  };
}
