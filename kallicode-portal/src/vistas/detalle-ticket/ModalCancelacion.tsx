/**
 * Modal de cancelación del ticket (KC-PT-06-5, cierra PT-F18). Reemplaza al prompt() del
 * navegador: avisa que no se puede deshacer, pide el motivo con la regla del backend
 * (motivo_cancelacion, 5 a 500 caracteres) y muestra su error junto al campo. Los demás
 * errores (CANCELACION_TARDIA, PERMISO_DENEGADO) quedan en el diálogo.
 */
import { useId, useState, type FormEvent } from "react";

import { useCancelarTicket } from "../../api/consultas";
import {
  atributosDeError,
  errorDeTexto,
  erroresDelServidor,
  esErrorDeValidacion,
} from "../../api/erroresCampo";
import { ErrorCampo } from "../../componentes/ErrorCampo";
import Modal from "../../componentes/Modal";
import { CajaError } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";

const REGLA_MOTIVO = { min: 5, max: 500 };

interface Props {
  numero: string;
  alCerrar: () => void;
  alCancelar: () => void;
}

export default function ModalCancelacion({ numero, alCerrar, alCancelar }: Props) {
  const { t } = useI18n();
  const id = useId();
  const cancelacion = useCancelarTicket(numero);
  const [motivo, setMotivo] = useState("");
  const [intentado, setIntentado] = useState(false);

  // Un 422 sin campos (motivo ausente en tickets.actualizar) también es del motivo.
  const servidor = erroresDelServidor(
    t,
    cancelacion.error,
    (campo) => (campo === "motivo_cancelacion" ? "motivo" : null),
    "motivo",
  );
  const error =
    (intentado ? errorDeTexto(t, motivo, REGLA_MOTIVO) : undefined) ?? servidor.porCampo.motivo;
  const idMotivo = `${id}-motivo`;
  const idAyuda = `${idMotivo}-ayuda`;
  const idError = `${idMotivo}-error`;
  const errorDeValidacion = esErrorDeValidacion(cancelacion.error);

  function confirmar(e: FormEvent) {
    e.preventDefault();
    setIntentado(true);
    const enfocarMotivo = () => document.getElementById(idMotivo)?.focus();
    if (errorDeTexto(t, motivo, REGLA_MOTIVO)) {
      enfocarMotivo();
      return;
    }
    cancelacion.mutate(motivo.trim(), {
      onSuccess: alCancelar,
      onError: (err) => {
        if (esErrorDeValidacion(err)) enfocarMotivo();
      },
    });
  }

  const atributos = atributosDeError(idError, error);

  return (
    <Modal
      titulo={t("cancelacion.titulo", { numero })}
      alCerrar={alCerrar}
      bloqueado={cancelacion.isPending}
      testid="detalle-ticket.cancelacion.dialogo"
    >
      <form className="modal-form" onSubmit={confirmar} noValidate>
        <p className="note">{t("cancelacion.aviso")}</p>
        <div>
          <label htmlFor={idMotivo}>{t("cancelacion.motivo")}</label>
          <textarea
            id={idMotivo}
            rows={3}
            value={motivo}
            required
            minLength={REGLA_MOTIVO.min}
            maxLength={REGLA_MOTIVO.max}
            onChange={(e) => {
              if (errorDeValidacion) cancelacion.reset();
              setMotivo(e.target.value);
            }}
            aria-invalid={atributos["aria-invalid"]}
            // La ayuda siempre; el error, cuando lo hay.
            aria-describedby={[idAyuda, atributos["aria-describedby"]].filter(Boolean).join(" ")}
            data-testid="detalle-ticket.cancelacion.motivo"
          />
          <p id={idAyuda} className="ayuda-campo">
            {t("cancelacion.ayuda")}
          </p>
          <ErrorCampo id={idError} error={error} testid="detalle-ticket.cancelacion.error" />
        </div>
        {!errorDeValidacion && <CajaError error={cancelacion.error} />}
        <div className="modal-acciones">
          <button
            type="button"
            className="btn"
            disabled={cancelacion.isPending}
            onClick={alCerrar}
            data-testid="detalle-ticket.cancelacion.volver"
          >
            {t("cancelacion.volver")}
          </button>
          <button
            type="submit"
            className="btn warn"
            disabled={cancelacion.isPending}
            data-testid="detalle-ticket.cancelacion.confirmar"
          >
            {cancelacion.isPending ? t("comun.cargando") : t("cancelacion.confirmar")}
          </button>
        </div>
      </form>
    </Modal>
  );
}
