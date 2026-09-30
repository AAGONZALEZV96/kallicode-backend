/** Listado de tickets (§9.1) con filtros y paginación. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Paginado, TicketResumen } from "../api/tipos";
import {
  CajaError,
  Cargando,
  ChipEtapa,
  ChipGate,
  ChipTipo,
  Prio,
  fechaCorta,
} from "../componentes/ui";
import { useI18n } from "../estado/i18n";

const ETAPAS = [
  "triage",
  "design",
  "build",
  "qa",
  "security",
  "deploy",
  "produccion",
  "en_cola_por_cuota",
  "cancelado",
];

export default function Tickets() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [datos, setDatos] = useState<Paginado<TicketResumen> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [q, setQ] = useState("");
  const [etapa, setEtapa] = useState("");
  const [prioridad, setPrioridad] = useState("");
  const [page, setPage] = useState(1);

  useEffect(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "25" });
    if (q) p.set("q", q);
    if (etapa) p.set("etapa", etapa);
    if (prioridad) p.set("prioridad", prioridad);
    api.get<Paginado<TicketResumen>>(`/api/v1/tickets?${p}`).then(setDatos).catch(setError);
  }, [q, etapa, prioridad, page]);

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("tk.title")}</h1>
          <p>{t("tk.sub")}</p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => nav("/nueva-funcionalidad")}>
          ✦ {t("nav.newfeature")}
        </button>
        <button className="btn primary" onClick={() => nav("/nuevo-ticket")}>
          + {t("nav.newticket")}
        </button>
      </div>

      <div className="card" style={{ marginBottom: 14, display: "flex", gap: 10 }}>
        <input
          type="search"
          placeholder={t("tk.buscar")}
          style={{ maxWidth: 280 }}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <select
          style={{ width: "auto" }}
          value={etapa}
          onChange={(e) => {
            setEtapa(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("tk.todas")}</option>
          {ETAPAS.map((e) => (
            <option key={e} value={e}>
              {t(`st.${e}`)}
            </option>
          ))}
        </select>
        <select
          style={{ width: "auto" }}
          value={prioridad}
          onChange={(e) => {
            setPrioridad(e.target.value);
            setPage(1);
          }}
        >
          <option value="">{t("tk.toda_prio")}</option>
          {["alta", "media", "baja"].map((p) => (
            <option key={p} value={p}>
              {t(`pr.${p}`)}
            </option>
          ))}
        </select>
      </div>

      <CajaError error={error} />
      {!datos ? (
        <Cargando />
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table className="kc">
            <thead>
              <tr>
                <th>ID</th>
                <th>{t("tk.col.titulo")}</th>
                <th>{t("tk.col.tipo")}</th>
                <th>{t("tk.col.prio")}</th>
                <th>{t("tk.col.etapa")}</th>
                <th>{t("tk.col.gate")}</th>
                <th>{t("tk.col.upd")}</th>
              </tr>
            </thead>
            <tbody>
              {datos.items.map((tk) => (
                <tr key={tk.id} className="click" onClick={() => nav(`/tickets/${tk.id}`)}>
                  <td>
                    <b>{tk.id}</b>
                  </td>
                  <td>{tk.titulo}</td>
                  <td>
                    <ChipTipo tipo={tk.tipo} />
                  </td>
                  <td>
                    <Prio p={tk.prioridad} />
                  </td>
                  <td>
                    <ChipEtapa etapa={tk.etapa} />
                  </td>
                  <td>
                    <ChipGate gate={tk.gate_pendiente} />
                  </td>
                  <td>{fechaCorta(tk.actualizado_en)}</td>
                </tr>
              ))}
              {datos.items.length === 0 && (
                <tr>
                  <td colSpan={7} style={{ color: "var(--muted)" }}>
                    {t("comun.vacio")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <div style={{ display: "flex", gap: 8, padding: 12, alignItems: "center" }}>
            <button className="btn sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              ←
            </button>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {page} / {Math.max(1, Math.ceil(datos.total / datos.page_size))}
            </span>
            <button
              className="btn sm"
              disabled={page * datos.page_size >= datos.total}
              onClick={() => setPage(page + 1)}
            >
              →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
