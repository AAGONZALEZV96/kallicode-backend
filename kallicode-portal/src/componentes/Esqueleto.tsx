/**
 * Esqueletos de carga de las listas (KC-PT-09-4, cierra PT-F28): la silueta de lo que va a
 * aparecer (filas de una tabla, elementos de una lista, tarjetas), en vez de un «Cargando…»
 * suelto que luego empuja el contenido.
 *
 * - Para lectores de pantalla es el mismo indicador de siempre: role=status con el texto
 *   «Cargando…» y data-testid comun.carga.indicador. La silueta es decorativa (aria-hidden).
 * - Con prefers-reduced-motion no hay animación (tokens.css).
 * - Anchos variados y fijos por fila: parecen texto real y no cambian entre renders.
 * - Si una vista junta varias siluetas (KPIs y tabla), solo una anuncia la carga
 *   (`anunciar`): las demás son decorativas, para que haya un único indicador.
 */
import type { ReactNode } from "react";

import { useI18n } from "../estado/i18n";

const ANCHOS = ["72%", "54%", "86%", "63%", "45%", "78%"];
const ancho = (i: number) => ANCHOS[i % ANCHOS.length];

interface Comunes {
  /** false: solo la silueta, sin role=status (otra silueta de la vista ya anuncia la carga). */
  anunciar?: boolean;
}

function Indicador({
  children,
  className,
  anunciar = true,
}: Comunes & { children: ReactNode; className: string }) {
  const { t } = useI18n();
  if (!anunciar)
    return (
      <div className={`esqueleto ${className}`} aria-hidden="true">
        {children}
      </div>
    );
  return (
    <div
      className={`esqueleto ${className}`}
      role="status"
      aria-busy="true"
      data-testid="comun.carga.indicador"
    >
      <span className="solo-lector">{t("comun.cargando")}</span>
      <div aria-hidden="true">{children}</div>
    </div>
  );
}

const Barra = ({ w }: { w: string | undefined }) => (
  <span className="esqueleto-barra" style={{ width: w }} />
);

/** Silueta de una tabla con encabezado. */
export function EsqueletoTabla({
  columnas,
  filas = 6,
  anunciar,
}: Comunes & { columnas: number; filas?: number }) {
  return (
    <Indicador className="esqueleto-tabla card" anunciar={anunciar}>
      <div className="esqueleto-fila esqueleto-encabezado">
        {Array.from({ length: columnas }, (_, c) => (
          <Barra key={c} w="60%" />
        ))}
      </div>
      {Array.from({ length: filas }, (_, f) => (
        <div className="esqueleto-fila" key={f}>
          {Array.from({ length: columnas }, (_, c) => (
            <Barra key={c} w={ancho(f + c)} />
          ))}
        </div>
      ))}
    </Indicador>
  );
}

/** Silueta de una lista (notificaciones, actividad, conexiones). */
export function EsqueletoLista({ elementos = 4, anunciar }: Comunes & { elementos?: number }) {
  return (
    <Indicador className="esqueleto-lista" anunciar={anunciar}>
      {Array.from({ length: elementos }, (_, i) => (
        <div className="esqueleto-elemento" key={i}>
          <Barra w={ancho(i)} />
          <Barra w={ancho(i + 3)} />
        </div>
      ))}
    </Indicador>
  );
}

/** Silueta de tarjetas en grilla (KPIs, aprobaciones, tiers de modelos). */
export function EsqueletoTarjetas({ tarjetas = 3, anunciar }: Comunes & { tarjetas?: number }) {
  return (
    <Indicador className="esqueleto-tarjetas" anunciar={anunciar}>
      {Array.from({ length: tarjetas }, (_, i) => (
        <div className="card esqueleto-tarjeta" key={i}>
          <Barra w="40%" />
          <Barra w={ancho(i)} />
          <Barra w={ancho(i + 2)} />
        </div>
      ))}
    </Indicador>
  );
}
