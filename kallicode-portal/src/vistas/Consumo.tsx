/** Consumo y plan (§15): cuota LOC del ciclo, análisis por ticket y upgrade. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useConsumoPorTicket, useResumenConsumo, useSolicitarUpgrade } from "../api/consultas";
import Paginacion from "../componentes/Paginacion";
import { CajaError, Cargando, ChipEtapa, ChipTipo, Kpi } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";
import { usePaginaUrl } from "../usePaginaUrl";

export default function Consumo() {
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const nav = useNavigate();
  const consultaResumen = useResumenConsumo();
  const [pagina, irAPagina] = usePaginaUrl();
  const consultaTickets = useConsumoPorTicket(pagina);
  const solicitarUpgrade = useSolicitarUpgrade();
  const [msj, setMsj] = useState("");
  const resumen = consultaResumen.data;
  const tickets = consultaTickets.data;
  const error = consultaResumen.error ?? consultaTickets.error ?? solicitarUpgrade.error;

  function upgrade() {
    solicitarUpgrade.mutate("enterprise", { onSuccess: (r) => setMsj(r.mensaje) });
  }

  if (error)
    return (
      <div className="view" data-testid="consumo.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  if (!resumen) return <Cargando />;

  const pct = resumen.loc.porcentaje;
  const clase = resumen.umbral === "agotado" ? "err" : resumen.umbral === "aviso_80" ? "warn" : "";

  return (
    <div className="view" data-testid="consumo.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("cu.title")}</h1>
          <p>{t("cu.sub")}</p>
        </div>
        <div className="spacer" />
        <span className="chip" style={{ alignSelf: "center" }} data-testid="consumo.cabecera.plan">
          <span className="dot" style={{ background: "var(--ok)" }} />
          Plan: <b>{resumen.plan?.codigo ?? "—"}</b>
        </span>
        {puedeHacer({ tipo: "solicitar-upgrade" }) && (
          <button className="btn primary" onClick={upgrade} data-testid="consumo.cabecera.upgrade">
            {t("cu.upgrade")}
          </button>
        )}
      </div>
      {msj && (
        <div className="aviso-box" data-testid="consumo.avisos.mensaje">
          {msj}
        </div>
      )}

      <div className="grid4" style={{ marginBottom: 16 }}>
        <Kpi
          lbl={t("cu.k1")}
          num={resumen.loc.consumidas.toLocaleString()}
          testid="consumo.kpis.consumidas"
        />
        <Kpi
          lbl={t("cu.k2")}
          num={resumen.loc.restantes?.toLocaleString() ?? "∞"}
          color="ok"
          testid="consumo.kpis.restantes"
        />
        <Kpi
          lbl={t("cu.umbral")}
          num={t(`cu.umbral.${resumen.umbral}`)}
          color={
            resumen.umbral === "agotado" ? "err" : resumen.umbral === "aviso_80" ? "warn" : "ok"
          }
          testid="consumo.kpis.umbral"
        />
        <Kpi
          testid="consumo.kpis.tickets"
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
        <div className="quota-bar" style={{ marginTop: 8 }} data-testid="consumo.cuota.barra">
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
          <table className="kc" style={{ marginTop: 8 }} data-testid="consumo.tickets.tabla">
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
                  data-testid="consumo.tickets.fila"
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
          <Paginacion
            pagina={pagina}
            tamano={tickets.page_size}
            total={tickets.total}
            enPagina={tickets.items.length}
            alCambiar={irAPagina}
            vista="consumo"
          />
        </div>
      )}
    </div>
  );
}
