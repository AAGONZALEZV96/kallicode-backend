/**
 * Paso 5 del asistente (KC-PT-07-5): avance del grafo de código y encendido de la fábrica.
 *
 * - El grafo se muestra con su barra y métricas; mientras no llega al 100 % se vuelve a
 *   consultar solo (useEstadoGrafo).
 * - Encender la fábrica (POST /onboarding/complete) es solo del owner y pide confirmación:
 *   crea las líneas de producción del plan y queda en auditoría (fabrica_activada).
 * - El backend exige repositorio conectado y mapa de ramas (si no, 409
 *   ONBOARDING_INCOMPLETO): el portal lo sabe antes, así que el botón queda inactivo y
 *   lista los pasos que faltan, con un botón para ir a cada uno.
 */
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useEncenderFabrica } from "../../api/consultas";
import Modal from "../../componentes/Modal";
import { CajaError, fechaCorta } from "../../componentes/ui";
import { usePermiso } from "../../estado/auth";
import { useI18n } from "../../estado/i18n";
import { PASOS, pasosFaltantes, type DatosOnboarding } from "./pasos";

interface Props {
  datos: DatosOnboarding;
  irAPaso: (indice: number) => void;
}

export default function ActivacionPaso({ datos, irAPaso }: Props) {
  return (
    <div className="activacion-paso">
      <EstadoDelGrafo datos={datos} />
      <Fabrica datos={datos} irAPaso={irAPaso} />
    </div>
  );
}

function EstadoDelGrafo({ datos }: { datos: DatosOnboarding }) {
  const { t } = useI18n();
  const id = useId();
  const grafo = datos.grafo;
  // Sin datos y sin error (el error lo muestra la vista), aún se está consultando.
  if (!grafo) return null;

  const porcentaje = Math.round(grafo.progreso * 100);
  return (
    <div className="activacion-grafo">
      <p id={id} data-testid="onboarding.resumen.grafo">
        {t("asis.grafo", { porcentaje: String(porcentaje) })}
      </p>
      <div
        className="asistente-barra"
        role="progressbar"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={porcentaje}
        data-testid="onboarding.grafo.barra"
      >
        <div style={{ width: `${porcentaje}%` }} />
      </div>
      <p className="note-inline" data-testid="onboarding.grafo.detalle">
        {t("grafo.detalle", {
          fase: String(grafo.fase),
          definiciones: String(grafo.metricas.definiciones),
          documentos: String(grafo.metricas.documentos),
        })}
        {grafo.ultimo_refresh &&
          ` · ${t("grafo.actualizado", { fecha: fechaCorta(grafo.ultimo_refresh) })}`}
      </p>
      {porcentaje < 100 && <p className="note-inline">{t("grafo.procesando")}</p>}
      {grafo.curacion_pendiente > 0 && (
        <p className="note-inline">
          {t("grafo.curacion", { n: String(grafo.curacion_pendiente) })}
        </p>
      )}
    </div>
  );
}

function Fabrica({ datos, irAPaso }: Props) {
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const idFaltan = useId();
  const encender = useEncenderFabrica();
  const [confirmando, setConfirmando] = useState(false);
  const [lineas, setLineas] = useState<number | null>(null);
  const faltan = pasosFaltantes(datos);
  const bloqueado = faltan.length > 0;
  const tablero = useRef<HTMLAnchorElement>(null);

  // Al encender, el botón da paso al enlace al tablero: el foco va ahí y no se pierde.
  useEffect(() => {
    if (lineas !== null && datos.fabricaActiva) tablero.current?.focus();
  }, [lineas, datos.fabricaActiva]);

  function confirmar() {
    encender.mutate(undefined, {
      onSuccess: (r) => {
        setConfirmando(false);
        setLineas(r.lineas_activadas);
      },
    });
  }

  return (
    <div className="activacion-fabrica">
      <p data-testid="onboarding.resumen.fabrica">
        {datos.fabricaActiva ? t("asis.fabrica_activa") : t("asis.fabrica_inactiva")}
      </p>

      {/* Región viva siempre presente: anuncia el resultado al encender. */}
      <p
        role="status"
        className={lineas === null ? undefined : "res pass"}
        data-testid="onboarding.activacion.resultado"
      >
        {lineas !== null &&
          `✓ ${lineas === 1 ? t("activacion.encendida_una") : t("activacion.encendida", { n: String(lineas) })}`}
      </p>

      {datos.fabricaActiva ? (
        <Link to="/" ref={tablero} className="btn" data-testid="onboarding.activacion.tablero">
          {t("activacion.ir_tablero")}
        </Link>
      ) : !puedeHacer({ tipo: "encender-fabrica" }) ? (
        <p className="note-inline" data-testid="onboarding.activacion.solo-owner">
          {t("activacion.solo_owner")}
        </p>
      ) : (
        <>
          {bloqueado && (
            <div id={idFaltan} data-testid="onboarding.activacion.pendientes">
              <p>{t("activacion.faltan")}</p>
              <ul className="activacion-faltan">
                {faltan.map((p) => (
                  <li key={p}>
                    <button
                      type="button"
                      className="btn sm"
                      onClick={() => irAPaso(PASOS.indexOf(p))}
                      data-testid="onboarding.activacion.ir"
                    >
                      {t("activacion.ir_a", { paso: t(`asis.${p}.titulo`) })}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <button
            type="button"
            className="btn primary"
            // aria-disabled y no disabled: el botón conserva el foco y lee por qué no se puede.
            aria-disabled={bloqueado}
            aria-describedby={bloqueado ? idFaltan : undefined}
            aria-haspopup="dialog"
            onClick={() => {
              if (bloqueado) return;
              encender.reset();
              setConfirmando(true);
            }}
            data-testid="onboarding.activacion.encender"
          >
            {t("activacion.encender")}
          </button>
        </>
      )}

      {confirmando && (
        <Modal
          titulo={t("activacion.dialogo_titulo")}
          alCerrar={() => setConfirmando(false)}
          bloqueado={encender.isPending}
          testid="onboarding.encendido.dialogo"
        >
          <p>{t("activacion.aviso")}</p>
          <CajaError error={encender.error} />
          <div className="modal-acciones">
            <button
              type="button"
              className="btn"
              disabled={encender.isPending}
              onClick={() => setConfirmando(false)}
              data-testid="onboarding.encendido.cancelar"
            >
              {t("comun.cancelar")}
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={encender.isPending}
              onClick={confirmar}
              data-testid="onboarding.encendido.confirmar"
            >
              {encender.isPending ? t("activacion.encendiendo") : t("activacion.confirmar")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
