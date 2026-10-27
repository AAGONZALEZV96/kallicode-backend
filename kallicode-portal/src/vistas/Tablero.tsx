/**
 * Tablero (§8): KPIs, líneas y kanban, con refresco por polling (D12).
 *
 * Carga inicial y refresco se tratan distinto (KC-PT-01-5, cierra PT-F13):
 * - si falla la carga inicial, se muestra el error con "Reintentar" (antes quedaba
 *   congelado en "Cargando…");
 * - si falla un refresco, se conservan los últimos datos y se avisa con la hora de
 *   la última actualización correcta;
 * - TanStack Query no lanza un refresco mientras el anterior sigue en curso (KC-PT-04-1).
 * El intervalo es el `polling.tablero_s` de GET /organization (KC-PT-04-4, cierra PT-F15).
 */
import { Link, useNavigate } from "react-router-dom";

import { useIntervalosPolling, useTablero } from "../api/consultas";
import { CajaError, Cargando, ChipEtapa, Kpi } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

const COLS = ["triage", "design", "build", "qa", "security", "deploy"];

export default function Tablero() {
  const { t } = useI18n();
  const nav = useNavigate();
  const { tableroMs } = useIntervalosPolling();
  const { data: datos, error, dataUpdatedAt, refetch } = useTablero(tableroMs);

  if (!datos && error)
    return (
      <div className="view" data-testid="tablero.vista.raiz">
        <CajaError error={error} />
        <button
          className="btn"
          onClick={() => void refetch()}
          data-testid="tablero.carga.reintentar"
        >
          {t("err.reintentar")}
        </button>
      </div>
    );
  if (!datos) return <Cargando />;

  const { resumen, lineas, kanban } = datos;
  const errorRefresco = error;
  const actualizadoEn = new Date(dataUpdatedAt);

  return (
    <div className="view" data-testid="tablero.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("dash.title")}</h1>
          <p>{t("dash.sub")}</p>
        </div>
        <div className="spacer" />
        <button
          className="btn primary"
          onClick={() => nav("/nuevo-ticket")}
          data-testid="tablero.cabecera.nuevo-ticket"
        >
          + {t("nav.newticket")}
        </button>
      </div>

      {errorRefresco !== null && (
        <p className="aviso-refresco" role="status" data-testid="tablero.refresco.aviso">
          {t("dash.refresco_fallido")} · {t("dash.ultima_act")}{" "}
          {actualizadoEn.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
        </p>
      )}

      <div className="grid4" style={{ marginBottom: 16 }}>
        <Kpi lbl={t("dash.k1")} num={resumen.en_cola} testid="tablero.kpis.en-cola" />
        <Kpi lbl={t("dash.k2")} num={resumen.en_proceso} testid="tablero.kpis.en-proceso" />
        <Kpi
          lbl={t("dash.k3")}
          num={resumen.esperando_aprobacion}
          color="gate"
          testid="tablero.kpis.esperando-aprobacion"
        />
        <Kpi
          lbl={t("dash.k4")}
          num={resumen.en_produccion_30d}
          color="ok"
          testid="tablero.kpis.en-produccion"
        />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <label>{t("dash.lines")}</label>
        <table className="kc" style={{ marginTop: 6 }} data-testid="tablero.lineas.tabla">
          <tbody>
            {lineas.lineas.map((l) => (
              <tr
                key={l.numero}
                className={l.ticket_id ? "click" : ""}
                onClick={() => l.ticket_id && nav(`/tickets/${l.ticket_id}`)}
                data-testid="tablero.lineas.fila"
              >
                <td>
                  <b>{t("dash.line")}</b> {l.numero}
                </td>
                <td>{l.ticket_id ?? "—"}</td>
                <td>
                  {l.etapa ? (
                    <ChipEtapa etapa={l.etapa} />
                  ) : (
                    <span className="chip">
                      <span className="dot" />
                      {t("dash.idle")}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <label>{t("dash.kanban")}</label>
        <div className="kanban" style={{ marginTop: 10 }}>
          {/* Una columna por etapa: el elemento lleva la etapa (tablero.kanban.col-build). */}
          {COLS.map((col) => (
            <div className="kcol" key={col} data-testid={`tablero.kanban.col-${col}`}>
              <h4>
                <span>{t(`st.${col}`)}</span>
                <span>{kanban.columnas[col]?.length ?? 0}</span>
              </h4>
              {(kanban.columnas[col] ?? []).map((tk) => (
                <Link
                  key={tk.id}
                  to={`/tickets/${tk.id}`}
                  className={`kcard ${tk.gate_pendiente ? "gatewait" : ""}`}
                  data-testid="tablero.kanban.tarjeta"
                >
                  <span className="kid">{tk.id}</span>
                  <br />
                  {tk.titulo}
                  {tk.gate_pendiente && (
                    <>
                      <br />
                      <span className="gtag">⛨ Gate {tk.gate_pendiente}</span>
                    </>
                  )}
                </Link>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
