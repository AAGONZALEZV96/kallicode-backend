/**
 * Web Vitals del portal (KC-PT-11-2, PT-F20): LCP, INP, CLS, FCP y TTFB de cada visita, con la
 * biblioteca web-vitals. main.tsx carga este módulo después de pintar: no suma al paquete inicial.
 *
 * Van con la telemetría del portal a POST /api/v1/telemetry/client (KC-PT-11-5, api/telemetria):
 * nombre, valor, calificación y la ruta sin identificadores. También quedan en
 * window.__kcWebVitals para revisarlas a mano y en pruebas.
 */
import { onCLS, onFCP, onINP, onLCP, onTTFB, type Metric } from "web-vitals";

import { registrarVital, rutaGenerica } from "../api/telemetria";

export interface MetricaVital {
  nombre: Metric["name"];
  /** Milisegundos, salvo CLS (sin unidad). */
  valor: number;
  calificacion: Metric["rating"];
  /** Identificador de la métrica en esta visita (las que cambian se reportan de nuevo con el mismo id). */
  id: string;
  navegacion: Metric["navigationType"];
  /** Ruta de la página cargada, sin identificadores: /tickets/:numero. */
  ruta: string;
}

declare global {
  interface Window {
    __kcWebVitals?: MetricaVital[];
  }
}

export function aMetricaVital(m: Metric, ruta: string): MetricaVital {
  return {
    nombre: m.name,
    valor: m.name === "CLS" ? Number(m.value.toFixed(4)) : Math.round(m.value),
    calificacion: m.rating,
    id: m.id,
    navegacion: m.navigationType,
    ruta: rutaGenerica(ruta),
  };
}

export function iniciarWebVitals({
  registrar = registrarVital,
}: { registrar?: typeof registrarVital } = {}) {
  // Las métricas son de la visita: se atribuyen a la página que se cargó.
  const ruta = window.location.pathname;
  const metricas: MetricaVital[] = [];
  window.__kcWebVitals = metricas;
  const reportar = (m: Metric) => {
    const metrica = aMetricaVital(m, ruta);
    metricas.push(metrica);
    registrar({
      nombre: metrica.nombre,
      valor: metrica.valor,
      calificacion: metrica.calificacion,
      ruta: metrica.ruta,
    });
  };
  onLCP(reportar);
  onINP(reportar);
  onCLS(reportar);
  onFCP(reportar);
  onTTFB(reportar);
}
