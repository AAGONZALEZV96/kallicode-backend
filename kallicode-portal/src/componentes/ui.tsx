/** Componentes base reutilizables: chips de etapa, prioridad, KPI, errores. */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { useI18n } from "../estado/i18n";

export function ChipEtapa({ etapa }: { etapa: string }) {
  const { t } = useI18n();
  const clase = ["triage", "design", "build", "qa", "security", "deploy", "produccion"].includes(
    etapa,
  )
    ? `s-${etapa}`
    : "";
  return (
    <span className={`chip ${clase}`}>
      <span className="dot" /> {t(`st.${etapa}`)}
    </span>
  );
}

export function ChipGate({ gate }: { gate: number | null }) {
  if (!gate) return <>—</>;
  return <span className="chip gate">⛨ Gate {gate}</span>;
}

export function ChipTipo({ tipo }: { tipo: string }) {
  const { t } = useI18n();
  return <span className="chip">{t(`ty.${tipo}`)}</span>;
}

export function Prio({ p }: { p: string }) {
  const { t } = useI18n();
  return <span className={`prio ${p}`}>{t(`pr.${p}`)}</span>;
}

export function Kpi({
  lbl,
  num,
  color,
  testid,
}: {
  lbl: string;
  num: ReactNode;
  /** Color del número: un token del tema (KC-PT-11-4: clase texto-<color>). */
  color?: "ok" | "warn" | "err" | "gate";
  /** data-testid completo (vista.zona.elemento). */
  testid?: string;
}) {
  return (
    <div className="card kpi" data-testid={testid}>
      <div className="lbl">{lbl}</div>
      <div className={color ? `num texto-${color}` : "num"}>{num}</div>
    </div>
  );
}

/** Muestra el sobre de error del backend (codigo + mensaje + trace_id). */
/**
 * Mensaje en el idioma del usuario: los errores de red (los genera el cliente) y cada código
 * de CATALOGO_ERRORES (KC-PT-10-3, textos.test.ts lo exige). Solo conservan el mensaje del
 * servidor los códigos cuyo texto trae un detalle propio que la traducción perdería
 * (CON_DETALLE_DEL_BACKEND en textos.test.ts).
 */
function mensajeTraducido(t: (clave: string) => string, codigo: string): string | null {
  for (const clave of [`red.${codigo}`, `err.${codigo}`]) {
    if (t(clave) !== clave) return t(clave);
  }
  return null;
}

/** Mensaje de un ApiError en el idioma del usuario, sin caja (p. ej. dentro de una lista). */
export function MensajeError({ error }: { error: ApiError }) {
  const { t } = useI18n();
  return (
    <>
      {mensajeTraducido(t, error.codigo) ?? error.message}
      {error.traceId && <span className="mono"> · {error.traceId}</span>}
    </>
  );
}

export function CajaError({ error }: { error: unknown }) {
  const { t } = useI18n();
  if (!error) return null;
  if (error instanceof ApiError) {
    const mensaje = mensajeTraducido(t, error.codigo) ?? error.message;
    return (
      <div className="error-box" role="alert" data-testid="comun.error.caja">
        <b>{error.codigo}</b> · {mensaje}
        {error.traceId && <span className="mono opaco-70"> · {error.traceId}</span>}
      </div>
    );
  }
  return (
    <div className="error-box" role="alert" data-testid="comun.error.caja">
      {String(error)}
    </div>
  );
}

export function Cargando() {
  const { t } = useI18n();
  return (
    <p className="cargando-pagina" role="status" data-testid="comun.carga.indicador">
      {t("comun.cargando")}
    </p>
  );
}

/**
 * Enlace en la celda que identifica una fila clicable (KC-PT-05-4, PT-F12): la fila
 * responde al mouse, pero solo un enlace se alcanza con Tab y se activa con Enter. No
 * propaga el clic para que la fila no navegue otra vez.
 */
export function EnlaceCelda({
  to,
  state,
  children,
}: {
  to: string;
  /** Estado de navegación (p. ej. a dónde volver desde el detalle). */
  state?: unknown;
  children: ReactNode;
}) {
  return (
    <Link to={to} state={state} className="celda-enlace" onClick={(e) => e.stopPropagation()}>
      {children}
    </Link>
  );
}
