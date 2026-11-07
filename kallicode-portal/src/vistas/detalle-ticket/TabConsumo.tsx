/** Pestaña Consumo: totales de tokens, LOC e invocaciones, y detalle por función. */
import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import SinDatos from "./SinDatos";

export default function TabConsumo({ numero }: { numero: string }) {
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
