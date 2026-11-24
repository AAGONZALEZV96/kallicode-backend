/** Pestaña Seguridad: hallazgos con severidad y estado; avisa si bloquean el Deploy. */
import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import SinDatos from "./SinDatos";

export default function TabSeguridad({ numero }: { numero: string }) {
  const { t } = useI18n();
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "security");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="seguridad" />;
  return (
    <>
      <table className="kc" data-testid="detalle-ticket.seguridad.tabla">
        <thead>
          <tr>
            <th>{t("ts.severidad")}</th>
            <th>{t("ts.hallazgo")}</th>
            <th>{t("ts.estado")}</th>
          </tr>
        </thead>
        <tbody>
          {datos.hallazgos.map((h) => (
            <tr key={h.id}>
              <td>
                <span className={`prio ${h.severidad === "critica" ? "alta" : h.severidad}`}>
                  {t(`ts.sev.${h.severidad}`)}
                </span>
              </td>
              <td>
                {h.titulo}
                {h.justificacion && (
                  <p style={{ fontSize: 11, color: "var(--muted)" }}>{h.justificacion}</p>
                )}
              </td>
              <td>
                {h.estado === "corregido" ? (
                  <span className="res pass" title={t("ts.est.corregido")}>
                    ✓<span className="solo-lector"> {t("ts.est.corregido")}</span>
                  </span>
                ) : (
                  t(`ts.est.${h.estado}`)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {datos.bloquea_deploy && (
        <div className="error-box" role="alert" data-testid="detalle-ticket.seguridad.bloqueo">
          {t("ts.bloqueo")}
        </div>
      )}
    </>
  );
}
