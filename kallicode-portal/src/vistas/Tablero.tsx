/**
 * Tablero (§8): KPIs, líneas y kanban, con refresco por polling (D12).
 *
 * Carga inicial y refresco se tratan distinto (KC-PT-01-5, cierra PT-F13):
 * - si falla la carga inicial, se muestra el error con "Reintentar" (antes quedaba
 *   congelado en "Cargando…");
 * - si falla un refresco, se conservan los últimos datos y se avisa con la hora de
 *   la última actualización correcta;
 * - cada refresco se programa cuando termina el anterior, así nunca se superponen.
 * El intervalo pasa a leerse de GET /organization en KC-PT-04-4.
 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Lineas } from "../api/tipos";
import { CajaError, Cargando, ChipEtapa, Kpi } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

export const REFRESCO_TABLERO_MS = 60_000;

interface Resumen {
  en_cola: number;
  en_proceso: number;
  esperando_aprobacion: number;
  en_produccion_30d: number;
}
interface Kanban {
  columnas: Record<
    string,
    { id: string; titulo: string; tipo: string; prioridad: string; gate_pendiente: number | null }[]
  >;
}

const COLS = ["triage", "design", "build", "qa", "security", "deploy"];

interface DatosTablero {
  resumen: Resumen;
  lineas: Lineas;
  kanban: Kanban;
}

export default function Tablero() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [datos, setDatos] = useState<DatosTablero | null>(null);
  const [errorInicial, setErrorInicial] = useState<unknown>(null);
  const [errorRefresco, setErrorRefresco] = useState<unknown>(null);
  const [actualizadoEn, setActualizadoEn] = useState<Date | null>(null);
  // Cambia al pulsar "Reintentar" tras un fallo de la carga inicial: relanza el efecto.
  const [intento, setIntento] = useState(0);

  useEffect(() => {
    let vivo = true;
    let hayDatos = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function cargar() {
      try {
        const [resumen, lineas, kanban] = await Promise.all([
          api.get<Resumen>("/api/v1/dashboard/summary"),
          api.get<Lineas>("/api/v1/dashboard/lines"),
          api.get<Kanban>("/api/v1/dashboard/kanban"),
        ]);
        if (!vivo) return;
        hayDatos = true;
        setDatos({ resumen, lineas, kanban });
        setErrorRefresco(null);
        setActualizadoEn(new Date());
      } catch (e) {
        if (!vivo) return;
        if (!hayDatos) {
          // Sin datos que mostrar: se detiene el polling hasta que el usuario reintente.
          setErrorInicial(e);
          return;
        }
        setErrorRefresco(e);
      }
      timer = setTimeout(cargar, REFRESCO_TABLERO_MS);
    }

    cargar();
    return () => {
      vivo = false;
      clearTimeout(timer);
    };
  }, [intento]);

  function reintentar() {
    setErrorInicial(null);
    setIntento((i) => i + 1);
  }

  if (errorInicial)
    return (
      <div className="view">
        <CajaError error={errorInicial} />
        <button className="btn" onClick={reintentar} data-testid="tablero.carga.reintentar">
          {t("err.reintentar")}
        </button>
      </div>
    );
  if (!datos) return <Cargando />;

  const { resumen, lineas, kanban } = datos;

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("dash.title")}</h1>
          <p>{t("dash.sub")}</p>
        </div>
        <div className="spacer" />
        <button className="btn primary" onClick={() => nav("/nuevo-ticket")}>
          + {t("nav.newticket")}
        </button>
      </div>

      {errorRefresco !== null && actualizadoEn && (
        <p className="aviso-refresco" role="status" data-testid="tablero.refresco.aviso">
          {t("dash.refresco_fallido")} · {t("dash.ultima_act")}{" "}
          {actualizadoEn.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
        </p>
      )}

      <div className="grid4" style={{ marginBottom: 16 }}>
        <Kpi lbl={t("dash.k1")} num={resumen.en_cola} />
        <Kpi lbl={t("dash.k2")} num={resumen.en_proceso} />
        <Kpi lbl={t("dash.k3")} num={resumen.esperando_aprobacion} color="gate" />
        <Kpi lbl={t("dash.k4")} num={resumen.en_produccion_30d} color="ok" />
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <label>{t("dash.lines")}</label>
        <table className="kc" style={{ marginTop: 6 }}>
          <tbody>
            {lineas.lineas.map((l) => (
              <tr
                key={l.numero}
                className={l.ticket_id ? "click" : ""}
                onClick={() => l.ticket_id && nav(`/tickets/${l.ticket_id}`)}
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
          {COLS.map((col) => (
            <div className="kcol" key={col}>
              <h4>
                <span>{t(`st.${col}`)}</span>
                <span>{kanban.columnas[col]?.length ?? 0}</span>
              </h4>
              {(kanban.columnas[col] ?? []).map((tk) => (
                <Link
                  key={tk.id}
                  to={`/tickets/${tk.id}`}
                  className={`kcard ${tk.gate_pendiente ? "gatewait" : ""}`}
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
