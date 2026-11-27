/** Pestaña Consumo: totales de tokens, LOC e invocaciones, y detalle por función. */
import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import Desplazable from "../../componentes/Desplazable";
import { useFormato } from "../../estado/formato";
import { useI18n } from "../../estado/i18n";
import SinDatos from "./SinDatos";

export default function TabConsumo({ numero }: { numero: string }) {
  const fmt = useFormato();
  const { t } = useI18n();
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "usage");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="consumo" />;
  return (
    <>
      <div className="grid3 mb-14">
        <div className="card kpi compacto">
          <div className="lbl">{t("tc.tokens")}</div>
          <div className="num fs-20">
            {fmt.compacto(datos.totales.tokens_in + datos.totales.tokens_out)}
          </div>
        </div>
        <div className="card kpi compacto">
          <div className="lbl">{t("cu.col.loc")}</div>
          <div className="num fs-20">
            {datos.totales.loc_netas}
            <span className="texto-tenue fs-12">
              {" "}
              {t("tc.tests", { n: String(datos.totales.loc_tests) })}
            </span>
          </div>
        </div>
        <div className="card kpi compacto">
          <div className="lbl">{t("tc.invocaciones")}</div>
          <div className="num fs-20">{datos.totales.invocaciones}</div>
        </div>
      </div>
      {/* La tabla se desplaza dentro de la pestaña en vez de salirse de la tarjeta (KC-PT-10-1). */}
      <Desplazable etiqueta={t("reg.consumo")}>
        <table className="kc" data-testid="detalle-ticket.consumo.tabla">
          <thead>
            <tr>
              <th>{t("tc.funcion")}</th>
              <th>{t("tc.modelo")}</th>
              <th>
                <abbr title={t("tc.invocaciones")}>{t("tc.inv")}</abbr>
              </th>
              <th>{t("tc.tokens_in")}</th>
              <th>{t("tc.tokens_out")}</th>
            </tr>
          </thead>
          <tbody className="mono fs-12">
            {datos.por_funcion.map((f, i) => (
              <tr key={i}>
                <td>{f.funcion}</td>
                <td>{f.modelo}</td>
                <td>{f.invocaciones}</td>
                <td>{fmt.numero(f.tokens_in)}</td>
                <td>{fmt.numero(f.tokens_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Desplazable>
    </>
  );
}
