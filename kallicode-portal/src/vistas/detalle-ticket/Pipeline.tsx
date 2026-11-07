/** Stepper del pipeline: etapas hechas, la actual y el gate que espera (§9.6). */
import type { TicketDetalle } from "../../api/tipos";
import { useI18n } from "../../estado/i18n";
import { ETAPAS_PIPE, GATE_DE_ETAPA } from "./pestanas";

export default function Pipeline({ ticket }: { ticket: TicketDetalle }) {
  const { t } = useI18n();
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="pipe" role="list" aria-label={t("td.pipeline")}>
        {ETAPAS_PIPE.map((e, i) => {
          const estado = ticket.pipeline.find((p) => p.etapa === e)?.estado ?? "pendiente";
          const gate = GATE_DE_ETAPA[e] ?? null;
          // Una etapa sin gate (build, security…) nunca lo espera: sin el `gate !== null`,
          // null === null dibujaba «⛨ GATE» sin número en la etapa actual tras firmar.
          const esperando = gate !== null && ticket.gate_pendiente === gate && estado === "actual";
          return (
            <div
              key={e}
              role="listitem"
              aria-current={estado === "actual" ? "step" : undefined}
              className={`stg ${estado === "done" ? "done" : ""} ${estado === "actual" ? "now" : ""}`}
              data-testid={`detalle-ticket.pipeline.etapa-${e}`}
            >
              {esperando && <div className="gflag">⛨ GATE {gate}</div>}
              <div className="bubble">{estado === "done" ? "✓" : i + 1}</div>
              <div className="lbl">{t(`st.${e}`)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
