/** Resumen del ticket: etapa, línea de producción y enlace al PR. */
import { useId } from "react";

import type { TicketDetalle } from "../../api/tipos";
import { ChipEtapa } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import { sanearUrl } from "../../urls";

export default function Resumen({ ticket }: { ticket: TicketDetalle }) {
  const { t } = useI18n();
  const id = useId();
  // pr_url viene del repositorio del cliente: solo se enlaza si es http(s) (PT-F22).
  const prUrl = sanearUrl(ticket.detalle.pr_url);
  return (
    <div className="card">
      <p className="caption" id={id}>
        {t("td.detalle")}
      </p>
      <table className="kc" style={{ fontSize: 12.5, marginTop: 4 }} aria-labelledby={id}>
        <tbody>
          <tr>
            <td style={{ color: "var(--muted)" }}>{t("tk.col.etapa")}</td>
            <td>
              <ChipEtapa etapa={ticket.etapa} />
            </td>
          </tr>
          <tr>
            <td style={{ color: "var(--muted)" }}>{t("td.linea")}</td>
            <td>{ticket.detalle.linea ?? "—"}</td>
          </tr>
          <tr>
            <td style={{ color: "var(--muted)" }}>PR</td>
            <td>
              {prUrl ? (
                <a
                  href={prUrl}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={t("td.pr.abrir")}
                  data-testid="detalle-ticket.resumen.pr"
                >
                  ↗
                </a>
              ) : (
                "—"
              )}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
