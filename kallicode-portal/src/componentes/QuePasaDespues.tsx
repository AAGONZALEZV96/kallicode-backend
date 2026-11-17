/**
 * «¿Qué pasa después de enviar?» (KC-PT-06-6): los pasos del pipeline que sigue un ticket
 * nuevo, con los tres gates donde una persona firma. Sigue el orquestador: triage (un
 * duplicado se cierra ahí), diseño, Gate 1, build, QA (si falla vuelve a build), Gate 2,
 * seguridad (un hallazgo grave vuelve a build), Gate 3 y producción. Los roles de cada
 * gate son los de kallicode_core.seguridad.
 */
import { useId } from "react";

import { useI18n } from "../estado/i18n";

interface Paso {
  id: string;
  titulo: string;
  texto: string;
  firma?: boolean;
}

function pasos(tipo: "ticket" | "funcionalidad"): Paso[] {
  return [
    { id: "triage", titulo: "st.triage", texto: "qpd.triage" },
    {
      id: "design",
      titulo: "st.design",
      texto: tipo === "funcionalidad" ? "qpd.design_funcionalidad" : "qpd.design",
    },
    { id: "gate1", titulo: "gate.g1", texto: "qpd.gate1", firma: true },
    { id: "build", titulo: "st.build", texto: "qpd.build" },
    { id: "qa", titulo: "st.qa", texto: "qpd.qa" },
    { id: "gate2", titulo: "gate.g2", texto: "qpd.gate2", firma: true },
    { id: "security", titulo: "st.security", texto: "qpd.security" },
    { id: "gate3", titulo: "gate.g3", texto: "qpd.gate3", firma: true },
    { id: "produccion", titulo: "st.produccion", texto: "qpd.produccion" },
  ];
}

interface Props {
  /** Vista que lo contiene: data-testid `<vista>.que-pasa.<elemento>`. */
  vista: string;
  tipo: "ticket" | "funcionalidad";
}

export default function QuePasaDespues({ vista, tipo }: Props) {
  const { t } = useI18n();
  const id = useId();
  return (
    <section className="card" aria-labelledby={id} data-testid={`${vista}.que-pasa.panel`}>
      <p className="caption" id={id}>
        {t("qpd.titulo")}
      </p>
      <p className="que-pasa-intro">{t("qpd.intro")}</p>
      <ol className="que-pasa">
        {pasos(tipo).map((p) => (
          <li
            key={p.id}
            className={p.firma ? "firma" : undefined}
            data-testid={`${vista}.que-pasa.paso`}
          >
            <b>{t(p.titulo)}</b>
            {p.firma && <span className="que-pasa-firma">{t("qpd.firma")}</span>}
            <span className="que-pasa-texto">{t(p.texto)}</span>
          </li>
        ))}
      </ol>
      <p className="ayuda-campo">{t("qpd.cuota")}</p>
      <p className="ayuda-campo">{t("qpd.seguimiento")}</p>
    </section>
  );
}
