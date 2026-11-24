/**
 * ¿El contenido de un contenedor es más ancho o más alto que lo visible? (KC-PT-10-2). Se vuelve
 * a medir cuando cambia el tamaño del contenedor o de su contenido, y cuando el contenido cambia
 * (otra vista, datos que llegan). Lo usan Desplazable y el <main> del layout para que una zona
 * que se desplaza se pueda enfocar con Tab cuando dentro no hay nada enfocable.
 */
import { useEffect, useState, type RefObject } from "react";

/** Lo que se enfoca con Tab. */
const ENFOCABLES =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

export function useDesborda(ref: RefObject<HTMLElement | null>) {
  const [estado, setEstado] = useState({ desborda: false, conEnfocables: false });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const medir = () => {
      const desborda = el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;
      const conEnfocables = el.querySelector(ENFOCABLES) !== null;
      setEstado((antes) =>
        antes.desborda === desborda && antes.conEnfocables === conEnfocables
          ? antes
          : { desborda, conEnfocables },
      );
    };
    medir();
    const limpiar: (() => void)[] = [];
    if (typeof ResizeObserver !== "undefined") {
      const tamano = new ResizeObserver(medir);
      tamano.observe(el);
      for (const hijo of Array.from(el.children)) tamano.observe(hijo);
      limpiar.push(() => tamano.disconnect());
    }
    if (typeof MutationObserver !== "undefined") {
      const cambios = new MutationObserver(medir);
      // También cuando algo de adentro pasa a enfocarse (un Desplazable que empieza a desbordar).
      cambios.observe(el, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["tabindex"],
      });
      limpiar.push(() => cambios.disconnect());
    }
    return () => limpiar.forEach((f) => f());
  }, [ref]);
  return estado;
}
