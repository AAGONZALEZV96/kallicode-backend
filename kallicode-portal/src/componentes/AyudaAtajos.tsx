/**
 * Ayuda de los atajos de teclado (KC-PT-11-6): se abre con «?» o con el botón de la cabecera.
 * Lista los atajos que el rol puede usar y permite desactivar los de una tecla (WCAG 2.1.4).
 */
import { useId } from "react";

import { ATAJOS } from "../atajos";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";
import Modal from "./Modal";

export default function AyudaAtajos({
  activos,
  alCambiarActivos,
  alCerrar,
}: {
  activos: boolean;
  alCambiarActivos: (activos: boolean) => void;
  alCerrar: () => void;
}) {
  const { t } = useI18n();
  const { tieneAccesoARuta } = usePermiso();
  const id = useId();
  const visibles = ATAJOS.filter((a) => a.accion.tipo !== "ir" || tieneAccesoARuta(a.accion.ruta));

  return (
    <Modal titulo={t("atajos.titulo")} alCerrar={alCerrar} testid="layout.atajos.dialogo">
      <p className="note">{t("atajos.intro")}</p>
      <table className="kc atajos-tabla" data-testid="layout.atajos.lista">
        <tbody>
          {visibles.map((a) => (
            <tr key={a.teclas.join("+")}>
              <td className="atajos-teclas">
                {a.teclas.map((tecla, i) => (
                  <span key={i}>
                    {i > 0 && <span className="texto-tenue"> {t("atajos.luego")} </span>}
                    <kbd>{tecla}</kbd>
                  </span>
                ))}
              </td>
              <td>
                {a.accion.tipo === "ir"
                  ? t("atajos.ir_a", { destino: t(a.descripcion) })
                  : t(a.descripcion)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="atajos-preferencia">
        <input
          id={`${id}-activos`}
          type="checkbox"
          checked={activos}
          aria-describedby={`${id}-activos-ayuda`}
          onChange={(e) => alCambiarActivos(e.target.checked)}
          data-testid="layout.atajos.activar"
        />
        <label htmlFor={`${id}-activos`}>{t("atajos.activar")}</label>
      </div>
      <p className="note" id={`${id}-activos-ayuda`}>
        {t("atajos.activar_ayuda")}
      </p>
      <div className="modal-acciones">
        <button type="button" className="btn" onClick={alCerrar} data-testid="layout.atajos.cerrar">
          {t("atajos.cerrar")}
        </button>
      </div>
    </Modal>
  );
}
