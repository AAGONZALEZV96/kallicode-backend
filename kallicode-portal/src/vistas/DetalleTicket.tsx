/** Detalle de ticket (§9.6): stepper del pipeline, 5 pestañas y panel de gate.
 * Las pestañas cargan bajo demanda; el panel de gate firma o devuelve (§10). */
import { useCallback, useEffect, useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type {
  Actividad,
  ConsumoTicket,
  Evidencia,
  Hallazgos,
  Spec,
  TicketDetalle,
} from "../api/tipos";
import { CajaError, Cargando, ChipEtapa, ChipTipo, Prio, fechaCorta } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";
import { sanearUrl } from "../urls";

const ETAPAS_PIPE = ["triage", "design", "build", "qa", "security", "deploy", "produccion"];
type Pestana = "spec" | "evidence" | "security" | "usage" | "activity";

export default function DetalleTicket() {
  const { numero = "" } = useParams();
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const nav = useNavigate();
  const [tk, setTk] = useState<TicketDetalle | null>(null);
  const [pestana, setPestana] = useState<Pestana>("spec");
  const [error, setError] = useState<unknown>(null);

  const cargar = useCallback(() => {
    api.get<TicketDetalle>(`/api/v1/tickets/${numero}`).then(setTk).catch(setError);
  }, [numero]);
  useEffect(cargar, [cargar]);

  if (error)
    return (
      <div className="view">
        <CajaError error={error} />
      </div>
    );
  if (!tk) return <Cargando />;

  // pr_url viene del repositorio del cliente: solo se enlaza si es http(s) (PT-F22).
  const prUrl = sanearUrl(tk.detalle.pr_url);
  const puedeFirmar =
    tk.gate_pendiente != null && puedeHacer({ tipo: "firmar-gate", gate: tk.gate_pendiente });
  const puedeCancelar = puedeHacer({
    tipo: "cancelar-ticket",
    creadorId: tk.reportado_por?.id ?? null,
  });

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            <Link to="/tickets" style={{ color: "var(--muted)" }}>
              ← {t("tk.title")}
            </Link>
            {" · "}
            <b>{tk.id}</b>
          </p>
          <h1>{tk.titulo}</h1>
          <p>
            <ChipTipo tipo={tk.tipo} /> <Prio p={tk.prioridad} />
            {" · "}
            {tk.origen}
            {tk.reportado_por && <> · {tk.reportado_por.nombre}</>}
          </p>
        </div>
      </div>

      {/* stepper */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="pipe">
          {ETAPAS_PIPE.map((e, i) => {
            const info = tk.pipeline.find((p) => p.etapa === e);
            const estado = info?.estado ?? "pendiente";
            const gate = { design: 1, qa: 2, deploy: 3 }[e as "design"] ?? null;
            const esperando = tk.gate_pendiente === gate && estado === "actual";
            return (
              <div
                key={e}
                className={`stg ${estado === "done" ? "done" : ""} ${estado === "actual" ? "now" : ""}`}
              >
                {esperando && <div className="gflag">⛨ GATE {gate}</div>}
                <div className="bubble">{estado === "done" ? "✓" : i + 1}</div>
                <div className="lbl">{t(`st.${e}`)}</div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="grid2" style={{ gridTemplateColumns: "1.6fr 1fr", alignItems: "start" }}>
        <div className="card">
          <div className="tabs">
            {(["spec", "evidence", "security", "usage", "activity"] as Pestana[]).map((p) => (
              <button
                key={p}
                className={pestana === p ? "active" : ""}
                onClick={() => setPestana(p)}
              >
                {t(`td.tab.${p}`)}
              </button>
            ))}
          </div>
          {pestana === "spec" && <TabSpec numero={numero} />}
          {pestana === "evidence" && <TabEvidencia numero={numero} />}
          {pestana === "security" && <TabSeguridad numero={numero} />}
          {pestana === "usage" && <TabConsumo numero={numero} />}
          {pestana === "activity" && <TabActividad numero={numero} />}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {puedeFirmar && tk.gate_pendiente && (
            <PanelGate numero={numero} gate={tk.gate_pendiente} alDecidir={cargar} />
          )}
          <div className="card">
            <label>{t("td.detalle")}</label>
            <table className="kc" style={{ fontSize: 12.5, marginTop: 4 }}>
              <tbody>
                <tr>
                  <td style={{ color: "var(--muted)" }}>{t("tk.col.etapa")}</td>
                  <td>
                    <ChipEtapa etapa={tk.etapa} />
                  </td>
                </tr>
                <tr>
                  <td style={{ color: "var(--muted)" }}>{t("td.linea")}</td>
                  <td>{tk.detalle.linea ?? "—"}</td>
                </tr>
                <tr>
                  <td style={{ color: "var(--muted)" }}>PR</td>
                  <td>
                    {prUrl ? (
                      <a href={prUrl} target="_blank" rel="noreferrer">
                        ↗
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="card">
            <label>{t("td.watchers")}</label>
            <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
              {tk.visualizadores.map((w) => (
                <div key={w.id} className="avatar" title={w.nombre}>
                  {w.nombre
                    .split(" ")
                    .map((p) => p[0])
                    .slice(0, 2)
                    .join("")}
                </div>
              ))}
            </div>
          </div>
          {puedeCancelar && !["deploy", "produccion", "cancelado"].includes(tk.etapa) && (
            <BotonCancelar numero={numero} alCancelar={() => nav("/tickets")} />
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- pestañas (carga bajo demanda, 404 = aún sin datos) ------ */
function usePestana<T>(ruta: string) {
  const [datos, setDatos] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [cargando, setCargando] = useState(true);
  useEffect(() => {
    api
      .get<T>(ruta)
      .then(setDatos)
      .catch((e) => {
        if (!(e instanceof ApiError && e.http === 404)) setError(e);
      })
      .finally(() => setCargando(false));
  }, [ruta]);
  return { datos, error, cargando };
}

function TabSpec({ numero }: { numero: string }) {
  const { t } = useI18n();
  const { datos, error, cargando } = usePestana<Spec>(`/api/v1/tickets/${numero}/spec`);
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <p className="note">—</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p style={{ fontSize: 12, color: "var(--muted)" }}>
        Spec v{datos.version} · {datos.estado}
      </p>
      <div>
        <b>{t("td.alt")}</b>
        <div className="grid2" style={{ marginTop: 6 }}>
          {datos.alternativas.map((a) => (
            <div
              key={a.id}
              className="card"
              style={
                a.elegida || a.recomendada
                  ? { borderWidth: 2, borderColor: "var(--accent)" }
                  : undefined
              }
            >
              <b>
                {a.id} · {a.titulo}
              </b>
              <p style={{ fontSize: 12, color: "var(--muted)" }}>
                {a.descripcion} {a.recomendada && "✔"}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div>
        <b>{t("td.criterios")}</b>
        <table className="kc" style={{ marginTop: 6 }}>
          <tbody>
            {datos.criterios.map((c) => (
              <tr key={c.id}>
                <td style={{ width: 60 }}>
                  <b>{c.id}</b>
                </td>
                <td>
                  Dado {c.given}, cuando {c.when}, entonces {c.then}.
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <b>{t("td.scope")}</b>
        <p className="note" style={{ marginTop: 6 }}>
          <span className="mono">{datos.scope_paths.join(" · ")}</span>
        </p>
      </div>
    </div>
  );
}

function TabEvidencia({ numero }: { numero: string }) {
  const { datos, error, cargando } = usePestana<Evidencia>(`/api/v1/tickets/${numero}/evidence`);
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <p className="note">—</p>;
  return (
    <>
      <table className="kc">
        <thead>
          <tr>
            <th>ID</th>
            <th>Resultado</th>
            <th>Detalle</th>
          </tr>
        </thead>
        <tbody>
          {datos.matriz.map((m) => (
            <tr key={m.criterio_id}>
              <td>
                <b>{m.criterio_id}</b>
              </td>
              <td>
                <span className={`res ${m.resultado === "pasa" ? "pass" : "fail"}`}>
                  {m.resultado === "pasa"
                    ? "✓ PASA"
                    : m.resultado === "falla"
                      ? "✗ FALLA"
                      : "~ FLAKY"}
                </span>
              </td>
              <td className="mono" style={{ fontSize: 11 }}>
                {m.extracto_fallo ?? ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note" style={{ marginTop: 10 }}>
        Regresión dirigida: {datos.regresion.dirigidos_ok}/{datos.regresion.dirigidos}
        {" · "}suite del cliente: {datos.regresion.cliente_ok}/{datos.regresion.suite_cliente}
      </p>
    </>
  );
}

function TabSeguridad({ numero }: { numero: string }) {
  const { datos, error, cargando } = usePestana<Hallazgos>(`/api/v1/tickets/${numero}/security`);
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <p className="note">—</p>;
  return (
    <>
      <table className="kc">
        <thead>
          <tr>
            <th>Severidad</th>
            <th>Hallazgo</th>
            <th>Estado</th>
          </tr>
        </thead>
        <tbody>
          {datos.hallazgos.map((h) => (
            <tr key={h.id}>
              <td>
                <span className={`prio ${h.severidad === "critica" ? "alta" : h.severidad}`}>
                  {h.severidad}
                </span>
              </td>
              <td>
                {h.titulo}
                {h.justificacion && (
                  <p style={{ fontSize: 11, color: "var(--muted)" }}>{h.justificacion}</p>
                )}
              </td>
              <td>{h.estado === "corregido" ? <span className="res pass">✓</span> : h.estado}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {datos.bloquea_deploy && (
        <div className="error-box">Severidad alta sin resolver: bloquea el Deploy.</div>
      )}
    </>
  );
}

function TabConsumo({ numero }: { numero: string }) {
  const { datos, cargando } = usePestana<ConsumoTicket>(`/api/v1/tickets/${numero}/usage`);
  if (cargando) return <Cargando />;
  if (!datos) return <p className="note">—</p>;
  return (
    <>
      <div className="grid3" style={{ marginBottom: 14 }}>
        <div className="card kpi" style={{ padding: "10px 12px" }}>
          <div className="lbl">Tokens totales</div>
          <div className="num" style={{ fontSize: 20 }}>
            {((datos.totales.tokens_in + datos.totales.tokens_out) / 1000).toFixed(1)}k
          </div>
        </div>
        <div className="card kpi" style={{ padding: "10px 12px" }}>
          <div className="lbl">LOC netas</div>
          <div className="num" style={{ fontSize: 20 }}>
            {datos.totales.loc_netas}
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {" "}
              +{datos.totales.loc_tests} test
            </span>
          </div>
        </div>
        <div className="card kpi" style={{ padding: "10px 12px" }}>
          <div className="lbl">Invocaciones</div>
          <div className="num" style={{ fontSize: 20 }}>
            {datos.totales.invocaciones}
          </div>
        </div>
      </div>
      <table className="kc">
        <thead>
          <tr>
            <th>Función</th>
            <th>Modelo</th>
            <th>Inv.</th>
            <th>Tokens in</th>
            <th>Tokens out</th>
          </tr>
        </thead>
        <tbody className="mono" style={{ fontSize: 12 }}>
          {datos.por_funcion.map((f, i) => (
            <tr key={i}>
              <td>{f.funcion}</td>
              <td>{f.modelo}</td>
              <td>{f.invocaciones}</td>
              <td>{f.tokens_in.toLocaleString()}</td>
              <td>{f.tokens_out.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function TabActividad({ numero }: { numero: string }) {
  const { datos, cargando } = usePestana<Actividad>(
    `/api/v1/tickets/${numero}/activity?page_size=50`,
  );
  if (cargando) return <Cargando />;
  if (!datos) return <p className="note">—</p>;
  return (
    <ul className="audit">
      {datos.items.map((a, i) => (
        <li key={i}>
          <span className="when">{fechaCorta(a.fecha)}</span>
          <span className={`who ${a.actor.tipo === "humano" ? "human" : "ai"}`}>
            {a.actor.nombre}
          </span>
          <span>{a.evento}</span>
        </li>
      ))}
    </ul>
  );
}

/* ---------------- panel de gate ------------------------------------------ */
function PanelGate({
  numero,
  gate,
  alDecidir,
}: {
  numero: string;
  gate: number;
  alDecidir: () => void;
}) {
  const { t } = useI18n();
  const [comentario, setComentario] = useState("");
  const [alternativa, setAlternativa] = useState("");
  const [alts, setAlts] = useState<{ id: string; titulo: string }[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [ocupado, setOcupado] = useState(false);
  const idAlternativa = useId();
  const idComentario = useId();

  useEffect(() => {
    if (gate === 1) {
      api
        .get<Spec>(`/api/v1/tickets/${numero}/spec`)
        .then((s) => {
          setAlts(s.alternativas);
          const rec = s.alternativas.find((a) => a.recomendada);
          if (rec) setAlternativa(rec.id);
        })
        .catch(() => setAlts([]));
    }
  }, [numero, gate]);

  async function decidir(accion: "approve" | "request-changes") {
    setError(null);
    setOcupado(true);
    try {
      const cuerpo =
        accion === "approve"
          ? {
              comentario: comentario || undefined,
              alternativa_id: gate === 1 && alts.length > 1 ? alternativa : undefined,
            }
          : { comentario };
      await api.post(`/api/v1/tickets/${numero}/gates/${gate}/${accion}`, cuerpo);
      alDecidir();
    } catch (e) {
      setError(e);
    } finally {
      setOcupado(false);
    }
  }

  return (
    <div className="gate-panel">
      <h3>{t(`gate.g${gate}`)}</h3>
      {gate === 1 && alts.length > 1 && (
        <div style={{ marginBottom: 8 }}>
          <label htmlFor={idAlternativa}>Alternativa</label>
          <select
            id={idAlternativa}
            value={alternativa}
            onChange={(e) => setAlternativa(e.target.value)}
          >
            {alts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id} · {a.titulo}
              </option>
            ))}
          </select>
        </div>
      )}
      <label htmlFor={idComentario}>{t("gate.comentario")}</label>
      <textarea
        id={idComentario}
        rows={3}
        value={comentario}
        onChange={(e) => setComentario(e.target.value)}
      />
      <CajaError error={error} />
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          className="btn ok"
          style={{ flex: 1 }}
          disabled={ocupado}
          onClick={() => decidir("approve")}
        >
          {t("gate.aprobar")}
        </button>
        <button
          className="btn warn"
          style={{ flex: 1 }}
          disabled={ocupado || comentario.length < 10}
          onClick={() => decidir("request-changes")}
        >
          {t("gate.cambios")}
        </button>
      </div>
    </div>
  );
}

function BotonCancelar({ numero, alCancelar }: { numero: string; alCancelar: () => void }) {
  const { t } = useI18n();
  const [error, setError] = useState<unknown>(null);
  async function cancelar() {
    const motivo = prompt(t("td.cancelar") + " — motivo:");
    if (!motivo || motivo.length < 5) return;
    try {
      await api.patch(`/api/v1/tickets/${numero}`, {
        accion: "cancelar",
        motivo_cancelacion: motivo,
      });
      alCancelar();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div>
      <CajaError error={error} />
      <button className="btn warn" style={{ width: "100%" }} onClick={cancelar}>
        {t("td.cancelar")}
      </button>
    </div>
  );
}
