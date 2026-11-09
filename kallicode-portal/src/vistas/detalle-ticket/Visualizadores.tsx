/** Visualizadores del ticket (iniciales con el nombre como nombre accesible). */
import { useId } from "react";

import type { TicketDetalle } from "../../api/tipos";
import { useI18n } from "../../estado/i18n";

const iniciales = (nombre: string) =>
  nombre
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("");

export default function Visualizadores({ ticket }: { ticket: TicketDetalle }) {
  const { t } = useI18n();
  const id = useId();
  return (
    <div className="card">
      <p className="caption" id={id}>
        {t("td.watchers")}
      </p>
      <div
        style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}
        role="list"
        aria-labelledby={id}
        data-testid="detalle-ticket.visualizadores.lista"
      >
        {ticket.visualizadores.map((w) => (
          <div key={w.id} className="avatar" title={w.nombre} role="listitem" aria-label={w.nombre}>
            {iniciales(w.nombre)}
          </div>
        ))}
      </div>
    </div>
  );
}
