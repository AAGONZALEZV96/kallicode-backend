/** Cancelar el ticket con un motivo (el modal accesible de cancelación llega con KC-PT-06-5). */
import { useCancelarTicket } from "../../api/consultas";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";

export default function BotonCancelar({
  numero,
  alCancelar,
}: {
  numero: string;
  alCancelar: () => void;
}) {
  const { t } = useI18n();
  const cancelacion = useCancelarTicket(numero);
  function cancelar() {
    const motivo = prompt(t("td.cancelar") + " — motivo:");
    if (!motivo || motivo.length < 5) return;
    cancelacion.mutate(motivo, { onSuccess: alCancelar });
  }
  return (
    <div>
      <CajaError error={cancelacion.error} />
      <button
        className="btn warn"
        style={{ width: "100%" }}
        disabled={cancelacion.isPending}
        onClick={cancelar}
        data-testid="detalle-ticket.acciones.cancelar"
      >
        {t("td.cancelar")}
      </button>
    </div>
  );
}
