/** Modelos IA (§14, modalidad gestionada): los tres tiers y la asignación
 * por paso del pipeline con su política de escalado. */
import { useAsignacionesLLM, useProveedoresLLM } from "../api/consultas";
import { CajaError, Cargando } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

const COLOR_TIER: Record<string, string> = { flash: "ok", pro: "warn", fable: "gate" };

export default function ModelosIA() {
  const { t } = useI18n();
  const proveedores = useProveedoresLLM();
  const asignaciones = useAsignacionesLLM();
  const prov = proveedores.data;
  const asig = asignaciones.data;
  const error = proveedores.error ?? asignaciones.error;

  if (error)
    return (
      <div className="view" data-testid="modelos-ia.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  if (!prov || !asig) return <Cargando />;

  return (
    <div className="view" data-testid="modelos-ia.vista.raiz">
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
            data-testid="modelos-ia.tiers.tarjeta"
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

      <div className="note" style={{ marginBottom: 16 }} data-testid="modelos-ia.politica.texto">
        {asig.politica_escalado}
      </div>

      <div className="card" style={{ padding: 0 }}>
        <table className="kc" data-testid="modelos-ia.asignaciones.tabla">
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
              <tr key={a.paso} data-testid="modelos-ia.asignaciones.fila">
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
