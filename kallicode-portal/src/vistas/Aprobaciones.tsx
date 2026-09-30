/** Aprobaciones pendientes (§10.1): los gates esperando decisión del rol. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Aprobaciones as Datos } from "../api/tipos";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

export default function Aprobaciones() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [datos, setDatos] = useState<Datos | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.get<Datos>("/api/v1/approvals/pending").then(setDatos).catch(setError);
  }, []);

  if (error)
    return (
      <div className="view">
        <CajaError error={error} />
      </div>
    );
  if (!datos) return <Cargando />;

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("ap.title")}</h1>
          <p>{t("ap.sub")}</p>
        </div>
      </div>
      <div className="grid3">
        {datos.items.map((a) => (
          <div className="card" key={a.ticket_id} style={{ borderTop: "3px solid var(--gate)" }}>
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
            >
              {t("ap.revisar")}
            </button>
          </div>
        ))}
        {datos.items.length === 0 && <p style={{ color: "var(--muted)" }}>{t("comun.vacio")}</p>}
      </div>
    </div>
  );
}
