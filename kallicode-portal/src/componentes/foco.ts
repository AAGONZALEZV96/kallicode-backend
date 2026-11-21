/**
 * Foco de los paneles que se abren en línea, sin diálogo (KC-PT-07-6): un formulario que
 * reemplaza al botón que lo abrió. Si nadie mueve el foco, al cerrarse queda en <body> y
 * quien navega con teclado o lector de pantalla vuelve al inicio de la página.
 */
import { useEffect, useRef } from "react";

/**
 * Devuelve el foco al botón que abrió un panel cuando `abierto` pasa de true a false
 * (cancelar, Escape o terminar). Se usa con el ref que devuelve, puesto en ese botón.
 */
export function useFocoAlCerrar<T extends HTMLElement>(abierto: boolean) {
  const disparador = useRef<T>(null);
  const antes = useRef(abierto);
  useEffect(() => {
    if (antes.current && !abierto) disparador.current?.focus();
    antes.current = abierto;
  }, [abierto]);
  return disparador;
}
