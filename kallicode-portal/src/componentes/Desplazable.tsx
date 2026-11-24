/**
 * Contenedor que se desplaza cuando su contenido no cabe: tablas anchas en el celular, el
 * pipeline del ticket, el diff del expediente (KC-PT-10-2, WCAG 2.1.1).
 *
 * Mientras desborda es una región con nombre que se enfoca con Tab y se desplaza con las
 * flechas: sin eso, quien usa teclado no puede ver lo que queda fuera si dentro no hay nada
 * enfocable (axe: scrollable-region-focusable). Cuando todo cabe no agrega una parada de Tab.
 */
import { useRef, type ReactNode } from "react";

import { useDesborda } from "../useDesborda";

export default function Desplazable({
  etiqueta,
  className = "",
  id,
  children,
}: {
  /** Nombre de la región para el lector de pantalla (p. ej. el título de la tabla). */
  etiqueta: string;
  className?: string;
  id?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { desborda } = useDesborda(ref);
  return (
    <div
      ref={ref}
      id={id}
      className={`desplazable ${className}`.trim()}
      {...(desborda ? { tabIndex: 0, role: "region", "aria-label": etiqueta } : {})}
    >
      {children}
    </div>
  );
}
