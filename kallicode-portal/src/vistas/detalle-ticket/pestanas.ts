/** Constantes del detalle de ticket que comparten sus componentes (KC-PT-06-7). */
import type { Pestana } from "../../api/consultas";

export const ETAPAS_PIPE = ["triage", "design", "build", "qa", "security", "deploy", "produccion"];

/** Gate que espera cada etapa (§10). */
export const GATE_DE_ETAPA: Record<string, number> = { design: 1, qa: 2, deploy: 3 };

export const PESTANAS: Pestana[] = ["spec", "evidence", "security", "usage", "activity"];

/** Zona data-testid de cada pestaña (detalle-ticket.pestanas.<zona>, detalle-ticket.<zona>.*). */
export const ZONA_PESTANA: Record<Pestana, string> = {
  spec: "spec",
  evidence: "evidencia",
  security: "seguridad",
  usage: "consumo",
  activity: "actividad",
};

/** Pedir cambios exige un comentario que oriente la nueva iteración (COMENTARIO_REQUERIDO). */
export const MIN_COMENTARIO_CAMBIOS = 10;

/** Etapas desde las que ya no se puede cancelar un ticket. */
export const ETAPAS_SIN_CANCELACION = ["deploy", "produccion", "cancelado"];
