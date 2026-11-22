/**
 * Verificador de integridad de los sellos del tramo de un ticket (KC-PT-08-4, sobre KC-BE-08-2).
 *
 * - Muestra la verificación que trae el expediente: estado, eventos verificados, el rango del
 *   tramo (primer y último evento del ticket) y el sello final.
 * - Deja claro el alcance: solo los eventos del ticket, no la cadena completa del tenant.
 * - Si hay un sello alterado, señala el primero (el backend informa el menor id): los eventos
 *   anteriores quedan confirmados y los posteriores, sin confirmar.
 * - Quien tiene permiso (owner, admin, architect) puede volver a verificar a pedido
 *   (GET /audit/integrity); el resultado se anuncia en una región viva.
 */
import type { UseMutationResult } from "@tanstack/react-query";
import { useId, useState } from "react";

import { abreviarSello } from "../../api/integridad";
import type { Expediente, Integridad } from "../../api/tipos";
import { CajaError } from "../../componentes/ui";
import { useFormato } from "../../estado/formato";
import { useI18n } from "../../estado/i18n";

interface Props {
  integridad: Expediente["integridad"];
  /** Eventos del expediente, para nombrar el primero alterado (si el backend los envía). */
  eventos: Expediente["eventos"];
  /** La verificación a pedido (compartida con la lista de eventos); null si el rol no puede. */
  verificar: UseMutationResult<Integridad, Error, void> | null;
}

export default function VerificadorIntegridad({ integridad, eventos, verificar }: Props) {
  const fmt = useFormato();
  const { t } = useI18n();
  const id = useId();
  const [verificadoEn, setVerificadoEn] = useState<Date | null>(null);
  // La verificación a pedido reemplaza a la del expediente: es más reciente.
  const vigente: Integridad = verificar?.data ?? integridad;
  const sinEventos = vigente.eventos_verificados === 0;
  const roto = vigente.integra ? null : vigente.primer_evento_roto;
  const eventoRoto = roto === null ? undefined : eventos?.find((e) => e.id === roto);
  const enCurso = verificar?.isPending ?? false;

  return (
    <div className="integridad">
      <p className="caption">{t("au.integridad")}</p>
      <p className="integridad-estado">
        <span
          className={sinEventos ? "res" : `res ${vigente.integra ? "pass" : "fail"}`}
          data-testid="expediente.integridad.estado"
        >
          {sinEventos
            ? t("au.int.sin_eventos")
            : vigente.integra
              ? `✓ ${t("au.integra")}`
              : `✗ ${t("au.rota")}`}
        </span>
        {integridad.hash_raiz && (
          <span className="mono" style={{ fontSize: 11 }} title={integridad.hash_raiz}>
            {t("au.int.sello_final")}: {abreviarSello(integridad.hash_raiz)}
          </span>
        )}
      </p>
      <p className="note-inline" data-testid="expediente.integridad.tramo">
        {!sinEventos &&
          `${t("au.int.tramo", {
            n: String(vigente.eventos_verificados),
            desde: fmt.fecha(integridad.desde),
            hasta: fmt.fecha(integridad.hasta),
          })} `}
        {t("au.int.alcance")}
      </p>
      {roto !== null && (
        <p className="integridad-roto" data-testid="expediente.integridad.roto">
          {t("au.int.primer_roto", { id: String(roto) })}
          {eventoRoto && <> — «{eventoRoto.resumen}»</>}. {t("au.int.posteriores")}
        </p>
      )}
      {verificar && (
        <div className="integridad-acciones">
          <button
            type="button"
            className="btn sm"
            // aria-disabled y no disabled: el botón conserva el foco mientras verifica.
            aria-disabled={enCurso}
            aria-describedby={`${id}-resultado`}
            onClick={() => {
              if (enCurso) return;
              verificar.mutate(undefined, { onSuccess: () => setVerificadoEn(new Date()) });
            }}
            data-testid="expediente.integridad.verificar"
          >
            {t("au.int.verificar")}
          </button>
          {/* Región viva siempre presente: anuncia el resultado de cada verificación. */}
          <p
            id={`${id}-resultado`}
            role="status"
            className="note-inline integridad-resultado"
            data-testid="expediente.integridad.resultado"
          >
            {enCurso && t("au.int.verificando")}
            {!enCurso &&
              verificar.data &&
              verificadoEn &&
              t(verificar.data.integra ? "au.int.resultado_ok" : "au.int.resultado_roto", {
                hora: fmt.horaConSegundos(verificadoEn),
                n: String(verificar.data.eventos_verificados),
              })}
          </p>
        </div>
      )}
      <CajaError error={verificar?.error ?? null} />
    </div>
  );
}
