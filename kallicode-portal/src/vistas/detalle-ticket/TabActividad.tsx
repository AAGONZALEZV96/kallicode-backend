/** Pestaña Actividad: eventos del ticket, paginados con su propio parámetro en la URL. */
import { usePestanaTicket } from "../../api/consultas";
import Paginacion from "../../componentes/Paginacion";
import { EsqueletoLista } from "../../componentes/Esqueleto";
import { EstadoVacio } from "../../componentes/EstadoVacio";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import { useFormato } from "../../estado/formato";
import { usePaginaUrl } from "../../usePaginaUrl";
import SinDatos from "./SinDatos";

export default function TabActividad({ numero }: { numero: string }) {
  const fmt = useFormato();
  const { t } = useI18n();
  // Parámetro propio: no choca con la página de otro listado si se comparte la URL.
  const [pagina, irAPagina] = usePaginaUrl("actividad");
  const {
    data: datos,
    error,
    isPending: cargando,
  } = usePestanaTicket(numero, "activity", { pagina });
  if (cargando) return <EsqueletoLista elementos={5} />;
  if (error) return <CajaError error={error} />;
  if (!datos) return <SinDatos zona="actividad" />;
  if (datos.items.length === 0 && datos.page === 1)
    return (
      <EstadoVacio
        testid="detalle-ticket.actividad.vacio"
        titulo={t("vacio.actividad")}
        descripcion={t("vacio.actividad.desc")}
      />
    );
  return (
    <>
      <ul className="audit" data-testid="detalle-ticket.actividad.lista">
        {datos.items.map((a, i) => (
          <li key={i}>
            <span className="when">{fmt.fecha(a.fecha)}</span>
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
