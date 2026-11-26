/** Consumo y plan (§15): cuota LOC del ciclo, análisis por ticket y upgrade. */
import { useId, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import {
  ESTADOS_SOLICITUD_ABIERTA,
  sanearOrdenConsumo,
  useConsumoPorTicket,
  useResumenConsumo,
  useSolicitudMejora,
  type OrdenConsumo,
} from "../api/consultas";
import type { Umbral } from "../api/tipos";
import Paginacion from "../componentes/Paginacion";
import { EsqueletoTabla, EsqueletoTarjetas } from "../componentes/Esqueleto";
import { FilaVacia } from "../componentes/EstadoVacio";
import { CajaError, ChipEtapa, ChipTipo, EnlaceCelda, Kpi } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";
import { esUnoDe } from "../tipado";
import { usePaginaUrl } from "../usePaginaUrl";
import ComparadorPlanes from "./consumo/ComparadorPlanes";
import { nombrePlan } from "./consumo/planes";

/** Color de la barra de una cuota según su umbral. */
const claseUmbral = (umbral: Umbral) =>
  umbral === "agotado" ? "err" : umbral === "aviso_80" ? "warn" : "";

/** Etapas en las que el ticket terminó: su tiempo e2e ya no corre. */
const TERMINALES = ["produccion", "cancelado", "cerrado_duplicado"];

export default function Consumo() {
  const { t } = useI18n();
  const fmt = useFormato();
  const { puedeHacer, tieneAccesoARuta } = usePermiso();
  const nav = useNavigate();
  const consultaResumen = useResumenConsumo();
  const [pagina, irAPagina] = usePaginaUrl();
  // Orden de la tabla en la URL (?orden=-tiempo_e2e): se comparte y sobrevive a recargar.
  const [params, setParams] = useSearchParams();
  const orden = sanearOrdenConsumo(params.get("orden"));
  const consultaTickets = useConsumoPorTicket(pagina, orden);
  const puedeSolicitar = puedeHacer({ tipo: "solicitar-upgrade" });
  // Solo owner y admin leen la solicitud; si falla, la vista sigue (el comparador no la ofrece).
  const ultimaSolicitud = useSolicitudMejora(puedeSolicitar).data;
  const solicitudAbierta =
    ultimaSolicitud && esUnoDe(ESTADOS_SOLICITUD_ABIERTA, ultimaSolicitud.estado)
      ? ultimaSolicitud
      : null;
  const [comparando, setComparando] = useState(false);
  const [msj, setMsj] = useState("");
  const id = useId();
  const resumen = consultaResumen.data;
  const tickets = consultaTickets.data;
  const error = consultaResumen.error ?? consultaTickets.error;

  /** Primero de mayor a menor (lo que más consume o más tarda); otro clic invierte. */
  function ordenarPor(campo: OrdenConsumo) {
    const siguiente = orden === `-${campo}` ? campo : `-${campo}`;
    setParams(
      (p) => {
        p.set("orden", siguiente);
        p.delete("page");
        return p;
      },
      { replace: true },
    );
  }
  const ordenable = (campo: OrdenConsumo, clave: string, etiqueta: string) => {
    const sentido = orden === campo ? "ascending" : orden === `-${campo}` ? "descending" : "none";
    return (
      <th style={{ textAlign: "right" }} aria-sort={sentido}>
        <button
          type="button"
          className="orden-columna"
          onClick={() => ordenarPor(campo)}
          data-testid={`consumo.tickets.orden-${clave}`}
        >
          {etiqueta}
          <span aria-hidden="true">
            {sentido === "ascending" ? " ▲" : sentido === "descending" ? " ▼" : " ↕"}
          </span>
        </button>
      </th>
    );
  };

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
        <EsqueletoTabla columnas={7} filas={4} anunciar={false} />
      </div>
    );

  const pct = resumen.loc.porcentaje;
  const clase = claseUmbral(resumen.umbral);
  const alm = resumen.almacenamiento;

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
          {t("cu.plan")}: <b>{resumen.plan?.codigo ?? "—"}</b>
        </span>
        {/* Owner y admin piden la mejora desde el comparador; el resto solo compara. */}
        {puedeSolicitar ? (
          <button
            className="btn primary"
            onClick={() => setComparando(true)}
            data-testid="consumo.cabecera.upgrade"
          >
            {t("cu.upgrade")}
          </button>
        ) : (
          <button
            className="btn"
            onClick={() => setComparando(true)}
            data-testid="consumo.cabecera.planes"
          >
            {t("cu.planes")}
          </button>
        )}
      </div>
      {msj && (
        <div className="aviso-box" role="status" data-testid="consumo.avisos.mensaje">
          {msj}
        </div>
      )}
      {solicitudAbierta && (
        <div className="info-box" data-testid="consumo.avisos.solicitud">
          {t("cu.solicitud", {
            plan: nombrePlan(solicitudAbierta.plan_deseado),
            fecha: fmt.fechaLarga(solicitudAbierta.creada_en),
          })}
          {solicitudAbierta.urgente && (
            <span className="chip urgente">{t("cu.solicitud.urgente")}</span>
          )}
        </div>
      )}
      {comparando && (
        <ComparadorPlanes
          puedeSolicitar={puedeSolicitar}
          solicitudAbierta={solicitudAbierta}
          alCerrar={() => setComparando(false)}
          alSolicitar={(mensaje) => {
            setComparando(false);
            setMsj(mensaje);
          }}
        />
      )}

      <div className="grid-kpis" style={{ marginBottom: 16 }}>
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
        {/* Tiempo de fábrica del ciclo: mediana de los que llegaron a producción (KC-PT-09-2). */}
        <Kpi
          testid="consumo.kpis.tiempo-e2e"
          lbl={t("cu.k5")}
          num={
            <span title={fmt.duracion(resumen.tiempo_e2e.mediana_s, "larga")}>
              {fmt.duracion(resumen.tiempo_e2e.mediana_s)}
              <span className="kpi-detalle">
                {t("cu.k5.detalle", { n: fmt.numero(resumen.tiempo_e2e.cerrados) })}
              </span>
            </span>
          }
        />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p className="caption" id={`${id}-cuota`}>
          {t("cu.cuota")}
          {resumen.plan?.loc_mes &&
            ` · ${t("cu.loc_mes", { n: fmt.numero(resumen.plan.loc_mes) })}`}
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
            <span>{t("cu.renueva", { fecha: fmt.dia(resumen.plan.renueva_el) })}</span>
          )}
        </div>
        {/* Tickets que esperan la renovación de la cuota (KC-PT-09-5). */}
        {resumen.cola.tickets > 0 && (
          <p className="nota-cola" data-testid="consumo.cola.resumen">
            {t("cu.cola", {
              n: fmt.numero(resumen.cola.tickets),
              desde: fmt.dia(resumen.cola.desde),
            })}{" "}
            {tieneAccesoARuta("/tickets") && (
              <Link to="/tickets?etapa=en_cola_por_cuota" data-testid="consumo.cola.enlace">
                {t("aviso.ver_cola")}
              </Link>
            )}
          </p>
        )}
      </div>

      {/* Almacenamiento de evidencia, con los umbrales que bloquean la subida (KC-PT-09-5). */}
      <div className="card" style={{ marginBottom: 16 }}>
        <p className="caption" id={`${id}-almacenamiento`}>
          {t("cu.almacenamiento")}
        </p>
        {alm.porcentaje != null && (
          <div
            className="quota-bar"
            style={{ marginTop: 8 }}
            role="progressbar"
            aria-labelledby={`${id}-almacenamiento`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.min(alm.porcentaje, 100)}
            data-testid="consumo.almacenamiento.barra"
          >
            <div
              className={claseUmbral(alm.umbral)}
              style={{ width: `${Math.min(alm.porcentaje, 100)}%` }}
            />
          </div>
        )}
        <p className="pie-cuota" data-testid="consumo.almacenamiento.uso">
          {alm.limite_bytes == null
            ? t("cu.almacenamiento.sin_limite", { usados: fmt.bytes(alm.usados_bytes) })
            : t("cu.almacenamiento.uso", {
                usados: fmt.bytes(alm.usados_bytes),
                limite: fmt.bytes(alm.limite_bytes),
                pct: fmt.porcentaje(alm.porcentaje),
              })}
        </p>
      </div>

      {!tickets && !consultaTickets.error && <EsqueletoTabla columnas={7} filas={4} />}
      {tickets && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "16px 16px 0" }}>
            <p className="caption" id={`${id}-tickets`}>
              {t("cu.por_ticket", { ciclo: resumen.ciclo })}
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
                {ordenable("loc_netas", "loc", t("cu.col.loc"))}
                {ordenable("tokens", "tokens", t("cu.col.tokens"))}
                {ordenable("tiempo_e2e", "tiempo", t("cu.col.tiempo"))}
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
                  <td
                    className="mono"
                    style={{ textAlign: "right" }}
                    title={fmt.duracion(tk.tiempo_e2e_s, "larga")}
                  >
                    {fmt.duracion(tk.tiempo_e2e_s)}
                    {!TERMINALES.includes(tk.etapa) && (
                      <span className="note-inline"> · {t("cu.en_curso")}</span>
                    )}
                  </td>
                  <td>
                    <ChipEtapa etapa={tk.etapa} />
                  </td>
                </tr>
              ))}
              {tickets.items.length === 0 && (
                <FilaVacia
                  columnas={7}
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
