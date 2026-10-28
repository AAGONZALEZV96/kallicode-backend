/**
 * Paginación reutilizable de los listados (KC-PT-04-5, cierra PT-F16).
 *
 * Calcula las páginas con el `page_size` que devolvió el backend, no con el pedido: el
 * servidor lo puede ajustar (máximo 100). Los listados sin `total` (actividad del
 * ticket) solo saben si hay más: una página llena indica que puede haber otra.
 * Con una sola página no se muestra.
 */
import { useI18n } from "../estado/i18n";

interface Props {
  pagina: number;
  /** `page_size` de la respuesta. */
  tamano: number;
  /** `total` de la respuesta; se omite en los listados que no lo informan. */
  total?: number;
  /** Elementos de la página actual. */
  enPagina: number;
  alCambiar: (pagina: number) => void;
  /** Vista que la contiene: data-testid `<vista>.paginacion.<elemento>` (una por vista). */
  vista: string;
}

export default function Paginacion({ pagina, tamano, total, enPagina, alCambiar, vista }: Props) {
  const { t } = useI18n();
  const porPagina = Math.max(1, tamano);
  const paginas = total === undefined ? null : Math.max(1, Math.ceil(total / porPagina));
  const haySiguiente = paginas === null ? enPagina >= porPagina : pagina < paginas;
  if (pagina <= 1 && !haySiguiente) return null;

  return (
    <nav
      className="paginacion"
      aria-label={t("pag.etiqueta")}
      data-testid={`${vista}.paginacion.contenedor`}
    >
      <button
        type="button"
        className="btn sm"
        aria-label={t("pag.anterior")}
        disabled={pagina <= 1}
        onClick={() => alCambiar(pagina - 1)}
        data-testid={`${vista}.paginacion.anterior`}
      >
        ←
      </button>
      <span aria-live="polite" data-testid={`${vista}.paginacion.estado`}>
        {paginas === null
          ? t("pag.pagina", { pagina: String(pagina) })
          : t("pag.de", { pagina: String(pagina), paginas: String(paginas) })}
      </span>
      <button
        type="button"
        className="btn sm"
        aria-label={t("pag.siguiente")}
        disabled={!haySiguiente}
        onClick={() => alCambiar(pagina + 1)}
        data-testid={`${vista}.paginacion.siguiente`}
      >
        →
      </button>
    </nav>
  );
}
