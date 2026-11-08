/**
 * Modal de firma de gates (KC-PT-06-7): muestra qué se firma (gate, alternativa y
 * comentario) antes de enviarlo, porque la firma queda sellada en la cadena de auditoría.
 * Después muestra el resultado: el sello de la firma (y el aviso de cuota si el backend lo
 * envía) o la etapa a la que vuelve el ticket.
 *
 * Vive en DetalleTicket y no en el panel: al firmar, el ticket deja de tener gate pendiente
 * y el panel desaparece, pero el resultado tiene que seguir a la vista.
 */
import { useEffect, useRef } from "react";

import { useDecidirGate, type DecisionGate } from "../../api/consultas";
import Modal from "../../componentes/Modal";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";

export interface SolicitudFirma {
  decision: DecisionGate;
  comentario: string;
  /** «A · Título» de la alternativa elegida (solo Gate 1 con más de una). */
  alternativa?: string;
}

interface Props {
  numero: string;
  gate: number;
  solicitud: SolicitudFirma;
  alCerrar: () => void;
}

export default function ModalFirma({ numero, gate, solicitud, alCerrar }: Props) {
  const { t } = useI18n();
  const decision = useDecidirGate(numero, gate);
  const resultado = decision.data;
  const aprobar = solicitud.decision.accion === "approve";
  const cerrar = useRef<HTMLButtonElement>(null);

  // El botón que tenía el foco (confirmar) desaparece con el resultado: el foco pasa a cerrar.
  useEffect(() => {
    if (resultado) cerrar.current?.focus();
  }, [resultado]);

  return (
    <Modal
      titulo={t(aprobar ? "firma.titulo_aprobar" : "firma.titulo_cambios", { gate: String(gate) })}
      alCerrar={alCerrar}
      bloqueado={decision.isPending}
      testid="detalle-ticket.firma.dialogo"
    >
      {!resultado ? (
        <>
          <dl className="firma-resumen" data-testid="detalle-ticket.firma.resumen">
            <dt>{t("firma.ticket")}</dt>
            <dd>{numero}</dd>
            <dt>{t("firma.gate")}</dt>
            <dd>{t(`gate.g${gate}`)}</dd>
            {solicitud.alternativa && (
              <>
                <dt>{t("gate.alternativa")}</dt>
                <dd>{solicitud.alternativa}</dd>
              </>
            )}
            <dt>{t("firma.comentario")}</dt>
            <dd>{solicitud.comentario || t("firma.sin_comentario")}</dd>
          </dl>
          <p className="note">{t(aprobar ? "firma.aviso_aprobar" : "firma.aviso_cambios")}</p>
          <CajaError error={decision.error} />
          <div className="modal-acciones">
            <button
              type="button"
              className="btn"
              disabled={decision.isPending}
              onClick={alCerrar}
              data-testid="detalle-ticket.firma.cancelar"
            >
              {t("comun.cancelar")}
            </button>
            <button
              type="button"
              className={aprobar ? "btn ok" : "btn warn"}
              disabled={decision.isPending}
              onClick={() => decision.mutate(solicitud.decision)}
              data-testid="detalle-ticket.firma.confirmar"
            >
              {decision.isPending
                ? t("comun.cargando")
                : t(aprobar ? "firma.confirmar_aprobar" : "firma.confirmar_cambios")}
            </button>
          </div>
        </>
      ) : (
        <>
          <p role="status" data-testid="detalle-ticket.firma.resultado">
            {resultado.accion === "approve"
              ? t("firma.firmado", { gate: String(gate) })
              : t("firma.devuelto", {
                  etapa: t(`st.${resultado.cambios.ticket.etapa}`),
                  iteracion: String(resultado.cambios.ticket.iteracion),
                })}
          </p>
          {resultado.accion === "approve" && (
            <>
              <p className="mono firma-sello" data-testid="detalle-ticket.firma.sello">
                {t("firma.sello")}: {resultado.firma.sello}
              </p>
              {resultado.firma.aviso_cuota && (
                <div
                  className="aviso-box"
                  role="status"
                  data-testid="detalle-ticket.firma.aviso-cuota"
                >
                  {resultado.firma.aviso_cuota}
                </div>
              )}
            </>
          )}
          <div className="modal-acciones">
            <button
              ref={cerrar}
              type="button"
              className="btn primary"
              onClick={alCerrar}
              data-testid="detalle-ticket.firma.cerrar"
            >
              {t("firma.cerrar")}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
