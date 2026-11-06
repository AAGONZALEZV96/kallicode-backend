/**
 * Diálogo modal accesible (KC-PT-06-7), patrón dialog de WAI-ARIA: role="dialog" con
 * aria-modal y título; el foco entra al abrir, queda atrapado con Tab y vuelve a donde
 * estaba al cerrar (o al contenido principal si ese elemento ya no existe, p. ej. el botón
 * de un panel que desapareció). Escape y el fondo lo cierran, salvo con `bloqueado`.
 */
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

const ENFOCABLES =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface Props {
  titulo: string;
  alCerrar: () => void;
  /** Mientras se envía la decisión no se puede cerrar. */
  bloqueado?: boolean;
  children: ReactNode;
  testid?: string;
}

export default function Modal({ titulo, alCerrar, bloqueado = false, children, testid }: Props) {
  const idTitulo = useId();
  const dialogo = useRef<HTMLDivElement>(null);

  // Al abrir: recuerda el foco y entra al primer control. Al cerrar: lo devuelve.
  useEffect(() => {
    const previo = document.activeElement as HTMLElement | null;
    const primero = dialogo.current?.querySelector<HTMLElement>(ENFOCABLES);
    (primero ?? dialogo.current)?.focus();
    return () => {
      if (previo?.isConnected) previo.focus();
      else document.getElementById("contenido")?.focus();
    };
  }, []);

  function teclado(e: KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      if (!bloqueado) alCerrar();
      return;
    }
    if (e.key !== "Tab" || !dialogo.current) return;
    const controles = [...dialogo.current.querySelectorAll<HTMLElement>(ENFOCABLES)];
    if (controles.length === 0) return e.preventDefault();
    const primero = controles[0]!;
    const ultimo = controles[controles.length - 1]!;
    if (e.shiftKey && document.activeElement === primero) {
      e.preventDefault();
      ultimo.focus();
    } else if (!e.shiftKey && document.activeElement === ultimo) {
      e.preventDefault();
      primero.focus();
    }
  }

  return (
    // El fondo solo cierra con el mouse; con teclado se cierra con Escape (en el diálogo).
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <div
      className="modal-fondo"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !bloqueado) alCerrar();
      }}
    >
      {/* Patrón dialog de WAI-ARIA: Escape y el atrapado de Tab se escuchan en el diálogo. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <div
        ref={dialogo}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={idTitulo}
        tabIndex={-1}
        onKeyDown={teclado}
        data-testid={testid}
      >
        <h2 id={idTitulo}>{titulo}</h2>
        {children}
      </div>
    </div>
  );
}
