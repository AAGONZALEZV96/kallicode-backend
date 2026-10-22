/** Detalle de ticket (§9.6): stepper del pipeline, 5 pestañas y panel de gate.
 * Las pestañas cargan bajo demanda; el panel de gate firma o devuelve (§10). */
import { useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  useCancelarTicket,
  useDecidirGate,
  usePestanaTicket,
  useTicket,
  type Pestana,
} from "../api/consultas";
import Paginacion from "../componentes/Paginacion";
import { CajaError, Cargando, ChipEtapa, ChipTipo, Prio, fechaCorta } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";
import { usePaginaUrl } from "../usePaginaUrl";
import { sanearUrl } from "../urls";

const ETAPAS_PIPE = ["triage", "design", "build", "qa", "security", "deploy", "produccion"];
const PESTANAS: Pestana[] = ["spec", "evidence", "security", "usage", "activity"];
/** Zona data-testid de cada pestaña (detalle-ticket.pestanas.<zona>, detalle-ticket.<zona>.*). */
const ZONA_PESTANA: Record<Pestana, string> = {
  spec: "spec",
  evidence: "evidencia",
  security: "seguridad",
  usage: "consumo",
  activity: "actividad",
};

export default function DetalleTicket() {
  const { numero = "" } = useParams();
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const nav = useNavigate();
  const { data: tk, error } = useTicket(numero);
  const [pestana, setPestana] = useState<Pestana>("spec");

  if (error)
    return (
      <div className="view" data-testid="detalle-ticket.vista.raiz">
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
    <div className="view" data-testid="detalle-ticket.vista.raiz">
      <div className="view-head">
        <div>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            <Link
              to="/tickets"
              style={{ color: "var(--muted)" }}
              data-testid="detalle-ticket.cabecera.volver"
            >
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
                data-testid={`detalle-ticket.pipeline.etapa-${e}`}
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
            {PESTANAS.map((p) => (
              <button
                key={p}
                className={pestana === p ? "active" : ""}
                onClick={() => setPestana(p)}
                data-testid={`detalle-ticket.pestanas.${ZONA_PESTANA[p]}`}
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
          {/* La clave reinicia el panel (comentario, alternativa) al pasar a otro gate. */}
          {puedeFirmar && tk.gate_pendiente && (
            <PanelGate key={tk.gate_pendiente} numero={numero} gate={tk.gate_pendiente} />
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
                      <a
                        href={prUrl}
                        target="_blank"
                        rel="noreferrer"
                        data-testid="detalle-ticket.resumen.pr"
                      >
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
            <div
              style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}
              data-testid="detalle-ticket.visualizadores.lista"
            >
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
function TabSpec({ numero }: { numero: string }) {
  const { t } = useI18n();
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "spec");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="spec" />;
  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 12 }}
      data-testid="detalle-ticket.spec.contenido"
    >
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
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "evidence");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="evidencia" />;
  return (
    <>
      <table className="kc" data-testid="detalle-ticket.evidencia.tabla">
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
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "security");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="seguridad" />;
  return (
    <>
      <table className="kc" data-testid="detalle-ticket.seguridad.tabla">
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
        <div className="error-box" data-testid="detalle-ticket.seguridad.bloqueo">
          Severidad alta sin resolver: bloquea el Deploy.
        </div>
      )}
    </>
  );
}

function TabConsumo({ numero }: { numero: string }) {
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "usage");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="consumo" />;
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
      <table className="kc" data-testid="detalle-ticket.consumo.tabla">
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
  // Parámetro propio: no choca con la página de otro listado si se comparte la URL.
  const [pagina, irAPagina] = usePaginaUrl("actividad");
  const {
    data: datos,
    error,
    isPending: cargando,
  } = usePestanaTicket(numero, "activity", { pagina });
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="actividad" />;
  return (
    <>
      <ul className="audit" data-testid="detalle-ticket.actividad.lista">
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
      {/* La actividad no informa `total`: solo se sabe si hay más. */}
      <Paginacion
        pagina={pagina}
        tamano={datos.page_size}
        enPagina={datos.items.length}
        alCambiar={irAPagina}
        vista="detalle-ticket"
      />
    </>
  );
}

/** Pestaña sin datos todavía (404 del backend): detalle-ticket.<zona>.sin-datos. */
function SinDatos({ zona }: { zona: string }) {
  return (
    <p className="note" data-testid={`detalle-ticket.${zona}.sin-datos`}>
      —
    </p>
  );
}

/* ---------------- panel de gate ------------------------------------------ */
function PanelGate({ numero, gate }: { numero: string; gate: number }) {
  const { t } = useI18n();
  const [comentario, setComentario] = useState("");
  // null = aún no elegida: se propone la recomendada de la spec.
  const [elegida, setElegida] = useState<string | null>(null);
  const idAlternativa = useId();
  const idComentario = useId();
  // Misma consulta que la pestaña Spec: no se pide dos veces.
  const { data: spec } = usePestanaTicket(numero, "spec", { habilitada: gate === 1 });
  const alts = gate === 1 ? (spec?.alternativas ?? []) : [];
  const alternativa = elegida ?? alts.find((a) => a.recomendada)?.id ?? "";
  // Al terminar se invalidan juntos el ticket, el tablero, las aprobaciones y la auditoría.
  const decision = useDecidirGate(numero, gate);

  function decidir(accion: "approve" | "request-changes") {
    decision.mutate(
      accion === "approve"
        ? {
            accion,
            cuerpo: {
              comentario: comentario || undefined,
              alternativa_id: gate === 1 && alts.length > 1 ? alternativa : undefined,
            },
          }
        : { accion, cuerpo: { comentario } },
    );
  }
  const ocupado = decision.isPending;

  return (
    <div className="gate-panel" data-testid="detalle-ticket.gate.panel">
      <h3>{t(`gate.g${gate}`)}</h3>
      {gate === 1 && alts.length > 1 && (
        <div style={{ marginBottom: 8 }}>
          <label htmlFor={idAlternativa}>Alternativa</label>
          <select
            id={idAlternativa}
            value={alternativa}
            onChange={(e) => setElegida(e.target.value)}
            data-testid="detalle-ticket.gate.alternativa"
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
        data-testid="detalle-ticket.gate.comentario"
      />
      <CajaError error={decision.error} />
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          className="btn ok"
          style={{ flex: 1 }}
          disabled={ocupado}
          onClick={() => decidir("approve")}
          data-testid="detalle-ticket.gate.aprobar"
        >
          {t("gate.aprobar")}
        </button>
        <button
          className="btn warn"
          style={{ flex: 1 }}
          disabled={ocupado || comentario.length < 10}
          onClick={() => decidir("request-changes")}
          data-testid="detalle-ticket.gate.pedir-cambios"
        >
          {t("gate.cambios")}
        </button>
      </div>
    </div>
  );
}

function BotonCancelar({ numero, alCancelar }: { numero: string; alCancelar: () => void }) {
  const { t } = useI18n();
  const cancelacion = useCancelarTicket(numero);
  function cancelar() {
    const motivo = prompt(t("td.cancelar") + " — motivo:");
    if (!motivo || motivo.length < 5) return;
    cancelacion.mutate(motivo, { onSuccess: alCancelar });
  }
  return (
    <div>
      <CajaError error={cancelacion.error} />
      <button
        className="btn warn"
        style={{ width: "100%" }}
        disabled={cancelacion.isPending}
        onClick={cancelar}
        data-testid="detalle-ticket.acciones.cancelar"
      >
        {t("td.cancelar")}
      </button>
    </div>
  );
}
