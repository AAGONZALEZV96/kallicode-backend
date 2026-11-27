/**
 * Diálogo de confirmación (KC-PT-11-6) para acciones que no se pueden deshacer: descartar un
 * formulario con algo escrito, encender la fábrica. Sobre <Modal>: el foco entra al abrir y
 * vuelve al botón que lo abrió al cerrar; Escape cancela.
 *
 * Los data-testid se arman con `vista` y `zona`: <vista>.<zona>.dialogo | cancelar | confirmar.
 */
import type { ReactNode } from "react";

import { useI18n } from "../estado/i18n";
import Modal from "./Modal";
import { CajaError } from "./ui";

export default function Confirmacion({
  vista,
  zona,
  titulo,
  children,
  textoConfirmar,
  textoCancelar,
  alConfirmar,
  alCerrar,
  ocupado = false,
  peligro = false,
  error,
}: {
  vista: string;
  zona: string;
  titulo: string;
  children: ReactNode;
  textoConfirmar: string;
  /** Por defecto, «Cancelar». */
  textoCancelar?: string;
  alConfirmar: () => void;
  alCerrar: () => void;
  /** Mientras se ejecuta la acción no se puede cerrar ni repetir. */
  ocupado?: boolean;
  /** Acción que borra algo: el botón se ve como advertencia. */
  peligro?: boolean;
  error?: unknown;
}) {
  const { t } = useI18n();
  return (
    <Modal
      titulo={titulo}
      alCerrar={alCerrar}
      bloqueado={ocupado}
      testid={`${vista}.${zona}.dialogo`}
    >
      {children}
      <CajaError error={error} />
      <div className="modal-acciones">
        <button
          type="button"
          className="btn"
          disabled={ocupado}
          onClick={alCerrar}
          data-testid={`${vista}.${zona}.cancelar`}
        >
          {textoCancelar ?? t("comun.cancelar")}
        </button>
        <button
          type="button"
          className={peligro ? "btn warn" : "btn primary"}
          disabled={ocupado}
          onClick={alConfirmar}
          data-testid={`${vista}.${zona}.confirmar`}
        >
          {textoConfirmar}
        </button>
      </div>
    </Modal>
  );
}
