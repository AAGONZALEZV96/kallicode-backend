/** Pestaña Seguridad: hallazgos con severidad y estado; avisa si bloquean el Deploy. */
import { usePestanaTicket } from "../../api/consultas";
import { CajaError, Cargando } from "../../componentes/ui";
import SinDatos from "./SinDatos";

export default function TabSeguridad({ numero }: { numero: string }) {
  const { data: datos, error, isPending: cargando } = usePestanaTicket(numero, "security");
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="seguridad" />;
  return (
    <>
      <table className="kc" data-testid="detalle-ticket.seguridad.tabla">
        <thead>
          <tr>
            <th>Severidad</th>
            <th>Hallazgo</th>
            <th>Estado</th>
          </tr>
        </thead>
        <tbody>
          {datos.hallazgos.map((h) => (
            <tr key={h.id}>
              <td>
                <span className={`prio ${h.severidad === "critica" ? "alta" : h.severidad}`}>
                  {h.severidad}
                </span>
              </td>
              <td>
                {h.titulo}
                {h.justificacion && (
                  <p style={{ fontSize: 11, color: "var(--muted)" }}>{h.justificacion}</p>
                )}
              </td>
              <td>{h.estado === "corregido" ? <span className="res pass">✓</span> : h.estado}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {datos.bloquea_deploy && (
        <div className="error-box" role="alert" data-testid="detalle-ticket.seguridad.bloqueo">
          Severidad alta sin resolver: bloquea el Deploy.
        </div>
      )}
    </>
  );
}
