/** Pestaña Spec: alternativas evaluadas, criterios Given/When/Then y alcance autorizado. */
import { usePestanaTicket } from "../../api/consultas";
import Desplazable from "../../componentes/Desplazable";
import { CajaError, Cargando } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import SinDatos from "./SinDatos";

export default function TabSpec({ numero }: { numero: string }) {
  const { t } = useI18n();
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "spec");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="spec" />;
  return (
    <div className="columna gap-12" data-testid="detalle-ticket.spec.contenido">
      <p className="texto-tenue fs-12">
        {t("sp.version", { v: String(datos.version) })} · {t(`sp.estado.${datos.estado}`)}
      </p>
      <div>
        <b>{t("td.alt")}</b>
        <div className="grid2 mt-6">
          {datos.alternativas.map((a) => (
            <div key={a.id} className={a.elegida || a.recomendada ? "card destacada" : "card"}>
              <b>
                {a.id} · {a.titulo}
              </b>
              <p className="texto-tenue fs-12">
                {a.descripcion} {a.recomendada && "✔"}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div>
        <b>{t("td.criterios")}</b>
        <Desplazable etiqueta={t("td.criterios")}>
          <table className="kc mt-6">
            <tbody>
              {datos.criterios.map((c) => (
                <tr key={c.id}>
                  <td className="ancho-60">
                    <b>{c.id}</b>
                  </td>
                  <td>{t("sp.criterio", { dado: c.given, cuando: c.when, entonces: c.then })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Desplazable>
      </div>
      <div>
        <b>{t("td.scope")}</b>
        <p className="note mt-6">
          <span className="mono">{datos.scope_paths.join(" · ")}</span>
        </p>
      </div>
    </div>
  );
}
