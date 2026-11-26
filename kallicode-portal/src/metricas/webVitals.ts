/**
 * Web Vitals del portal (KC-PT-11-2, PT-F20): LCP, INP, CLS, FCP y TTFB de cada visita, con la
 * biblioteca web-vitals. main.tsx carga este módulo después de pintar: no suma al paquete inicial.
 *
 * Destino: VITE_TELEMETRIA_URL (navigator.sendBeacon, una métrica en JSON por envío). El backend
 * todavía no tiene dónde recibirlas; sin destino configurado, en desarrollo se escriben en la
 * consola. En cualquier caso quedan en window.__kcWebVitals para revisarlas a mano y en pruebas.
 * Solo se envían el nombre, el valor, la calificación y la ruta sin identificadores.
 */
import { onCLS, onFCP, onINP, onLCP, onTTFB, type Metric } from "web-vitals";

export interface MetricaVital {
  nombre: Metric["name"];
  /** Milisegundos, salvo CLS (sin unidad). */
  valor: number;
  calificacion: Metric["rating"];
  /** Identificador de la métrica en esta visita (las que cambian se reportan de nuevo con el mismo id). */
  id: string;
  navegacion: Metric["navigationType"];
  /** Ruta por la que se entró, sin identificadores: /tickets/:numero. */
  ruta: string;
}

declare global {
  interface Window {
    __kcWebVitals?: MetricaVital[];
  }
}

/** La ruta sin datos del cliente: /tickets/KC-1003 → /tickets/:numero. */
export function rutaGenerica(pathname: string): string {
  return pathname.replace(/\/KC-\d+(?=\/|$)/gi, "/:numero");
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

type Envio = (url: string, cuerpo: string) => void;

const enviarBeacon: Envio = (url, cuerpo) => {
  // sendBeacon sobrevive al cierre de la pestaña, que es cuando web-vitals reporta casi todo.
  navigator.sendBeacon?.(url, new Blob([cuerpo], { type: "application/json" }));
};

export function iniciarWebVitals({
  destino = import.meta.env.VITE_TELEMETRIA_URL,
  enviar = enviarBeacon,
}: { destino?: string; enviar?: Envio } = {}) {
  // Las métricas son de la visita: se atribuyen a la ruta por la que se entró.
  const ruta = window.location.pathname;
  const metricas: MetricaVital[] = [];
  window.__kcWebVitals = metricas;
  const reportar = (m: Metric) => {
    const metrica = aMetricaVital(m, ruta);
    metricas.push(metrica);
    if (destino) enviar(destino, JSON.stringify(metrica));
    else if (import.meta.env.DEV) console.info("[web-vitals]", metrica);
  };
  onLCP(reportar);
  onINP(reportar);
  onCLS(reportar);
  onFCP(reportar);
  onTTFB(reportar);
}
