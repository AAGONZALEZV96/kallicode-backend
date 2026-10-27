/** Aprobaciones pendientes (§10.1): los gates esperando decisión del rol. */
import { useNavigate } from "react-router-dom";

import { useAprobaciones } from "../api/consultas";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

export default function Aprobaciones() {
  const { t } = useI18n();
  const nav = useNavigate();
  // Misma consulta que el badge del Layout: abrir la vista no repite la petición.
  const { data: datos, error } = useAprobaciones();

  if (error)
    return (
      <div className="view" data-testid="aprobaciones.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  if (!datos) return <Cargando />;

  return (
    <div className="view" data-testid="aprobaciones.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("ap.title")}</h1>
          <p>{t("ap.sub")}</p>
        </div>
      </div>
      <div className="grid3">
        {datos.items.map((a) => (
          <div
            className="card"
            key={a.ticket_id}
            style={{ borderTop: "3px solid var(--gate)" }}
            data-testid="aprobaciones.lista.tarjeta"
          >
            <span className="chip gate">⛨ Gate {a.gate}</span>
            <h3 style={{ margin: "8px 0 2px", fontSize: 15 }}>{a.titulo}</h3>
            <p style={{ fontSize: 12, color: "var(--muted)" }}>
              {a.ticket_id} · {a.que_se_aprueba}
            </p>
            <p className="note" style={{ margin: "10px 0" }}>
              {fechaCorta(a.esperando_desde)}
            </p>
            <button
              className="btn primary"
              style={{ width: "100%" }}
              onClick={() => nav(`/tickets/${a.ticket_id}`)}
              data-testid="aprobaciones.lista.revisar"
            >
              {t("ap.revisar")}
            </button>
          </div>
        ))}
        {datos.items.length === 0 && (
          <p style={{ color: "var(--muted)" }} data-testid="aprobaciones.lista.vacio">
            {t("comun.vacio")}
          </p>
        )}
      </div>
    </div>
  );
}
