/**
 * Panel de inspección de un paso LLM del expediente (KC-PT-08-2, sobre KC-BE-08-4).
 *
 * - Plantilla y versión, modelo y tier, y la ejecución: confianza, validación, reintentos,
 *   tokens de entrada y salida, y duración (GET /audit/steps/{step_id}).
 * - Entrada y salida del modelo: se leen de sus URLs firmadas de Blob y se muestran como JSON
 *   con sangría. Si el navegador no puede leerlas (CORS del almacenamiento, URL vencida), lo
 *   dice; el enlace para abrirlas en otra pestaña está siempre.
 * - Las URLs vencen a los 10 minutos: cada vez que se abre el panel se piden otras.
 */
import { useId } from "react";

import { useDetallePaso, useTrazaPaso } from "../../api/consultas";
import type { PasoLLM } from "../../api/tipos";
import { ErrorTraza, type TipoTraza } from "../../api/trazas";
import Modal from "../../componentes/Modal";
import { CajaError, Cargando } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import { sanearUrl } from "../../urls";

interface Props {
  paso: PasoLLM;
  alCerrar: () => void;
}

const hora = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

export default function PanelPaso({ paso, alCerrar }: Props) {
  const { t } = useI18n();
  const { data: detalle, error } = useDetallePaso(paso.step_id);

  return (
    <Modal
      titulo={t("au.paso.titulo", { paso: paso.paso })}
      alCerrar={alCerrar}
      amplio
      testid="expediente.paso.panel"
    >
      {error ? (
        <CajaError error={error} />
      ) : !detalle ? (
        <Cargando />
      ) : (
        <>
          <dl className="firma-resumen" data-testid="expediente.paso.datos">
            <dt>{t("au.paso.etapa")}</dt>
            <dd>{t(`st.${detalle.paso.etapa}`)}</dd>
            <dt>{t("au.paso.plantilla")}</dt>
            <dd className="mono">
              {detalle.paso.plantilla} · v{detalle.paso.version_plantilla}
            </dd>
            <dt>{t("au.paso.modelo")}</dt>
            <dd className="mono">
              {detalle.paso.modelo} · {detalle.paso.tier}
            </dd>
            <dt>{t("au.paso.confianza")}</dt>
            <dd>{detalle.ejecucion.confianza ?? "—"}</dd>
            <dt>{t("au.paso.validacion")}</dt>
            <dd className="mono">{detalle.ejecucion.validacion}</dd>
            <dt>{t("au.paso.reintentos")}</dt>
            <dd>{detalle.ejecucion.reintentos}</dd>
            <dt>{t("au.paso.tokens")}</dt>
            <dd>
              {t("au.paso.tokens_detalle", {
                entrada: detalle.ejecucion.tokens_in.toLocaleString(),
                salida: detalle.ejecucion.tokens_out.toLocaleString(),
                total: (
                  detalle.ejecucion.tokens_in + detalle.ejecucion.tokens_out
                ).toLocaleString(),
              })}
            </dd>
            <dt>{t("au.paso.duracion")}</dt>
            <dd>{detalle.ejecucion.duracion_ms.toLocaleString()} ms</dd>
          </dl>
          <Traza stepId={paso.step_id} tipo="entrada" url={detalle.entrada_url} />
          <Traza stepId={paso.step_id} tipo="salida" url={detalle.salida_url} />
          {detalle.urls_expiran_en && (
            <p className="note-inline">
              {t("au.paso.vencen", { hora: hora(detalle.urls_expiran_en) })}
            </p>
          )}
        </>
      )}
      <div className="modal-acciones">
        <button
          type="button"
          className="btn"
          onClick={alCerrar}
          data-testid="expediente.paso.cerrar"
        >
          {t("au.paso.cerrar")}
        </button>
      </div>
    </Modal>
  );
}

function Traza({ stepId, tipo, url }: { stepId: string; tipo: TipoTraza; url: string | null }) {
  const { t } = useI18n();
  const id = useId();
  const enlace = sanearUrl(url);
  const traza = useTrazaPaso(stepId, tipo, enlace);
  const vencida = traza.error instanceof ErrorTraza && traza.error.estado === 403;

  return (
    <section className="traza" aria-labelledby={`${id}-titulo`}>
      <h3 id={`${id}-titulo`}>{t(`au.paso.${tipo}`)}</h3>
      <div data-testid={`expediente.paso.${tipo}`}>
        {!enlace ? (
          <p className="note-inline">{t("au.paso.sin_traza")}</p>
        ) : traza.data ? (
          <>
            <pre
              className="traza-contenido"
              // Región desplazable: con tabIndex se recorre con el teclado (WCAG 2.1.1).
              // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
              tabIndex={0}
              role="region"
              aria-labelledby={`${id}-titulo`}
            >
              {traza.data.texto}
            </pre>
            {traza.data.truncada && <p className="note-inline">{t("au.paso.truncada")}</p>}
          </>
        ) : traza.error ? (
          <p className="note-inline">{t(vencida ? "au.paso.vencida" : "au.paso.no_legible")}</p>
        ) : (
          <p className="note-inline">{t("au.paso.leyendo")}</p>
        )}
      </div>
      {enlace && (
        <a
          href={enlace}
          target="_blank"
          rel="noopener noreferrer"
          data-testid={`expediente.paso.abrir-${tipo}`}
        >
          {t(`au.paso.abrir_${tipo}`)} ↗
        </a>
      )}
    </section>
  );
}
