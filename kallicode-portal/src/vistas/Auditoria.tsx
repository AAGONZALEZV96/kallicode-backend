/** Explorador de auditoría (§11.1) con filtros; clic abre el expediente. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

interface Evento {
  id: number;
  fecha: string;
  ticket_id: string | null;
  etapa: string | null;
  actor_tipo: string;
  actor_id: string | null;
  evento: string;
  modelo: string | null;
  sello: string;
}
interface Datos {
  items: Evento[];
  total: number;
  cadena: { eventos_hoy: number };
}

export default function Auditoria() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [datos, setDatos] = useState<Datos | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "50" });
    if (actor) p.set("actor_tipo", actor);
    if (q) p.set("q", q);
    api.get<Datos>(`/api/v1/audit/events?${p}`).then(setDatos).catch(setError);
  }, [actor, q, page]);

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("au.title")}</h1>
          <p>{t("au.sub")}</p>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 14, display: "flex", gap: 10 }}>
        <input
          type="search"
          placeholder={t("tk.buscar")}
          style={{ maxWidth: 240 }}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <select
          style={{ width: "auto" }}
          value={actor}
          onChange={(e) => {
            setActor(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("au.col.actor")}: *</option>
          <option value="humano">👤 Humano</option>
          <option value="agente">⚙ Agente IA</option>
          <option value="sistema">Sistema</option>
        </select>
      </div>
      <CajaError error={error} />
      {!datos ? (
        <Cargando />
      ) : (
        <>
          <div className="card" style={{ padding: 0 }}>
            <table className="kc">
              <thead>
                <tr>
                  <th>{t("au.col.fecha")}</th>
                  <th>Ticket</th>
                  <th>{t("au.col.actor")}</th>
                  <th>{t("au.col.evento")}</th>
                  <th>{t("au.col.modelo")}</th>
                  <th>{t("au.col.sello")}</th>
                </tr>
              </thead>
              <tbody>
                {datos.items.map((e) => (
                  <tr
                    key={e.id}
                    className={e.ticket_id ? "click" : ""}
                    onClick={() => e.ticket_id && nav(`/auditoria/${e.ticket_id}`)}
                  >
                    <td>{fechaCorta(e.fecha)}</td>
                    <td>
                      <b>{e.ticket_id ?? "—"}</b>
                    </td>
                    <td>
                      {e.actor_tipo === "humano" ? "👤" : "⚙"} {e.actor_id ?? "sistema"}
                    </td>
                    <td>{e.evento}</td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {e.modelo ?? "—"}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {e.sello.slice(0, 4)}…{e.sello.slice(-4)} ✓
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
            <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              ←
            </button>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{page}</span>
            <button
              className="btn sm"
              disabled={page * 50 >= datos.total}
              onClick={() => setPage(page + 1)}
            >
              →
            </button>
            <span className="note" style={{ marginLeft: "auto" }}>
              {datos.cadena.eventos_hoy} eventos hoy
            </span>
          </div>
        </>
      )}
    </div>
  );
}
