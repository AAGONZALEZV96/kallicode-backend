/**
 * Patrón tablist de WAI-ARIA (KC-PT-05-4, cierra PT-F12): Tab entra a la pestaña activa y
 * sale al panel; ←/→ pasan a la anterior o la siguiente (circular) e Inicio/Fin a los
 * extremos. Cambiar el foco activa la pestaña: cargan bajo demanda y son livianas.
 */
import { useRef, type KeyboardEvent } from "react";

import type { Pestana } from "../../api/consultas";
import { useI18n } from "../../estado/i18n";
import { PESTANAS, ZONA_PESTANA } from "./pestanas";

interface Props {
  activa: Pestana;
  alCambiar: (p: Pestana) => void;
  idBase: string;
}

export default function ListaPestanas({ activa, alCambiar, idBase }: Props) {
  const { t } = useI18n();
  const botones = useRef<(HTMLButtonElement | null)[]>([]);

  function teclado(e: KeyboardEvent, i: number) {
    const ultimo = PESTANAS.length - 1;
    const destino = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: ultimo }[e.key];
    if (destino === undefined) return;
    e.preventDefault();
    const j = (destino + PESTANAS.length) % PESTANAS.length;
    alCambiar(PESTANAS[j]!);
    botones.current[j]?.focus();
  }

  return (
    <div className="tabs" role="tablist" aria-label={t("td.pestanas")}>
      {PESTANAS.map((p, i) => (
        <button
          key={p}
          ref={(el) => {
            botones.current[i] = el;
          }}
          type="button"
          role="tab"
          id={`${idBase}-tab-${p}`}
          aria-selected={activa === p}
          aria-controls={`${idBase}-panel`}
          tabIndex={activa === p ? 0 : -1}
          className={activa === p ? "active" : ""}
          onClick={() => alCambiar(p)}
          onKeyDown={(e) => teclado(e, i)}
          data-testid={`detalle-ticket.pestanas.${ZONA_PESTANA[p]}`}
        >
          {t(`td.tab.${p}`)}
        </button>
      ))}
    </div>
  );
}
