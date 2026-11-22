/**
 * Estado vacío común de las listas (KC-PT-09-4, cierra PT-F28): qué pasa (título), por qué o
 * qué hacer (descripción) y, si aplica, una acción. Antes cada vista mostraba un «Sin
 * resultados» suelto con su propio estilo, y algunas no mostraban nada.
 *
 * - `<EstadoVacio>` va donde estaría la lista; `<FilaVacia>` dentro del <tbody> de una tabla,
 *   en una fila que ocupa todas las columnas (el data-testid va en la fila).
 * - El ícono es decorativo (aria-hidden): el título ya dice lo que pasa.
 */
import type { ReactNode } from "react";

interface Props {
  titulo: string;
  descripcion?: string;
  /** Un botón o enlace que resuelve el vacío (p. ej. «Limpiar filtros»). */
  accion?: ReactNode;
  testid?: string;
  /** Más compacto, para vacíos dentro de una tarjeta pequeña (p. ej. una categoría). */
  compacto?: boolean;
}

export function EstadoVacio({ titulo, descripcion, accion, testid, compacto = false }: Props) {
  return (
    <div className={compacto ? "estado-vacio compacto" : "estado-vacio"} data-testid={testid}>
      <svg
        className="estado-vacio-icono"
        viewBox="0 0 24 24"
        width="28"
        height="28"
        aria-hidden="true"
        focusable="false"
      >
        <path
          d="M4 7h16v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7Zm0 0 2-3h12l2 3M9 12h6"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <p className="estado-vacio-titulo">{titulo}</p>
      {descripcion && <p className="estado-vacio-descripcion">{descripcion}</p>}
      {accion && <div className="estado-vacio-accion">{accion}</div>}
    </div>
  );
}

/** Estado vacío dentro de una tabla: una fila que ocupa todas las columnas. */
export function FilaVacia({
  columnas,
  testid,
  ...props
}: Omit<Props, "compacto"> & { columnas: number }) {
  return (
    <tr className="fila-vacia" data-testid={testid}>
      <td colSpan={columnas}>
        <EstadoVacio {...props} />
      </td>
    </tr>
  );
}
