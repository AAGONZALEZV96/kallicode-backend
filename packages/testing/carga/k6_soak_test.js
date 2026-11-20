/**
 * Script de Prueba de Fuga de Memoria y Resistencia (Soak Testing) con k6 (KC-QA-B6-09).
 *
 * Épica: EQA1 — CI/CD y Entornos de Prueba (Bloque 6 - Hito M6 GA)
 *
 * Modo completo nocturno (4 horas, 15 VUs constantes):
 *   k6 run -e SOAK_FAST_MODE=false -e BASE_URL=http://localhost:8000 packages/testing/carga/k6_soak_test.js
 *
 * Modo rápido sintético (30s por defecto):
 *   k6 run -e SOAK_FAST_MODE=true -e SOAK_DURATION=30s packages/testing/carga/k6_soak_test.js
 *
 * Validación CLI directa:
 *   k6 run --vus 5 --duration 10s packages/testing/carga/k6_soak_test.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Gauge, Rate, Trend } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Parametrización Dual por Entorno (Cero credenciales hardcodeadas)
// ---------------------------------------------------------------------------
const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';
const TENANT_ID = __ENV.TENANT_ID || 'tenant-qa-soak-001';
const TICKET_ID = __ENV.TICKET_ID || 'KC-1001';
const SUMMARY_PATH = __ENV.SUMMARY_PATH || 'soak-summary.json';

const FAST_MODE = (__ENV.SOAK_FAST_MODE || 'false').toLowerCase() === 'true';
const SOAK_DURATION = __ENV.SOAK_DURATION || (FAST_MODE ? '30s' : '4h');
const SOAK_VUS = parseInt(__ENV.SOAK_VUS || (FAST_MODE ? '10' : '15'), 10);
const WARMUP_SECONDS = FAST_MODE ? 5 : 1800; // 30 min warm-up en corrida de 4h

// ---------------------------------------------------------------------------
// Métricas de Estabilidad, Latencia y Memoria RSS
// ---------------------------------------------------------------------------
const http5xxErrors = new Rate('http_5xx_errors');
const oomKilledCount = new Counter('soak_oom_killed_count');
const memoryRssGauge = new Gauge('soak_memory_rss_mb');
const memoryRssTrend = new Trend('soak_memory_rss_trend', false);

const warmupDuration = new Trend('soak_warmup_duration', true);
const lateDuration = new Trend('soak_final_duration', true);
const kanbanDuration = new Trend('soak_kanban_duration', true);
const ticketDuration = new Trend('soak_ticket_detail_duration', true);
const healthDuration = new Trend('soak_health_duration', true);

export const options = {
  vus: SOAK_VUS,
  duration: SOAK_DURATION,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: {
    http_5xx_errors: ['rate==0'],
    soak_oom_killed_count: ['count==0'],
    http_req_duration: ['p(95)<500'],
  },
};

function buildHeaders() {
  const headers = {
    'Content-Type': 'application/json',
    'X-KalliCode-Tenant-ID': TENANT_ID,
    'X-KalliCode-User-ID': `soak-vu-${__VU || 0}`,
  };
  if (AUTH_TOKEN) {
    headers.Authorization = `Bearer ${AUTH_TOKEN}`;
  }
  return headers;
}

function parsePrometheusRssMb(metricsBody) {
  if (!metricsBody || typeof metricsBody !== 'string') {
    return null;
  }
  const rssMbMatch = metricsBody.match(/^kallicode_process_rss_mb(?:\{[^}]*\})?\s+([0-9.eE+-]+)/m);
  if (rssMbMatch) {
    return parseFloat(rssMbMatch[1]);
  }
  const rssBytesMatch = metricsBody.match(
    /^process_resident_memory_bytes(?:\{[^}]*\})?\s+([0-9.eE+-]+)/m,
  );
  if (rssBytesMatch) {
    return parseFloat(rssBytesMatch[1]) / (1024.0 * 1024.0);
  }
  return null;
}

function recordRequestTelemetry(res, targetMetric, startTimeMs) {
  const is5xx = res.status >= 500 || res.status === 0;
  http5xxErrors.add(is5xx);
  if (res.status === 502 || res.status === 503 || res.status === 0) {
    const bodyText = String(res.body || '').toLowerCase();
    if (bodyText.includes('oom') || bodyText.includes('out of memory')) {
      oomKilledCount.add(1);
    }
  }
  targetMetric.add(res.timings.duration);

  const elapsedSec = (Date.now() - startTimeMs) / 1000.0;
  if (elapsedSec <= WARMUP_SECONDS) {
    warmupDuration.add(res.timings.duration);
  } else {
    lateDuration.add(res.timings.duration);
  }
}

export function setup() {
  return {
    startedAtMs: Date.now(),
  };
}

export default function (data) {
  const headers = buildHeaders();
  const startedAtMs = data && data.startedAtMs ? data.startedAtMs : Date.now();
  const selector = __ITER % 3;

  if (selector === 0) {
    const res = http.get(`${BASE_URL}/api/v1/tickets?vista=kanban`, {
      headers,
      tags: { endpoint: 'kanban_board', suite: 'soak' },
      timeout: '5s',
    });
    recordRequestTelemetry(res, kanbanDuration, startedAtMs);
    check(res, {
      'soak kanban: HTTP < 500 y distinto de 404': (r) =>
        r.status > 0 && r.status < 500 && r.status !== 404,
    });
  } else if (selector === 1) {
    const res = http.get(`${BASE_URL}/api/v1/tickets/${TICKET_ID}`, {
      headers,
      tags: { endpoint: 'ticket_detail', suite: 'soak' },
      timeout: '5s',
    });
    recordRequestTelemetry(res, ticketDuration, startedAtMs);
    check(res, {
      'soak ticket: HTTP < 500': (r) => r.status > 0 && r.status < 500,
    });
  } else {
    const res = http.get(`${BASE_URL}/health`, {
      headers,
      tags: { endpoint: 'health', suite: 'soak' },
      timeout: '5s',
    });
    recordRequestTelemetry(res, healthDuration, startedAtMs);
    check(res, {
      'soak health: HTTP 200': (r) => r.status === 200,
    });
  }

  // Sondeo periódico de consumo RSS vía Prometheus /metrics
  if (__VU === 1 && __ITER % 10 === 0) {
    const metricsRes = http.get(`${BASE_URL}/metrics`, {
      headers,
      tags: { endpoint: 'metrics_rss', suite: 'soak' },
      timeout: '5s',
    });
    if (metricsRes.status === 200) {
      const rssMb = parsePrometheusRssMb(metricsRes.body);
      if (rssMb !== null && Number.isFinite(rssMb)) {
        memoryRssGauge.add(rssMb);
        memoryRssTrend.add(rssMb);
      }
    }
  }

  sleep(0.5);
}

export function handleSummary(data) {
  const totalDurationSec =
    data.state && data.state.testRunDurationMs ? data.state.testRunDurationMs / 1000.0 : 0.0;

  const enriched = {
    ...data,
    kallicode_soak_metadata: {
      ticket: 'KC-QA-B6-09',
      fast_mode: FAST_MODE,
      configured_duration: SOAK_DURATION,
      configured_vus: SOAK_VUS,
      total_duration_seconds: totalDurationSec,
      warmup_seconds: WARMUP_SECONDS,
    },
  };

  return {
    [SUMMARY_PATH]: JSON.stringify(enriched, null, 2),
  };
}
