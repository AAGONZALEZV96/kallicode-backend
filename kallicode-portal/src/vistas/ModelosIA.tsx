/** Modelos IA (§14, modalidad gestionada): los tres tiers y la asignación
 * por paso del pipeline con su política de escalado. */
import { useAsignacionesLLM, useProveedoresLLM } from "../api/consultas";
import { EsqueletoTabla, EsqueletoTarjetas } from "../componentes/Esqueleto";
import Desplazable from "../componentes/Desplazable";
import { FilaVacia } from "../componentes/EstadoVacio";
import { CajaError } from "../componentes/ui";
import { useI18n } from "../estado/i18n";

/** Color de cada tier (clases borde-sup-* y texto-*); uno desconocido queda neutro. */
const COLOR_TIER: Record<string, "ok" | "warn" | "gate"> = {
  flash: "ok",
  pro: "warn",
  fable: "gate",
};

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
  // Mientras carga: el título y la silueta (sin vista.raiz, que indica que la vista cargó).
  if (!prov || !asig)
    return (
      <div className="view">
        <div className="view-head">
          <div>
            <h1>{t("mo.title")}</h1>
            <p>{t("mo.sub")}</p>
          </div>
        </div>
        <EsqueletoTarjetas tarjetas={3} />
        <EsqueletoTabla columnas={6} anunciar={false} />
      </div>
    );

  return (
    <div className="view" data-testid="modelos-ia.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("mo.title")}</h1>
          <p>{t("mo.sub")}</p>
        </div>
      </div>

      <div className="grid3 mb-16">
        {prov.tiers.map((tier) => (
          <div
            className={`card borde-sup-${COLOR_TIER[tier.tier] ?? "border"}`}
            key={tier.tier}
            data-testid="modelos-ia.tiers.tarjeta"
          >
            <span className="chip">{tier.tier}</span>
            <h2 className="titulo-tarjeta">{tier.nombre}</h2>
            <p className="mono fs-12">{tier.modelo}</p>
            <p className="texto-tenue fs-12 mt-6">{tier.perfil}</p>
          </div>
        ))}
      </div>

      <div className="note mb-16" data-testid="modelos-ia.politica.texto">
        {asig.politica_escalado}
      </div>

      <div className="card p-0">
        <Desplazable etiqueta={t("reg.modelos")}>
          <table className="kc" data-testid="modelos-ia.asignaciones.tabla">
            <thead>
              <tr>
                <th>{t("mo.col.paso")}</th>
                <th>{t("tk.col.etapa")}</th>
                <th>Tier</th>
                <th>{t("tc.modelo")}</th>
                <th>{t("mo.col.escalable")}</th>
                <th>{t("mo.col.descripcion")}</th>
              </tr>
            </thead>
            <tbody>
              {asig.asignaciones.map((a) => (
                <tr key={a.paso} data-testid="modelos-ia.asignaciones.fila">
                  <td className="mono fs-12">
                    <b>{a.paso}</b>
                  </td>
                  <td>{t(`st.${a.etapa}`) !== `st.${a.etapa}` ? t(`st.${a.etapa}`) : a.etapa}</td>
                  <td>
                    <span className={`chip texto-${COLOR_TIER[a.tier] ?? "texto"}`}>{a.tier}</span>
                  </td>
                  <td className="mono fs-12">{a.modelo}</td>
                  <td>{a.escalable ? "flash→pro→fable" : t("mo.fijo")}</td>
                  <td className="texto-tenue fs-12">{a.descripcion}</td>
                </tr>
              ))}
              {asig.asignaciones.length === 0 && (
                <FilaVacia
                  columnas={6}
                  testid="modelos-ia.asignaciones.vacio"
                  titulo={t("vacio.modelos")}
                  descripcion={t("vacio.modelos.desc")}
                />
              )}
            </tbody>
          </table>
        </Desplazable>
      </div>
    </div>
  );
}
