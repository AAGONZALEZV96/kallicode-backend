/** Pestaña Actividad: eventos del ticket, paginados con su propio parámetro en la URL. */
import { usePestanaTicket } from "../../api/consultas";
import Paginacion from "../../componentes/Paginacion";
import { CajaError, Cargando, fechaCorta } from "../../componentes/ui";
import { usePaginaUrl } from "../../usePaginaUrl";
import SinDatos from "./SinDatos";

export default function TabActividad({ numero }: { numero: string }) {
  // Parámetro propio: no choca con la página de otro listado si se comparte la URL.
  const [pagina, irAPagina] = usePaginaUrl("actividad");
  const {
    data: datos,
    error,
    isPending: cargando,
  } = usePestanaTicket(numero, "activity", { pagina });
  if (cargando) return <Cargando />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="actividad" />;
  return (
    <>
      <ul className="audit" data-testid="detalle-ticket.actividad.lista">
        {datos.items.map((a, i) => (
          <li key={i}>
            <span className="when">{fechaCorta(a.fecha)}</span>
            <span className={`who ${a.actor.tipo === "humano" ? "human" : "ai"}`}>
              {a.actor.nombre}
            </span>
            <span>{a.evento}</span>
          </li>
        ))}
      </ul>
      {/* La actividad no informa `total`: solo se sabe si hay más. */}
      <Paginacion
        pagina={pagina}
        tamano={datos.page_size}
        enPagina={datos.items.length}
        alCambiar={irAPagina}
        vista="detalle-ticket"
      />
    </>
  );
}
