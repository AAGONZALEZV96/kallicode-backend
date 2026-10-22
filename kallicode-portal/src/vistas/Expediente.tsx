/** Expediente de auditoría de un ticket (§11.2): integridad, firmas, pasos LLM y cambios. */
import { Link, useParams } from "react-router-dom";

import { useExpediente } from "../api/consultas";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { sanearUrl } from "../urls";

export default function Expediente() {
  const { numero = "" } = useParams();
  const { t } = useI18n();
  const { data: datos, error } = useExpediente(numero);

  if (error)
    return (
      <div className="view" data-testid="expediente.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  if (!datos) return <Cargando />;

  return (
    <div className="view" data-testid="expediente.vista.raiz">
      <div className="view-head">
        <div>
          <p style={{ fontSize: 12, color: "var(--muted)" }}>
            <Link
              to="/auditoria"
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

      <div className="grid2" style={{ marginBottom: 16 }}>
        <div className="card">
          <p className="caption">{t("au.solicitante")}</p>
          <p style={{ marginTop: 8 }} data-testid="expediente.solicitante.datos">
            <b>{datos.solicitante.nombre ?? "—"}</b> · {datos.solicitante.origen}
            {" · "}
            {fechaCorta(datos.solicitante.fecha)}
          </p>
        </div>
        <div className="card">
          <p className="caption">{t("au.integridad")}</p>
          <p style={{ marginTop: 8 }}>
            <span
              className={`res ${datos.integridad.integra ? "pass" : "fail"}`}
              data-testid="expediente.integridad.estado"
            >
              {datos.integridad.integra ? `✓ ${t("au.integra")}` : `✗ ${t("au.rota")}`}
            </span>
            {datos.integridad.hash_raiz && (
              <span className="mono" style={{ fontSize: 11, marginLeft: 8 }}>
                root: {datos.integridad.hash_raiz.slice(0, 4)}…
                {datos.integridad.hash_raiz.slice(-4)}
              </span>
            )}
          </p>
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
          <div key={e.etapa} style={{ marginTop: 10 }}>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>

      <div className="card" style={{ marginBottom: 16 }} data-testid="expediente.cambios.lista">
        <p className="caption">{t("au.cambios")}</p>
        {datos.cambios?.length ? (
          <ul className="cambios">
            {datos.cambios.map((c) => {
              const pr = sanearUrl(c.pr_url);
              return (
                <li key={c.archivo}>
                  <span className="mono">{c.archivo}</span>
                  {pr && (
                    <a href={pr} target="_blank" rel="noreferrer">
                      {t("au.ver_pr")} ↗
                    </a>
                  )}
                  <p className="note">{c.racional}</p>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="note" data-testid="expediente.cambios.vacio">
            {t("au.sin_cambios")}
          </p>
        )}
      </div>

      {datos.eventos && (
        <div className="card" data-testid="expediente.eventos.lista">
          <p className="caption">{t("au.eventos")}</p>
          <ul className="audit" style={{ marginTop: 6 }}>
            {datos.eventos.map((ev) => (
              <li key={ev.id}>
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
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
