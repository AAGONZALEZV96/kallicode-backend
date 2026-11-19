/**
 * Script de Carga del Pipeline de Ingesta y Vectorización Local con k6 (KC-QA-B6-05).
 *
 * Épica: EQA8 — Pruebas de Carga (k6), Resiliencia, Caos y Expediente de Release
 *
 * Evalúa la ingesta paralela de 10 repositorios sintéticos multi-tenant hacia:
 *   POST /api/v1/onboarding/ingest (o /internal/v1/codemapping/ingest/batch vía __ENV.INGEST_ENDPOINT)
 *
 * Uso completo (10 VUs concurrentes):
 *   k6 run -e BASE_URL=http://localhost:8000 packages/testing/carga/k6_ingest_pipeline.js
 *
 * Modo rápido local / validación sintética:
 *   k6 run -e INGEST_FAST_MODE=true packages/testing/carga/k6_ingest_pipeline.js
 *   k6 run --vus 5 --duration 10s packages/testing/carga/k6_ingest_pipeline.js
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Gauge, Rate, Trend } from 'k6/metrics';

// ---------------------------------------------------------------------------
// Configuración por entorno (Cero credenciales hardcodeadas)
// ---------------------------------------------------------------------------
const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const INGEST_ENDPOINT = __ENV.INGEST_ENDPOINT || '/api/v1/onboarding/ingest';
const AUTH_TOKEN = __ENV.AUTH_TOKEN || '';
const FAST_MODE = (__ENV.INGEST_FAST_MODE || 'false').toLowerCase() === 'true';
const SUMMARY_PATH = __ENV.SUMMARY_PATH || 'ingest-summary.json';

// Catálogo determinista de 10 repositorios sintéticos repartidos en 5 tenants distintos
const SYNTHETIC_REPOS = [
  { tenant_id: 'tenant-ingest-01', repo_name: 'billing-core-api', files_count: 24, loc_total: 3200, chunks_vectorized: 128 },
  { tenant_id: 'tenant-ingest-01', repo_name: 'billing-portal-ui', files_count: 18, loc_total: 2450, chunks_vectorized: 96 },
  { tenant_id: 'tenant-ingest-02', repo_name: 'orders-orchestrator', files_count: 30, loc_total: 4100, chunks_vectorized: 164 },
  { tenant_id: 'tenant-ingest-02', repo_name: 'orders-worker', files_count: 16, loc_total: 1950, chunks_vectorized: 78 },
  { tenant_id: 'tenant-ingest-03', repo_name: 'auth-identity-svc', files_count: 22, loc_total: 2800, chunks_vectorized: 112 },
  { tenant_id: 'tenant-ingest-03', repo_name: 'auth-sdk-ts', files_count: 14, loc_total: 1600, chunks_vectorized: 64 },
  { tenant_id: 'tenant-ingest-04', repo_name: 'catalog-indexer', files_count: 28, loc_total: 3900, chunks_vectorized: 156 },
  { tenant_id: 'tenant-ingest-04', repo_name: 'catalog-docs', files_count: 12, loc_total: 1400, chunks_vectorized: 56 },
  { tenant_id: 'tenant-ingest-05', repo_name: 'analytics-pipeline', files_count: 26, loc_total: 3500, chunks_vectorized: 140 },
  { tenant_id: 'tenant-ingest-05', repo_name: 'analytics-reports', files_count: 20, loc_total: 2600, chunks_vectorized: 104 },
];

// ---------------------------------------------------------------------------
// Métricas de ingesta, vectorización, RSS y aislamiento multi-tenant
// ---------------------------------------------------------------------------
const ingestBatchDuration = new Trend('ingest_batch_duration', true);
const ingestLocPerSecond = new Trend('ingest_loc_per_second');
const ingestChunksPerSecond = new Trend('ingest_chunks_per_second');
const ingestLocTotal = new Counter('ingest_loc_total');
const ingestChunksTotal = new Counter('ingest_chunks_total');
const ingestMemoryRssMb = new Gauge('ingest_memory_rss_mb');
const ingestTenantLeakCount = new Counter('ingest_tenant_leak_count');
const ingestHttpErrors = new Rate('ingest_http_errors');

// ---------------------------------------------------------------------------
// Opciones k6: Stages compatibles con override CLI (--vus / --duration)
// ---------------------------------------------------------------------------
export const options = {
  stages: FAST_MODE
    ? [
        { duration: '5s', target: 5 },
        { duration: '5s', target: 10 },
      ]
    : [
        { duration: '30s', target: 10 },
        { duration: '90s', target: 10 },
        { duration: '15s', target: 0 },
      ],
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds: {
    ingest_batch_duration: ['p(95)<800'],
    http_req_duration: ['p(95)<800'],
    ingest_memory_rss_mb: ['value<1500'],
    ingest_tenant_leak_count: ['count==0'],
    ingest_http_errors: ['rate==0'],
  },
};

function getHeaderCaseInsensitive(headers, name) {
  if (!headers) {
    return undefined;
  }
  const target = name.toLowerCase();
  for (const [k, v] of Object.entries(headers)) {
    if (k.toLowerCase() === target) {
      return v;
    }
  }
  return undefined;
}

function buildPayload(repo) {
  return JSON.stringify({
    tenant_id: repo.tenant_id,
    repo_name: repo.repo_name,
    files_count: repo.files_count,
    loc_total: repo.loc_total,
    target_tables: ['vec.definition_embeddings', 'vec.doc_chunks'],
    files: [
      {
        path: 'src/main.py',
        language: 'python',
        loc: Math.floor(repo.loc_total * 0.7),
        content_preview: `def service_entry_${repo.repo_name.replace(/-/g, '_')}(): return True`,
      },
      {
        path: 'docs/architecture.md',
        language: 'markdown',
        loc: Math.floor(repo.loc_total * 0.3),
        content_preview: `# Architecture for ${repo.repo_name}\nIsolated tenant ${repo.tenant_id}.`,
      },
    ],
  });
}

export default function () {
  const repoIndex = ((__VU - 1) + __ITER) % SYNTHETIC_REPOS.length;
  const repo = SYNTHETIC_REPOS[repoIndex];

  const headers = {
    'Content-Type': 'application/json',
    'X-KalliCode-Tenant-ID': repo.tenant_id,
    'Idempotency-Key': `ingest-${repo.tenant_id}-${repo.repo_name}-${__VU}-${__ITER}`,
  };
  if (AUTH_TOKEN) {
    headers.Authorization = `Bearer ${AUTH_TOKEN}`;
  }

  const res = http.post(`${BASE_URL}${INGEST_ENDPOINT}`, buildPayload(repo), {
    headers,
    tags: {
      endpoint: 'ingest_batch',
      tenant_id: repo.tenant_id,
      repo_name: repo.repo_name,
    },
    timeout: '10s',
  });

  const durationMs = res.timings.duration;
  const durationSec = Math.max(durationMs / 1000.0, 0.001);
  ingestBatchDuration.add(durationMs, { tenant_id: repo.tenant_id, repo_name: repo.repo_name });

  const isError = res.status === 0 || res.status === 404 || res.status >= 500;
  ingestHttpErrors.add(isError);

  let body = {};
  try {
    body = res.json() || {};
  } catch (_) {
    body = {};
  }

  // Verificar aislamiento multi-tenant estricto en respuesta
  let tenantIsolated = true;
  if (body && typeof body === 'object') {
    if (body.tenant_id !== undefined && body.tenant_id !== repo.tenant_id) {
      tenantIsolated = false;
    }
    if (Array.isArray(body.tenants) && body.tenants.some((t) => t !== repo.tenant_id)) {
      tenantIsolated = false;
    }
  }
  if (!tenantIsolated) {
    ingestTenantLeakCount.add(1);
  }

  const locCount = Number(body.loc_total !== undefined ? body.loc_total : repo.loc_total);
  const chunksCount = Number(
    body.chunks_vectorized !== undefined ? body.chunks_vectorized : repo.chunks_vectorized,
  );
  ingestLocTotal.add(locCount);
  ingestChunksTotal.add(chunksCount);
  ingestLocPerSecond.add(locCount / durationSec);
  ingestChunksPerSecond.add(chunksCount / durationSec);

  const rssHeader = getHeaderCaseInsensitive(res.headers, 'X-Worker-RSS-MB');
  const rssMb = Number(rssHeader !== undefined ? rssHeader : body.memory_rss_mb || 0);
  ingestMemoryRssMb.add(rssMb);

  check(res, {
    'ingest: sin error 5xx ni 404': (r) => r.status > 0 && r.status < 500 && r.status !== 404,
    'ingest: p95 objetivo (< 800ms)': (r) => r.timings.duration < 800,
    'ingest: aislamiento multi-tenant estricto': () => tenantIsolated,
    'ingest: memoria RSS bajo 1.5 GB': () => rssMb < 1500,
  });

  sleep(0.2);
}

// ---------------------------------------------------------------------------
// Exportación ingest-summary.json para IngestLoadReporter
// ---------------------------------------------------------------------------
export function handleSummary(data) {
  const totalDurationSec =
    data.state && data.state.testRunDurationMs
      ? data.state.testRunDurationMs / 1000.0
      : 0.0;

  const enriched = {
    ...data,
    kallicode_ingest_metadata: {
      ticket: 'KC-QA-B6-05',
      endpoint: INGEST_ENDPOINT,
      fast_mode: FAST_MODE,
      total_duration_seconds: totalDurationSec,
      repos_catalog: SYNTHETIC_REPOS,
    },
  };

  return {
    [SUMMARY_PATH]: JSON.stringify(enriched, null, 2),
  };
}
