/** Criterios Dado/Cuando/Entonces de Nueva funcionalidad (KC-PT-06-5, KC-PT-06-6). */
import type { CriterioGWT } from "../../api/tipos";

export const PARTES = ["given", "when", "then"] as const;

/** Un criterio sin nada escrito no se envía; uno a medias se marca para completarlo. */
export const enBlanco = (c: CriterioGWT) => PARTES.every((p) => !c[p].trim());
