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

export function Kpi({ lbl, num, color }: { lbl: string; num: ReactNode; color?: string }) {
  return (
    <div className="card kpi">
      <div className="lbl">{lbl}</div>
      <div className="num" style={color ? { color: `var(--${color})` } : undefined}>
        {num}
      </div>
    </div>
  );
}

/** Muestra el sobre de error del backend (codigo + mensaje + trace_id). */
export function CajaError({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ApiError) {
    return (
      <div className="error-box">
        <b>{error.codigo}</b> · {error.message}
        {error.traceId && (
          <span className="mono" style={{ opacity: 0.7 }}>
            {" "}
            · {error.traceId}
          </span>
        )}
      </div>
    );
  }
  return <div className="error-box">{String(error)}</div>;
}

export function Cargando() {
  const { t } = useI18n();
  return <p style={{ color: "var(--muted)", padding: 20 }}>{t("comun.cargando")}</p>;
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
