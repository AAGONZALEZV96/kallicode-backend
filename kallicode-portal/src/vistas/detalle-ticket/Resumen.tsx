/**
 * Resumen del ticket: etapa, línea de producción, radio de impacto del triage (KC-PT-06-3)
 * y enlace al PR. La ETA no se muestra: el backend todavía no la calcula.
 */
import { useId } from "react";

import type { RadioImpacto, TicketDetalle } from "../../api/tipos";
import { ChipEtapa } from "../../componentes/ui";
import { useI18n } from "../../estado/i18n";
import { sanearUrl } from "../../urls";

/** El radio usa los colores de la prioridad: alto en rojo, medio en ámbar, bajo en gris. */
const CLASE_RADIO = { high: "alta", medium: "media", low: "baja" } as const;

const etiqueta = { color: "var(--muted)" };

export default function Resumen({ ticket }: { ticket: TicketDetalle }) {
  const { t } = useI18n();
  const id = useId();
  // pr_url viene del repositorio del cliente: solo se enlaza si es http(s) (PT-F22).
  const prUrl = sanearUrl(ticket.detalle.pr_url);
  return (
    <div className="card">
      <p className="caption" id={id}>
        {t("td.detalle")}
      </p>
      <table className="kc" style={{ fontSize: 12.5, marginTop: 4 }} aria-labelledby={id}>
        <tbody>
          <tr>
            <td style={etiqueta}>{t("tk.col.etapa")}</td>
            <td>
              <ChipEtapa etapa={ticket.etapa} />
            </td>
          </tr>
          <tr>
            <td style={etiqueta}>{t("td.linea")}</td>
            <td>{ticket.detalle.linea ?? "—"}</td>
          </tr>
          <FilasImpacto impacto={ticket.detalle.radio_impacto} />
          <tr>
            <td style={etiqueta}>PR</td>
            <td>
              {prUrl ? (
                <a
                  href={prUrl}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={t("td.pr.abrir")}
                  data-testid="detalle-ticket.resumen.pr"
                >
                  ↗
                </a>
              ) : (
                "—"
              )}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/** Radio, riesgo de regresión y módulos afectados, o «pendiente» si aún no hay triage. */
function FilasImpacto({ impacto }: { impacto: RadioImpacto }) {
  const { t } = useI18n();
  if (!impacto) {
    return (
      <tr>
        <td style={etiqueta}>{t("imp.titulo")}</td>
        <td className="note-inline" data-testid="detalle-ticket.resumen.impacto-pendiente">
          {t("imp.pendiente")}
        </td>
      </tr>
    );
  }
  const { radio, riesgo_regresion: riesgo, modulos } = impacto;
  return (
    <>
      <tr>
        <td style={etiqueta}>{t("imp.radio")}</td>
        <td>
          {radio ? (
            <span
              className={`prio ${CLASE_RADIO[radio]}`}
              data-testid="detalle-ticket.resumen.impacto-radio"
            >
              {t(`imp.radio.${radio}`)}
            </span>
          ) : (
            "—"
          )}
        </td>
      </tr>
      <tr>
        <td style={etiqueta}>{t("imp.riesgo")}</td>
        <td>
          {riesgo != null ? (
            <span data-testid="detalle-ticket.resumen.impacto-riesgo">
              {Math.round(riesgo * 100)} %
            </span>
          ) : (
            "—"
          )}
        </td>
      </tr>
      <tr>
        <td style={etiqueta}>{t("imp.modulos")}</td>
        <td>
          {modulos.length > 0 ? (
            <ul className="impacto-modulos" data-testid="detalle-ticket.resumen.impacto-modulos">
              {modulos.map((m) => (
                <li key={m} className="chip mono">
                  {m}
                </li>
              ))}
            </ul>
          ) : (
            "—"
          )}
        </td>
      </tr>
    </>
  );
}
