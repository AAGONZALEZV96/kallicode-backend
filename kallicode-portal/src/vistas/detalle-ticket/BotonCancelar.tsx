/** Abre el modal de cancelación del ticket (KC-PT-06-5). */
import { useState } from "react";

import { useI18n } from "../../estado/i18n";
import ModalCancelacion from "./ModalCancelacion";

export default function BotonCancelar({
  numero,
  alCancelar,
}: {
  numero: string;
  alCancelar: () => void;
}) {
  const { t } = useI18n();
  const [abierto, setAbierto] = useState(false);
  return (
    <div>
      <button
        type="button"
        className="btn warn"
        style={{ width: "100%" }}
        aria-haspopup="dialog"
        onClick={() => setAbierto(true)}
        data-testid="detalle-ticket.acciones.cancelar"
      >
        {t("td.cancelar")}
      </button>
      {abierto && (
        <ModalCancelacion
          numero={numero}
          alCerrar={() => setAbierto(false)}
          alCancelar={alCancelar}
        />
      )}
    </div>
  );
}
