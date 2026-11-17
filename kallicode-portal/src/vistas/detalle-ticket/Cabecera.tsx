/**
 * Cabecera del detalle: volver al listado, número, título, tipo, prioridad y origen. El
 * origen se muestra con su nombre (KC-PT-06-3); uno que el portal no conozca, tal cual.
 */
import { Link } from "react-router-dom";

import type { TicketDetalle } from "../../api/tipos";
import { ChipTipo, Prio } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";

export default function Cabecera({ ticket }: { ticket: TicketDetalle }) {
  const { t } = useI18n();
  const claveOrigen = `or.${ticket.origen}`;
  const origen = t(claveOrigen) === claveOrigen ? ticket.origen : t(claveOrigen);
  return (
    <div className="view-head">
      <div>
        <p style={{ fontSize: 12, color: "var(--muted)" }}>
          <Link
            to="/tickets"
            style={{ color: "var(--muted)" }}
            data-testid="detalle-ticket.cabecera.volver"
          >
            ← {t("tk.title")}
          </Link>
          {" · "}
          <b>{ticket.id}</b>
        </p>
        <h1>{ticket.titulo}</h1>
        <p>
          <ChipTipo tipo={ticket.tipo} /> <Prio p={ticket.prioridad} />
          {" · "}
          <span data-testid="detalle-ticket.cabecera.origen">{origen}</span>
          {ticket.reportado_por && <> · {ticket.reportado_por.nombre}</>}
        </p>
      </div>
    </div>
  );
}
