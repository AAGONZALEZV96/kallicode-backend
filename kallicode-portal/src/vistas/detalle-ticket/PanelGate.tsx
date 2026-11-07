/**
 * Panel del gate pendiente (§10): se elige la alternativa (Gate 1) y se escribe el
 * comentario. Aprobar o pedir cambios no decide aquí: abre el modal de firma, que muestra
 * qué se firma antes de enviarlo (KC-PT-06-7).
 */
import { useId, useState } from "react";

import { usePestanaTicket } from "../../api/consultas";
import { useI18n } from "../../estado/i18n";
import type { SolicitudFirma } from "./ModalFirma";
import { MIN_COMENTARIO_CAMBIOS } from "./pestanas";

interface Props {
  numero: string;
  gate: number;
  alSolicitar: (solicitud: SolicitudFirma) => void;
}

export default function PanelGate({ numero, gate, alSolicitar }: Props) {
  const { t } = useI18n();
  const [comentario, setComentario] = useState("");
  // null = aún no elegida: se propone la recomendada de la spec.
  const [elegida, setElegida] = useState<string | null>(null);
  const idAlternativa = useId();
  const idComentario = useId();
  const idTitulo = useId();
  // Misma consulta que la pestaña Spec: no se pide dos veces.
  const { data: spec } = usePestanaTicket(numero, "spec", { habilitada: gate === 1 });
  const alts = gate === 1 ? (spec?.alternativas ?? []) : [];
  const conAlternativa = gate === 1 && alts.length > 1;
  const alternativa = elegida ?? alts.find((a) => a.recomendada)?.id ?? "";
  const elegidaAlt = alts.find((a) => a.id === alternativa);

  function aprobar() {
    alSolicitar({
      decision: {
        accion: "approve",
        cuerpo: {
          comentario: comentario || undefined,
          alternativa_id: conAlternativa ? alternativa : undefined,
        },
      },
      comentario,
      alternativa:
        conAlternativa && elegidaAlt ? `${elegidaAlt.id} · ${elegidaAlt.titulo}` : undefined,
    });
  }

  function pedirCambios() {
    alSolicitar({ decision: { accion: "request-changes", cuerpo: { comentario } }, comentario });
  }

  return (
    <section
      className="gate-panel"
      aria-labelledby={idTitulo}
      data-testid="detalle-ticket.gate.panel"
    >
      <h3 id={idTitulo}>{t(`gate.g${gate}`)}</h3>
      {conAlternativa && (
        <div style={{ marginBottom: 8 }}>
          <label htmlFor={idAlternativa}>{t("gate.alternativa")}</label>
          <select
            id={idAlternativa}
            value={alternativa}
            onChange={(e) => setElegida(e.target.value)}
            data-testid="detalle-ticket.gate.alternativa"
          >
            {alts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id} · {a.titulo}
              </option>
            ))}
          </select>
        </div>
      )}
      <label htmlFor={idComentario}>{t("gate.comentario")}</label>
      <textarea
        id={idComentario}
        rows={3}
        value={comentario}
        onChange={(e) => setComentario(e.target.value)}
        data-testid="detalle-ticket.gate.comentario"
      />
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          className="btn ok"
          style={{ flex: 1 }}
          aria-haspopup="dialog"
          onClick={aprobar}
          data-testid="detalle-ticket.gate.aprobar"
        >
          {t("gate.aprobar")}
        </button>
        <button
          type="button"
          className="btn warn"
          style={{ flex: 1 }}
          aria-haspopup="dialog"
          disabled={comentario.length < MIN_COMENTARIO_CAMBIOS}
          onClick={pedirCambios}
          data-testid="detalle-ticket.gate.pedir-cambios"
        >
          {t("gate.cambios")}
        </button>
      </div>
    </section>
  );
}
