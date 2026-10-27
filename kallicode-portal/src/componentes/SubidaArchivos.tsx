/**
 * Zona de adjuntos de un formulario (KC-PT-05-1): arrastrar y soltar o elegir archivos,
 * progreso por archivo y el motivo de cada rechazo (tipo, tamaño, cantidad o cuota de
 * almacenamiento del plan). El estado vive en useSubidaArchivos, que usa el formulario.
 */
import { useId, useRef, useState, type DragEvent } from "react";

import { ACEPTA_ADJUNTOS } from "../api/adjuntos";
import { useI18n } from "../estado/i18n";
import { sePuedeReintentar, type SubidaArchivos as Subida } from "../useSubidaArchivos";
import { MensajeError } from "./ui";

interface Props {
  subida: Subida;
  etiqueta: string;
  /** Texto de la zona (qué conviene adjuntar). */
  ayuda: string;
  /** Vista que la contiene: data-testid `<vista>.adjuntos.<elemento>`. */
  vista: string;
}

function tamanoLegible(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function SubidaArchivos({ subida, etiqueta, ayuda, vista }: Props) {
  const { t } = useI18n();
  const idEntrada = useId();
  const entrada = useRef<HTMLInputElement>(null);
  const [encima, setEncima] = useState(false);

  function soltar(e: DragEvent) {
    e.preventDefault();
    setEncima(false);
    subida.agregar(e.dataTransfer.files);
  }

  return (
    <div>
      <label htmlFor={idEntrada}>{etiqueta}</label>
      <div
        className={encima ? "subida encima" : "subida"}
        onDragOver={(e) => {
          e.preventDefault();
          setEncima(true);
        }}
        onDragLeave={() => setEncima(false)}
        onDrop={soltar}
        data-testid={`${vista}.adjuntos.zona`}
      >
        <p>{ayuda}</p>
        <button
          type="button"
          className="btn sm"
          onClick={() => entrada.current?.click()}
          data-testid={`${vista}.adjuntos.elegir`}
        >
          {t("adj.elegir")}
        </button>
        <input
          ref={entrada}
          id={idEntrada}
          type="file"
          multiple
          accept={ACEPTA_ADJUNTOS}
          hidden
          onChange={(e) => {
            if (e.target.files) subida.agregar(e.target.files);
            // Permite volver a elegir el mismo archivo después de quitarlo.
            e.target.value = "";
          }}
          data-testid={`${vista}.adjuntos.entrada`}
        />
        <p className="subida-info">{t("adj.limites")}</p>
      </div>

      {subida.adjuntos.length > 0 && (
        <ul className="subida-lista">
          {subida.adjuntos.map((a) => {
            const nombre = a.archivo.name;
            return (
              <li key={a.clave} data-testid={`${vista}.adjuntos.archivo`}>
                <span className="mono">{nombre}</span>
                <span className="subida-info">{tamanoLegible(a.archivo.size)}</span>
                {a.estado === "subiendo" && (
                  <progress
                    max={100}
                    value={Math.round(a.progreso * 100)}
                    aria-label={t("adj.progreso", { nombre })}
                    data-testid={`${vista}.adjuntos.progreso`}
                  />
                )}
                {a.estado === "lista" && <span className="res pass">✓ {t("adj.lista")}</span>}
                {a.estado === "error" && a.error && (
                  <span className="res fail" role="alert" data-testid={`${vista}.adjuntos.error`}>
                    <MensajeError error={a.error} />
                  </span>
                )}
                {sePuedeReintentar(a) && (
                  <button
                    type="button"
                    className="btn sm"
                    onClick={() => subida.reintentar(a.clave)}
                    data-testid={`${vista}.adjuntos.reintentar`}
                  >
                    {t("err.reintentar")}
                  </button>
                )}
                <button
                  type="button"
                  className="btn sm"
                  aria-label={t("adj.quitar", { nombre })}
                  onClick={() => subida.quitar(a.clave)}
                  data-testid={`${vista}.adjuntos.quitar`}
                >
                  ✕
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
