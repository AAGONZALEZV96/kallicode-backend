/** Consumo y plan (§15): cuota LOC del ciclo, análisis por ticket y upgrade. */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Paginado, UsageSummary } from "../api/tipos";
import { CajaError, Cargando, ChipEtapa, ChipTipo, Kpi } from "../componentes/ui";
import { useAuth } from "../estado/auth";
import { useI18n } from "../estado/i18n";

interface FilaTicket {
  ticket_id: string;
  titulo: string;
  tipo: string;
  loc_netas: number;
  tokens: number;
  etapa: string;
}

export default function Consumo() {
  const { t } = useI18n();
  const { usuario } = useAuth();
  const nav = useNavigate();
  const [resumen, setResumen] = useState<UsageSummary | null>(null);
  const [tickets, setTickets] = useState<Paginado<FilaTicket> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [msj, setMsj] = useState("");

  useEffect(() => {
    api.get<UsageSummary>("/api/v1/usage/summary").then(setResumen).catch(setError);
    api.get<Paginado<FilaTicket>>("/api/v1/usage/by-ticket").then(setTickets).catch(setError);
  }, []);

  async function upgrade() {
    try {
      const r = await api.post<{ mensaje: string }>("/api/v1/plans/upgrade-request", {
        plan_deseado: "enterprise",
      });
      setMsj(r.mensaje);
    } catch (e) {
      setError(e);
    }
  }

  if (error)
    return (
      <div className="view">
        <CajaError error={error} />
      </div>
    );
  if (!resumen) return <Cargando />;

  const pct = resumen.loc.porcentaje;
  const clase = resumen.umbral === "agotado" ? "err" : resumen.umbral === "aviso_80" ? "warn" : "";

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("cu.title")}</h1>
          <p>{t("cu.sub")}</p>
        </div>
        <div className="spacer" />
        <span className="chip" style={{ alignSelf: "center" }}>
          <span className="dot" style={{ background: "var(--ok)" }} />
          Plan: <b>{resumen.plan?.codigo ?? "—"}</b>
        </span>
        {["owner", "admin"].includes(usuario?.rol ?? "") && (
          <button className="btn primary" onClick={upgrade}>
            {t("cu.upgrade")}
          </button>
        )}
      </div>
      {msj && <div className="aviso-box">{msj}</div>}

      <div className="grid4" style={{ marginBottom: 16 }}>
        <Kpi lbl={t("cu.k1")} num={resumen.loc.consumidas.toLocaleString()} />
        <Kpi lbl={t("cu.k2")} num={resumen.loc.restantes?.toLocaleString() ?? "∞"} color="ok" />
        <Kpi
          lbl="Umbral"
          num={resumen.umbral}
          color={
            resumen.umbral === "agotado" ? "err" : resumen.umbral === "aviso_80" ? "warn" : "ok"
          }
        />
        <Kpi
          lbl={t("cu.k4")}
          num={
            <>
              {resumen.tickets.cerrados}
              <span style={{ fontSize: 13, color: "var(--muted)" }}>
                {" "}
                + {resumen.tickets.en_proceso}
              </span>
            </>
          }
        />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <label>
          Cuota mensual de LOC
          {resumen.plan?.loc_mes && ` · ${resumen.plan.loc_mes.toLocaleString()} LOC/mes`}
        </label>
        <div className="quota-bar" style={{ marginTop: 8 }}>
          <div className={clase} style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 12,
            color: "var(--muted)",
            marginTop: 6,
          }}
        >
          <span>
            {resumen.loc.consumidas.toLocaleString()}
            {resumen.plan?.loc_mes && ` / ${resumen.plan.loc_mes.toLocaleString()}`} LOC
            {" · "}
            {pct}%
          </span>
          {resumen.plan?.renueva_el && <span>Se renueva el {resumen.plan.renueva_el}</span>}
        </div>
      </div>

      {tickets && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "16px 16px 0" }}>
            <label>Consumo por ticket (ciclo {resumen.ciclo})</label>
          </div>
          <table className="kc" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>ID</th>
                <th>{t("tk.col.titulo")}</th>
                <th>{t("tk.col.tipo")}</th>
                <th style={{ textAlign: "right" }}>LOC netas</th>
                <th style={{ textAlign: "right" }}>Tokens</th>
                <th>{t("tk.col.etapa")}</th>
              </tr>
            </thead>
            <tbody>
              {tickets.items.map((tk) => (
                <tr
                  key={tk.ticket_id}
                  className="click"
                  onClick={() => nav(`/tickets/${tk.ticket_id}`)}
                >
                  <td>
                    <b>{tk.ticket_id}</b>
                  </td>
                  <td>{tk.titulo}</td>
                  <td>
                    <ChipTipo tipo={tk.tipo} />
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    {tk.loc_netas || "—"}
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    {tk.tokens ? `${(tk.tokens / 1000).toFixed(1)}k` : "—"}
                  </td>
                  <td>
                    <ChipEtapa etapa={tk.etapa} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
