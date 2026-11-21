/**
 * Exportación de auditoría (KC-PT-08-3): pide el archivo a POST /audit/exports, sondea su
 * estado (encolada → procesando → lista | fallida) y ofrece el enlace de descarga firmado.
 *
 * - CSV: los eventos del ticket o los del explorador con sus filtros (sin el límite de
 *   92 días). PDF: el expediente completo del ticket.
 * - Mientras se prepara, los botones quedan con aria-disabled (conservan el foco) y una región
 *   viva anuncia el estado; al terminar, el enlace aparece en esa misma región.
 * - El enlace sale del backend: pasa por sanearUrl antes de ir a un href (PT-F22).
 */
import { useId, useState } from "react";

import { useCrearExportacion, useEstadoExportacion } from "../api/consultas";
import type { ExportacionIn } from "../api/tipos";
import { useI18n } from "../estado/i18n";
import { sanearUrl } from "../urls";
import { CajaError, fechaCorta } from "./ui";

type Formato = ExportacionIn["formato"] & ("csv" | "pdf");

interface Props {
  /** Vista donde vive: da el prefijo de los data-testid. */
  vista: "auditoria" | "expediente";
  formatos: readonly Formato[];
  /** Ticket (número) y filtros del pedido, sin el formato. */
  pedido: Omit<ExportacionIn, "formato">;
  /** Por qué no se puede exportar ahora (p. ej. un rango de fechas invertido). */
  bloqueo?: string;
  /** Qué incluye la exportación. */
  ayuda: string;
}

export default function Exportacion({ vista, formatos, pedido, bloqueo, ayuda }: Props) {
  const { t } = useI18n();
  const id = useId();
  const crear = useCrearExportacion();
  const [actual, setActual] = useState<{ id: string; formato: Formato } | null>(null);
  const consulta = useEstadoExportacion(actual?.id ?? null);
  // Recién creada y sin la primera consulta todavía: está encolada.
  const estado = consulta.data?.estado ?? (actual ? "encolada" : null);
  const enCurso = crear.isPending || estado === "encolada" || estado === "procesando";
  const deshabilitada = enCurso || Boolean(bloqueo);
  const enlace = estado === "lista" ? sanearUrl(consulta.data?.download_url) : null;

  function exportar(formato: Formato) {
    if (deshabilitada) return;
    crear.mutate(
      { ...pedido, formato },
      { onSuccess: (r) => setActual({ id: r.export_id, formato }) },
    );
  }

  return (
    <div className="exportacion">
      <div className="exportacion-acciones">
        {formatos.map((formato) => (
          <button
            key={formato}
            type="button"
            className="btn sm"
            // aria-disabled y no disabled: el botón conserva el foco mientras se prepara.
            aria-disabled={deshabilitada}
            aria-describedby={`${id}-ayuda`}
            onClick={() => exportar(formato)}
            data-testid={`${vista}.exportacion.${formato}`}
          >
            {t(`au.exp.${formato}`)}
          </button>
        ))}
      </div>
      <p id={`${id}-ayuda`} className="note-inline">
        {bloqueo ?? ayuda}
      </p>
      {/* Región viva siempre presente: anuncia cada cambio de estado. */}
      <p role="status" className="exportacion-estado" data-testid={`${vista}.exportacion.estado`}>
        {enCurso && t("au.exp.preparando")}
        {estado === "fallida" && t("au.exp.fallida")}
        {estado === "lista" && actual && (
          <>
            {t("au.exp.lista")}{" "}
            {enlace ? (
              <a
                href={enlace}
                target="_blank"
                rel="noopener noreferrer"
                data-testid={`${vista}.exportacion.descargar`}
              >
                {t("au.exp.descargar", { formato: actual.formato.toUpperCase() })}
              </a>
            ) : (
              t("au.exp.sin_enlace")
            )}
            {enlace && consulta.data?.expira_en && (
              <span className="note-inline">
                {" "}
                {t("au.exp.vence", { fecha: fechaCorta(consulta.data.expira_en) })}
              </span>
            )}
          </>
        )}
      </p>
      <CajaError error={crear.error ?? consulta.error} />
    </div>
  );
}
