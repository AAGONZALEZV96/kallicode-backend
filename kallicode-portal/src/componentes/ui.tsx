/** Componentes base reutilizables: chips de etapa, prioridad, KPI, errores. */
import type { ReactNode } from "react";

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
  color?: string;
  /** data-testid completo (vista.zona.elemento). */
  testid?: string;
}) {
  return (
    <div className="card kpi" data-testid={testid}>
      <div className="lbl">{lbl}</div>
      <div className="num" style={color ? { color: `var(--${color})` } : undefined}>
        {num}
      </div>
    </div>
  );
}

/** Muestra el sobre de error del backend (codigo + mensaje + trace_id). */
/**
 * Mensaje en el idioma del usuario para los errores que el portal sabe explicar: los de
 * red (los genera el cliente) y las denegaciones de permiso o de plan (KC-PT-03-6). El
 * resto conserva el mensaje del servidor, que suele traer detalle propio.
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
      <div className="error-box" data-testid="comun.error.caja">
        <b>{error.codigo}</b> · {mensaje}
        {error.traceId && (
          <span className="mono" style={{ opacity: 0.7 }}>
            {" "}
            · {error.traceId}
          </span>
        )}
      </div>
    );
  }
  return (
    <div className="error-box" data-testid="comun.error.caja">
      {String(error)}
    </div>
  );
}

export function Cargando() {
  const { t } = useI18n();
  return (
    <p style={{ color: "var(--muted)", padding: 20 }} data-testid="comun.carga.indicador">
      {t("comun.cargando")}
    </p>
  );
}

export function fechaCorta(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
