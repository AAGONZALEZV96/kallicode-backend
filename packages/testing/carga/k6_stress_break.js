/**
 * Script de Pruebas de Estrés y Punto de Ruptura con k6 (KC-QA-B6-02).
 *
 * Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
 *
 * Perfil por defecto (8 minutos totales):
 *   20 VUs (1m) -> 50 VUs (1m) -> 100 VUs (2m) -> 200 VUs (2m) -> 300 VUs (1m) -> Cooldown 0 VUs (1m)
 *
 * Modo rápido seguro local:
 *   k6 run -e STRESS_FAST_MODE=true -e BASE_URL=http://localhost:8000 packages/testing/carga/k6_stress_break.js
 *
 * Validación sintética CLI:
 *   k6 run --vus 5 --duration 10s -e BASE_URL=http://localhost:8000 packages/testing/carga/k6_stress_break.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Configuración por entorno (Cero credenciales hardcodeadas)
// ---------------------------------------------------------------------------
const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';
const TENANT_ID = __ENV.TENANT_ID || 'tenant-qa-stress-001';
const TICKET_ID = __ENV.TICKET_ID || 'KC-1001';
const SUMMARY_PATH = __ENV.SUMMARY_PATH || 'stress-summary.json';
const FAST_MODE = (__ENV.STRESS_FAST_MODE || 'false').toLowerCase() === 'true';
const RECOVERY_PROBES = parseInt(__ENV.RECOVERY_PROBES || '10', 10);

const STAGE_PLAN = FAST_MODE
  ? [
      { name: 'stage_20_vus', duration: '5s', durationSec: 5, target: 5 },
      { name: 'stage_50_vus', duration: '5s', durationSec: 5, target: 10 },
      { name: 'stage_100_vus', duration: '5s', durationSec: 5, target: 15 },
      { name: 'stage_200_vus', duration: '5s', durationSec: 5, target: 20 },
      { name: 'stage_300_vus', duration: '5s', durationSec: 5, target: 25 },
      { name: 'cooldown', duration: '5s', durationSec: 5, target: 0 },
    ]
  : [
      { name: 'stage_20_vus', duration: '1m', durationSec: 60, target: 20 },
      { name: 'stage_50_vus', duration: '1m', durationSec: 60, target: 50 },
      { name: 'stage_100_vus', duration: '2m', durationSec: 120, target: 100 },
      { name: 'stage_200_vus', duration: '2m', durationSec: 120, target: 200 },
      { name: 'stage_300_vus', duration: '1m', durationSec: 60, target: 300 },
      { name: 'cooldown', duration: '1m', durationSec: 60, target: 0 },
    ];

// ---------------------------------------------------------------------------
// Métricas por etapa, errores específicos (500/502/503/504/timeout) y recuperación
// ---------------------------------------------------------------------------
const stageMetrics = {
  stage_20_vus: {
    duration: new Trend('stress_stage_20_vus_duration', true),
    errors: new Rate('stress_stage_20_vus_errors'),
    reqs: new Counter('stress_stage_20_vus_reqs'),
  },
  stage_50_vus: {
    duration: new Trend('stress_stage_50_vus_duration', true),
    errors: new Rate('stress_stage_50_vus_errors'),
    reqs: new Counter('stress_stage_50_vus_reqs'),
  },
  stage_100_vus: {
    duration: new Trend('stress_stage_100_vus_duration', true),
    errors: new Rate('stress_stage_100_vus_errors'),
    reqs: new Counter('stress_stage_100_vus_reqs'),
  },
  stage_200_vus: {
    duration: new Trend('stress_stage_200_vus_duration', true),
    errors: new Rate('stress_stage_200_vus_errors'),
    reqs: new Counter('stress_stage_200_vus_reqs'),
  },
  stage_300_vus: {
    duration: new Trend('stress_stage_300_vus_duration', true),
    errors: new Rate('stress_stage_300_vus_errors'),
    reqs: new Counter('stress_stage_300_vus_reqs'),
  },
  cooldown: {
    duration: new Trend('stress_cooldown_duration', true),
    errors: new Rate('stress_cooldown_errors'),
    reqs: new Counter('stress_cooldown_reqs'),
  },
};

const http500Count = new Counter('stress_http_500_count');
const http502Count = new Counter('stress_http_502_count');
const http503Count = new Counter('stress_http_503_count');
const http504Count = new Counter('stress_http_504_count');
const timeoutCount = new Counter('stress_timeout_count');

const recoveryDuration = new Trend('recovery_req_duration', true);
const recoveryErrorRate = new Rate('recovery_error_rate');
const recoveryReqs = new Counter('recovery_reqs_count');

// ---------------------------------------------------------------------------
// Opciones k6: Stages compatibles con override CLI (--vus / --duration)
// ---------------------------------------------------------------------------
export const options = {
  stages: STAGE_PLAN.map((s) => ({ duration: s.duration, target: s.target })),
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: {
    recovery_req_duration: ['p(95)<300'],
    recovery_error_rate: ['rate==0'],
  },
};

function buildHeaders() {
  const headers = {
    'Content-Type': 'application/json',
    'X-KalliCode-Tenant-ID': TENANT_ID,
    'X-KalliCode-User-ID': `stress-vu-${__VU || 0}`,
  };
  if (AUTH_TOKEN) {
    headers.Authorization = `Bearer ${AUTH_TOKEN}`;
  }
  return headers;
}

function resolveStageName(elapsedSec) {
  let cursor = 0;
  for (const stage of STAGE_PLAN) {
    cursor += stage.durationSec;
    if (elapsedSec <= cursor) {
      return stage.name;
    }
  }
  return 'cooldown';
}

function classifyFailure(res) {
  if (res.status === 0 || (res.error_code !== undefined && res.error_code !== 0)) {
    timeoutCount.add(1);
    return true;
  }
  if (res.status === 500) {
    http500Count.add(1);
    return true;
  }
  if (res.status === 502) {
    http502Count.add(1);
    return true;
  }
  if (res.status === 503) {
    http503Count.add(1);
    return true;
  }
  if (res.status === 504) {
    http504Count.add(1);
    return true;
  }
  return res.status >= 500;
}

export function setup() {
  return { startTimeMs: Date.now() };
}

export default function (data) {
  const elapsedSec = (Date.now() - data.startTimeMs) / 1000.0;
  const stageName = resolveStageName(elapsedSec);
  const metrics = stageMetrics[stageName];
  const headers = buildHeaders();

  const endpointUrl =
    __ITER % 2 === 0
      ? `${BASE_URL}/api/v1/tickets?vista=kanban`
      : `${BASE_URL}/api/v1/tickets/${TICKET_ID}`;

  const res = http.get(endpointUrl, {
    headers,
    tags: { stage: stageName, method: 'GET' },
    timeout: '5s',
  });

  const failed = classifyFailure(res);
  metrics.duration.add(res.timings.duration);
  metrics.errors.add(failed);
  metrics.reqs.add(1);

  check(res, {
    'stress: respuesta recibida sin timeout': (r) => r.status > 0,
    'stress: no devuelve 404': (r) => r.status !== 404,
  });

  sleep(0.2);
}

// ---------------------------------------------------------------------------
// Teardown: Sondeo de recuperación automática post-estrés (p95 < 300ms, 0 errores)
// ---------------------------------------------------------------------------
export function teardown() {
  const headers = buildHeaders();
  for (let i = 0; i < RECOVERY_PROBES; i += 1) {
    const res = http.get(`${BASE_URL}/api/v1/tickets?vista=kanban`, {
      headers,
      tags: { stage: 'recovery_cooldown', method: 'GET' },
      timeout: '5s',
    });

    const failed = res.status === 0 || res.status >= 500 || res.status === 404;
    recoveryDuration.add(res.timings.duration);
    recoveryErrorRate.add(failed);
    recoveryReqs.add(1);

    check(res, {
      'cooldown recovery: HTTP 2xx/429 sin 5xx/404': (r) =>
        r.status > 0 && r.status < 500 && r.status !== 404,
      'cooldown recovery: latencia < 300ms': (r) => r.timings.duration < 300,
    });
  }
}

// ---------------------------------------------------------------------------
// Exportación stress-summary.json para StressReporter
// ---------------------------------------------------------------------------
export function handleSummary(data) {
  const totalDurationSec =
    data.state && data.state.testRunDurationMs
      ? data.state.testRunDurationMs / 1000.0
      : 0.0;

  const enriched = {
    ...data,
    kallicode_stress_metadata: {
      ticket: 'KC-QA-B6-02',
      fast_mode: FAST_MODE,
      total_duration_seconds: totalDurationSec,
      stages: STAGE_PLAN.map((s) => ({
        name: s.name,
        vus_target: s.target,
        duration_seconds: s.durationSec,
      })),
    },
  };

  return {
    [SUMMARY_PATH]: JSON.stringify(enriched, null, 2),
  };
}
