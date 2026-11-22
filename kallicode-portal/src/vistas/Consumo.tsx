/** Consumo y plan (§15): cuota LOC del ciclo, análisis por ticket y upgrade. */
import { useId, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useConsumoPorTicket, useResumenConsumo, useSolicitarUpgrade } from "../api/consultas";
import Paginacion from "../componentes/Paginacion";
import { EsqueletoTabla, EsqueletoTarjetas } from "../componentes/Esqueleto";
import { FilaVacia } from "../componentes/EstadoVacio";
import { CajaError, ChipEtapa, ChipTipo, EnlaceCelda, Kpi } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";
import { usePaginaUrl } from "../usePaginaUrl";

export default function Consumo() {
  const { t } = useI18n();
  const fmt = useFormato();
  const { puedeHacer } = usePermiso();
  const nav = useNavigate();
  const consultaResumen = useResumenConsumo();
  const [pagina, irAPagina] = usePaginaUrl();
  const consultaTickets = useConsumoPorTicket(pagina);
  const solicitarUpgrade = useSolicitarUpgrade();
  const [msj, setMsj] = useState("");
  const id = useId();
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
  // Mientras carga: el título y la silueta (sin vista.raiz, que indica que la vista cargó).
  if (!resumen)
    return (
      <div className="view">
        <div className="view-head">
          <div>
            <h1>{t("cu.title")}</h1>
            <p>{t("cu.sub")}</p>
          </div>
        </div>
        <EsqueletoTarjetas tarjetas={4} />
        <EsqueletoTabla columnas={6} filas={4} anunciar={false} />
      </div>
    );

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
        <div className="aviso-box" role="status" data-testid="consumo.avisos.mensaje">
          {msj}
        </div>
      )}

      <div className="grid4" style={{ marginBottom: 16 }}>
        <Kpi
          lbl={t("cu.k1")}
          num={fmt.numero(resumen.loc.consumidas)}
          testid="consumo.kpis.consumidas"
        />
        <Kpi
          lbl={t("cu.k2")}
          num={resumen.loc.restantes == null ? "∞" : fmt.numero(resumen.loc.restantes)}
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
        <p className="caption" id={`${id}-cuota`}>
          Cuota mensual de LOC
          {resumen.plan?.loc_mes && ` · ${fmt.numero(resumen.plan.loc_mes)} LOC/mes`}
        </p>
        <div
          className="quota-bar"
          style={{ marginTop: 8 }}
          role="progressbar"
          aria-labelledby={`${id}-cuota`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.min(pct, 100)}
          data-testid="consumo.cuota.barra"
        >
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
            {fmt.numero(resumen.loc.consumidas)}
            {resumen.plan?.loc_mes && ` / ${fmt.numero(resumen.plan.loc_mes)}`} LOC
            {" · "}
            {fmt.porcentaje(pct)}
          </span>
          {resumen.plan?.renueva_el && (
            <span>Se renueva el {fmt.dia(resumen.plan.renueva_el)}</span>
          )}
        </div>
      </div>

      {!tickets && !consultaTickets.error && <EsqueletoTabla columnas={6} filas={4} />}
      {tickets && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "16px 16px 0" }}>
            <p className="caption" id={`${id}-tickets`}>
              Consumo por ticket (ciclo {resumen.ciclo})
            </p>
          </div>
          <table
            className="kc"
            style={{ marginTop: 8 }}
            aria-labelledby={`${id}-tickets`}
            data-testid="consumo.tickets.tabla"
          >
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
                    <EnlaceCelda to={`/tickets/${tk.ticket_id}`}>{tk.ticket_id}</EnlaceCelda>
                  </td>
                  <td>{tk.titulo}</td>
                  <td>
                    <ChipTipo tipo={tk.tipo} />
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    {tk.loc_netas || "—"}
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>
                    {tk.tokens ? fmt.compacto(tk.tokens) : "—"}
                  </td>
                  <td>
                    <ChipEtapa etapa={tk.etapa} />
                  </td>
                </tr>
              ))}
              {tickets.items.length === 0 && (
                <FilaVacia
                  columnas={6}
                  testid="consumo.tickets.vacio"
                  titulo={t("vacio.consumo")}
                  descripcion={t("vacio.consumo.desc")}
                />
              )}
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
