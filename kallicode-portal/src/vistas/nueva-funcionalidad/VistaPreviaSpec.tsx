/**
 * Vista previa de lo que recibe el arquitecto IA (KC-PT-06-6). tickets.crear_feature arma
 * la descripción con el objetivo y una línea «Dado …, cuando …, entonces …» por criterio
 * enviado; aquí se ve igual mientras se escribe. Los criterios en blanco no se envían y
 * a uno a medias se le marca lo que falta (el formulario no deja enviarlo así).
 */
import { useId } from "react";

import type { CriterioGWT } from "../../api/tipos";
import { useI18n } from "../../estado/i18n";
import { enBlanco, PARTES } from "./criterios";

const PALABRA = { given: "gwt.dado", when: "gwt.cuando", then: "gwt.entonces" } as const;

interface Props {
  nombre: string;
  objetivo: string;
  criterios: CriterioGWT[];
}

export default function VistaPreviaSpec({ nombre, objetivo, criterios }: Props) {
  const { t } = useI18n();
  const id = useId();
  const enviados = criterios.filter((c) => !enBlanco(c));
  const vacia = !nombre.trim() && !objetivo.trim() && enviados.length === 0;
  const falta = <span className="vp-falta">{t("nf.vp.falta")}</span>;

  return (
    <section
      className="card"
      aria-labelledby={id}
      data-testid="nueva-funcionalidad.vista-previa.panel"
    >
      <p className="caption" id={id}>
        {t("nf.vp.titulo")}
      </p>
      {vacia ? (
        <p className="note" data-testid="nueva-funcionalidad.vista-previa.vacio">
          {t("nf.vp.vacio")}
        </p>
      ) : (
        <div className="vista-previa">
          <p className="vp-nombre" data-testid="nueva-funcionalidad.vista-previa.nombre">
            {nombre.trim() || falta}
          </p>
          <p data-testid="nueva-funcionalidad.vista-previa.objetivo">{objetivo.trim() || falta}</p>
          {enviados.length > 0 ? (
            <>
              <p className="vp-subtitulo">{t("nf.vp.criterios")}</p>
              <ol className="vp-criterios">
                {enviados.map((c, i) => (
                  <li key={i} data-testid="nueva-funcionalidad.vista-previa.criterio">
                    {PARTES.map((p, j) => (
                      <span key={p}>
                        {j > 0 && ", "}
                        <b>{t(PALABRA[p])}</b> {c[p].trim() || falta}
                      </span>
                    ))}
                  </li>
                ))}
              </ol>
            </>
          ) : (
            <p className="ayuda-campo">{t("nf.vp.sin_criterios")}</p>
          )}
        </div>
      )}
    </section>
  );
}
