/** Modelos IA (§14, modalidad gestionada): los tres tiers y la asignación
 * por paso del pipeline con su política de escalado. */
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { AsignacionesLLM } from "../api/tipos";
import { CajaError, Cargando } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

interface Providers {
  modalidad: string;
  tiers: { tier: string; modelo: string; nombre: string; perfil: string }[];
}

const COLOR_TIER: Record<string, string> = { flash: "ok", pro: "warn", fable: "gate" };

export default function ModelosIA() {
  const { t } = useI18n();
  const [prov, setProv] = useState<Providers | null>(null);
  const [asig, setAsig] = useState<AsignacionesLLM | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api.get<Providers>("/api/v1/models/providers").then(setProv).catch(setError);
    api.get<AsignacionesLLM>("/api/v1/models/assignments").then(setAsig).catch(setError);
  }, []);

  if (error)
    return (
      <div className="view">
        <CajaError error={error} />
      </div>
    );
  if (!prov || !asig) return <Cargando />;

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("mo.title")}</h1>
          <p>{t("mo.sub")}</p>
        </div>
      </div>

      <div className="grid3" style={{ marginBottom: 16 }}>
        {prov.tiers.map((tier) => (
          <div
            className="card"
            key={tier.tier}
            style={{ borderTop: `3px solid var(--${COLOR_TIER[tier.tier] ?? "border"})` }}
          >
            <span className="chip">{tier.tier}</span>
            <h3 style={{ margin: "8px 0 2px", fontSize: 15 }}>{tier.nombre}</h3>
            <p className="mono" style={{ fontSize: 12 }}>
              {tier.modelo}
            </p>
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>{tier.perfil}</p>
          </div>
        ))}
      </div>

      <div className="note" style={{ marginBottom: 16 }}>
        {asig.politica_escalado}
      </div>

      <div className="card" style={{ padding: 0 }}>
        <table className="kc">
          <thead>
            <tr>
              <th>Paso</th>
              <th>Etapa</th>
              <th>Tier</th>
              <th>Modelo</th>
              <th>Escalable</th>
              <th>Descripción</th>
            </tr>
          </thead>
          <tbody>
            {asig.asignaciones.map((a) => (
              <tr key={a.paso}>
                <td className="mono" style={{ fontSize: 12 }}>
                  <b>{a.paso}</b>
                </td>
                <td>{t(`st.${a.etapa}`) !== `st.${a.etapa}` ? t(`st.${a.etapa}`) : a.etapa}</td>
                <td>
                  <span
                    className="chip"
                    style={{ color: `var(--${COLOR_TIER[a.tier] ?? "text"})` }}
                  >
                    {a.tier}
                  </span>
                </td>
                <td className="mono" style={{ fontSize: 12 }}>
                  {a.modelo}
                </td>
                <td>{a.escalable ? "flash→pro→fable" : "fijo"}</td>
                <td style={{ fontSize: 12, color: "var(--muted)" }}>{a.descripcion}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
