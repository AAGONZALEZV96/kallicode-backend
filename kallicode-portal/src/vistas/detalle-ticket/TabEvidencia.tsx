/** Pestaña Evidencia QA: resultado por criterio y regresión dirigida. */
import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import SinDatos from "./SinDatos";

export default function TabEvidencia({ numero }: { numero: string }) {
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
