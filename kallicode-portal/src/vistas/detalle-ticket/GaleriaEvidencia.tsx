/**
 * Galería de evidencia QA (KC-PT-05-2): capturas, videos y trazas de Playwright de cada
 * criterio, con su enlace firmado de solo lectura. Las capturas se ven en miniatura, los
 * videos se reproducen en un diálogo y las trazas se descargan. Si una evidencia no trae
 * enlace, se lista como no disponible (hoy el backend no lo envía; llega con KC-BE-08-5).
 */
import { useId, useState, type MouseEvent } from "react";

import { vencido, type ArchivoEvidencia } from "../../api/enlacesEvidencia";
import type { Evidencia } from "../../api/tipos";
import Modal from "../../componentes/Modal";
import { useI18n } from "../../estado/i18n";

/** Los tipos que envía Testing (playwright_hooks.EvidenceType); el resto, «archivo». */
const TIPOS_CONOCIDOS = new Set(["screenshot", "video", "trace"]);

interface VideoAbierto {
  url: string;
  criterio: string;
}

interface Props {
  matriz: Evidencia["matriz"];
  /** Se hizo clic en un enlace vencido: hay que pedir la evidencia de nuevo. */
  alVencer: () => void;
}

export default function GaleriaEvidencia({ matriz, alVencer }: Props) {
  const { t } = useI18n();
  const idTitulo = useId();
  const [video, setVideo] = useState<VideoAbierto | null>(null);
  const conArchivos = matriz.filter((fila) => fila.evidencias.length > 0);

  return (
    <section
      className="evidencia-galeria"
      aria-labelledby={idTitulo}
      data-testid="detalle-ticket.evidencia.galeria"
    >
      <h3 id={idTitulo}>{t("td.ev.galeria")}</h3>
      {conArchivos.length === 0 ? (
        <p className="note" data-testid="detalle-ticket.evidencia.galeria-vacia">
          {t("td.ev.sin_archivos")}
        </p>
      ) : (
        conArchivos.map((fila) => (
          <div key={fila.criterio_id} className="evidencia-criterio">
            <h4>{fila.criterio_id}</h4>
            <ul className="evidencia-lista">
              {fila.evidencias.map((archivo, i) => (
                <Tarjeta
                  key={archivo.blob_ref ?? archivo.adjunto_id ?? `${archivo.tipo}-${i}`}
                  archivo={archivo}
                  criterio={fila.criterio_id}
                  alVerVideo={setVideo}
                  alVencer={alVencer}
                />
              ))}
            </ul>
          </div>
        ))
      )}

      {video && (
        <Modal
          titulo={t("td.ev.video_titulo", { criterio: video.criterio })}
          alCerrar={() => setVideo(null)}
          amplio
          testid="detalle-ticket.video.dialogo"
        >
          {/* Grabación de pantalla de Playwright, sin audio: no hay diálogo que subtitular.
              tabIndex: no todos los navegadores enfocan el <video> en sí; así recibe el foco
              al abrir y responde al teclado (espacio para reproducir o pausar). */}
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <video
            className="evidencia-video"
            src={video.url}
            controls
            tabIndex={0}
            preload="metadata"
            data-testid="detalle-ticket.video.reproductor"
          />
          <div className="modal-acciones">
            <a
              className="btn"
              href={video.url}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="detalle-ticket.video.descargar"
            >
              {t("td.ev.descargar")}
            </a>
            <button
              type="button"
              className="btn primary"
              onClick={() => setVideo(null)}
              data-testid="detalle-ticket.video.cerrar"
            >
              {t("td.ev.cerrar")}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}

interface PropsTarjeta {
  archivo: ArchivoEvidencia;
  criterio: string;
  alVerVideo: (video: VideoAbierto) => void;
  alVencer: () => void;
}

function Tarjeta({ archivo, criterio, alVerVideo, alVencer }: PropsTarjeta) {
  const { t } = useI18n();
  const tipo = TIPOS_CONOCIDOS.has(archivo.tipo) ? archivo.tipo : "otro";
  const nombre = t(`td.ev.tipo.${tipo}`);
  const url = archivo.download_url;

  /** Un enlace vencido abriría el error del almacenamiento: se pide uno nuevo. */
  function vencioAlUsar(e: MouseEvent) {
    if (!vencido(archivo.expira_en)) return false;
    e.preventDefault();
    alVencer();
    return true;
  }

  return (
    <li className="evidencia" data-testid="detalle-ticket.evidencia.item">
      <span className="evidencia-tipo">{nombre}</span>
      {!url ? (
        <span className="note" data-testid="detalle-ticket.evidencia.no-disponible">
          {t("td.ev.no_disponible")}
        </span>
      ) : (
        <>
          {tipo === "screenshot" && (
            <a
              className="evidencia-miniatura"
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={vencioAlUsar}
              data-testid="detalle-ticket.evidencia.miniatura"
            >
              <img src={url} alt={t("td.ev.alt_captura", { criterio })} loading="lazy" />
            </a>
          )}
          <div className="evidencia-acciones">
            {tipo === "video" && (
              <button
                type="button"
                className="btn sm"
                aria-label={t("td.ev.ver_video_de", { criterio })}
                onClick={(e) => {
                  if (!vencioAlUsar(e)) alVerVideo({ url, criterio });
                }}
                data-testid="detalle-ticket.evidencia.ver-video"
              >
                {t("td.ev.ver_video")}
              </button>
            )}
            <a
              className="btn sm"
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={t("td.ev.descargar_de", { tipo: nombre, criterio })}
              onClick={vencioAlUsar}
              data-testid="detalle-ticket.evidencia.descargar"
            >
              {t("td.ev.descargar")}
            </a>
          </div>
          {tipo === "trace" && <span className="note">{t("td.ev.traza_ayuda")}</span>}
        </>
      )}
    </li>
  );
}
