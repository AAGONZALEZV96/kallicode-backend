/** Expediente de auditoría de un ticket (§11.2): integridad, firmas, pasos LLM y cambios. */
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { useExpediente, useVerificarIntegridad } from "../api/consultas";
import { estadoSello, type EstadoSello } from "../api/integridad";
import type { PasoLLM } from "../api/tipos";
import Exportacion from "../componentes/Exportacion";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";
import PanelPaso from "./expediente/PanelPaso";
import VerificadorIntegridad from "./expediente/VerificadorIntegridad";
import VisorDiff from "./expediente/VisorDiff";

export default function Expediente() {
  const { numero = "" } = useParams();
  const { t } = useI18n();
  const { data: datos, error } = useExpediente(numero);
  const { puedeHacer } = usePermiso();
  // Compartida por el verificador y la lista de eventos, que marca el sello de cada uno.
  const verificar = useVerificarIntegridad(numero);
  // Paso LLM abierto en el panel de inspección (KC-PT-08-2).
  const [inspeccionado, setInspeccionado] = useState<PasoLLM | null>(null);
  // Al llegar desde el explorador, se vuelve con sus filtros (KC-PT-08-5). Solo una query.
  const { state } = useLocation();
  const volver = (state as { volver?: unknown } | null)?.volver;
  const filtrosDeVuelta = typeof volver === "string" && volver.startsWith("?") ? volver : "";

  if (error)
    return (
      <div className="view" data-testid="expediente.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  if (!datos) return <Cargando />;

  const verificacion = verificar.data ?? datos.integridad;
  const puedeInspeccionar = puedeHacer({ tipo: "inspeccionar-paso" });
  const marcas: Record<EstadoSello, string> = {
    confirmado: `✓ ${t("au.int.sello.confirmado")}`,
    alterado: `✗ ${t("au.int.sello.alterado")}`,
    "sin-confirmar": `? ${t("au.int.sello.sin_confirmar")}`,
  };

  return (
    <div className="view" data-testid="expediente.vista.raiz">
      <div className="view-head">
        <div>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            <Link
              to={`/auditoria${filtrosDeVuelta}`}
              style={{ color: "var(--muted)" }}
              data-testid="expediente.cabecera.volver"
            >
              ← {t("au.title")}
            </Link>
          </p>
          <h1>
            {t("au.expediente")} · {numero}
          </h1>
        </div>
      </div>

      {/* Expediente en PDF y eventos del ticket en CSV (KC-PT-08-3); el approver solo lo ve. */}
      {puedeHacer({ tipo: "exportar-auditoria" }) && (
        <div className="card" style={{ marginBottom: 16 }}>
          <Exportacion
            vista="expediente"
            formatos={["pdf", "csv"]}
            pedido={{ ticket_id: numero }}
            ayuda={t("au.exp.ayuda_ticket")}
          />
        </div>
      )}

      <div className="grid2" style={{ marginBottom: 16 }}>
        <div className="card">
          <p className="caption">{t("au.solicitante")}</p>
          <p style={{ marginTop: 8 }} data-testid="expediente.solicitante.datos">
            <b>{datos.solicitante.nombre ?? "—"}</b> · {datos.solicitante.origen}
            {" · "}
            {fechaCorta(datos.solicitante.fecha)}
          </p>
        </div>
        {/* Verificación de sellos del tramo del ticket (KC-PT-08-4). */}
        <div className="card">
          <VerificadorIntegridad
            integridad={datos.integridad}
            eventos={datos.eventos}
            verificar={puedeHacer({ tipo: "verificar-integridad" }) ? verificar : null}
          />
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <p className="caption">{t("au.firmas")}</p>
        <table className="kc" style={{ marginTop: 6 }} data-testid="expediente.firmas.tabla">
          <thead>
            <tr>
              <th>Gate</th>
              <th>{t("au.col.actor")}</th>
              <th>{t("au.col.fecha")}</th>
              <th>{t("au.col.comentario")}</th>
              <th>{t("au.col.sello")}</th>
            </tr>
          </thead>
          <tbody>
            {datos.firmas.map((f, i) => (
              <tr key={i}>
                <td>
                  <span className="chip gate">⛨ Gate {f.gate}</span>
                </td>
                <td>👤 {f.actor}</td>
                <td>{fechaCorta(f.fecha)}</td>
                <td style={{ fontSize: 12 }}>{f.comentario ?? "—"}</td>
                <td className="mono" style={{ fontSize: 11 }}>
                  {f.sello.slice(0, 4)}…{f.sello.slice(-4)} ✓
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginBottom: 16 }} data-testid="expediente.pasos.lista">
        <p className="caption">{t("au.pasos")}</p>
        {datos.etapas.map((e) => (
          <div key={e.etapa} className="etapa-pasos" style={{ marginTop: 10 }}>
            <b style={{ fontSize: 12, textTransform: "uppercase", color: "var(--muted)" }}>
              {t(`st.${e.etapa}`)}
            </b>
            <table className="kc">
              <tbody>
                {e.pasos.map((p) => (
                  <tr key={p.step_id}>
                    <td>
                      <b>{p.paso}</b>
                    </td>
                    <td>
                      <span className="chip mono" style={{ fontSize: 10.5 }}>
                        {p.modelo} · {p.tier}
                      </span>
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {p.confianza != null && <>confianza {p.confianza} · </>}
                      {p.validacion}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {(p.tokens / 1000).toFixed(1)}k tok · {p.duracion_ms} ms
                    </td>
                    {puedeInspeccionar && (
                      <td>
                        <button
                          type="button"
                          className="btn sm"
                          // El nombre accesible empieza con el texto visible (WCAG 2.5.3).
                          aria-label={`${t("au.paso.inspeccionar")} ${p.paso}`}
                          onClick={() => setInspeccionado(p)}
                          data-testid="expediente.pasos.inspeccionar"
                        >
                          {t("au.paso.inspeccionar")}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }} data-testid="expediente.cambios.lista">
        <p className="caption">{t("au.cambios")}</p>
        {/* Diffs por archivo con su racional y su sello verificado (KC-PT-08-1). */}
        <VisorDiff cambios={datos.cambios} />
      </div>

      {datos.eventos.length > 0 && (
        <div className="card" data-testid="expediente.eventos.lista">
          <p className="caption">{t("au.eventos")}</p>
          <ul className="audit" style={{ marginTop: 6 }}>
            {datos.eventos.map((ev) => {
              const sello = estadoSello(ev.id, verificacion);
              return (
                <li key={ev.id} data-sello={sello}>
                  <span className="when">{fechaCorta(ev.creado_en)}</span>
                  <span className={`who ${ev.actor_tipo === "humano" ? "human" : "ai"}`}>
                    {ev.actor_id ?? "sistema"}
                  </span>
                  <span>
                    {ev.resumen}
                    <span className="mono" style={{ fontSize: 10, color: "var(--muted)" }}>
                      {" "}
                      {ev.sello.slice(0, 6)}…
                    </span>
                  </span>
                  <span className={`sello ${sello}`}>{marcas[sello]}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {inspeccionado && <PanelPaso paso={inspeccionado} alCerrar={() => setInspeccionado(null)} />}
    </div>
  );
}
