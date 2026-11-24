/**
 * Contraseña nueva de las pantallas públicas (KC-PT-10-6): el campo con su rótulo y su error, y
 * la lista de requisitos de la política que se marca mientras se escribe. La lista está enlazada
 * al campo (aria-describedby), así un lector de pantalla la lee al llegar a él.
 */
import type { ReactNode } from "react";

import { atributosDeError } from "../api/erroresCampo";
import { useI18n } from "../estado/i18n";
import { REQUISITOS_CLAVE } from "../estado/politicaClave";

interface Props {
  id: string;
  etiqueta: string;
  valor: string;
  alCambiar: (valor: string) => void;
  error: string | undefined;
  testid: string;
  /** El <ErrorCampo> del campo, con id `${id}-error` (su testid se escribe en la vista). */
  children?: ReactNode;
  /** Id de la lista de requisitos que describe el campo (solo en el primero). */
  idRequisitos?: string;
  deshabilitado?: boolean;
}

export function CampoClave({
  id,
  etiqueta,
  valor,
  alCambiar,
  error,
  testid,
  children,
  idRequisitos,
  deshabilitado = false,
}: Props) {
  const idError = `${id}-error`;
  const atributos = atributosDeError(idError, error);
  const describe = [idRequisitos, atributos["aria-describedby"]].filter(Boolean).join(" ");
  return (
    <div>
      <label htmlFor={id}>{etiqueta}</label>
      <input
        id={id}
        type="password"
        autoComplete="new-password"
        value={valor}
        maxLength={128}
        disabled={deshabilitado}
        onChange={(e) => alCambiar(e.target.value)}
        aria-invalid={atributos["aria-invalid"]}
        aria-describedby={describe || undefined}
        data-testid={testid}
      />
      {children}
    </div>
  );
}

/** Requisitos de la política, marcados según lo escrito. */
export function RequisitosClave({
  id,
  valor,
  testid,
}: {
  id: string;
  valor: string;
  testid: string;
}) {
  const { t } = useI18n();
  return (
    <div className="requisitos-clave" id={id} data-testid={testid}>
      <p>{t("clave.requisitos")}</p>
      <ul>
        {REQUISITOS_CLAVE.map((r) => {
          const cumple = r.cumple(valor);
          return (
            <li key={r.clave} className={cumple ? "cumple" : undefined}>
              <span aria-hidden="true">{cumple ? "✓" : "○"}</span> {t(`clave.req.${r.clave}`)}
              <span className="solo-lector">
                {" "}
                ({t(cumple ? "clave.cumplido" : "clave.pendiente")})
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
