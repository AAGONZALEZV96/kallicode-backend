/**
 * Cadena de firmas de los gates (KC-PT-06-4, cierra PT-F10): los tres gates en orden,
 * con quién firmó, cuándo, su comentario y el sello con que la firma quedó enlazada en la
 * cadena de auditoría. El sello no cambia después de firmar; la verificación de toda la
 * cadena de hashes está en el expediente, que se enlaza a quien puede verlo.
 */
import { useId } from "react";
import { Link } from "react-router-dom";

import { useCadenaFirmas } from "../../api/consultas";
import type { EslabonFirma } from "../../api/tipos";
import { CajaError, Cargando, fechaCorta } from "../../componentes/ui";
import { usePermiso } from "../../estado/auth";
import { useI18n } from "../../estado/i18n";

const MARCA: Record<EslabonFirma["estado"], string> = {
  firmado: "✓",
  pendiente: "⛨",
  no_alcanzado: "○",
};

export default function CadenaFirmas({ numero }: { numero: string }) {
  const { t } = useI18n();
  const { tieneAccesoARuta } = usePermiso();
  const id = useId();
  const { data: cadena, error, isPending: cargando } = useCadenaFirmas(numero);
  const hayFirmas = cadena?.gates.some((g) => g.estado === "firmado") ?? false;

  return (
    <div className="card" data-testid="detalle-ticket.firmas.cadena">
      <p className="caption" id={id}>
        {t("td.firmas.titulo")}
      </p>
      {cargando && <Cargando />}
      {error && <CajaError error={error} />}
      {cadena && (
        <ol className="cadena-firmas" aria-labelledby={id}>
          {cadena.gates.map((eslabon) => (
            <Eslabon key={eslabon.gate} eslabon={eslabon} />
          ))}
        </ol>
      )}
      {hayFirmas && tieneAccesoARuta("/auditoria/:numero") && (
        <Link
          className="cadena-expediente"
          to={`/auditoria/${numero}`}
          data-testid="detalle-ticket.firmas.expediente"
        >
          {t("td.firmas.verificar")}
        </Link>
      )}
    </div>
  );
}

function Eslabon({ eslabon }: { eslabon: EslabonFirma }) {
  const { t } = useI18n();
  return (
    <li
      className={`eslabon ${eslabon.estado}`}
      aria-current={eslabon.estado === "pendiente" ? "step" : undefined}
      data-testid="detalle-ticket.firmas.gate"
    >
      <span className="eslabon-marca" aria-hidden="true">
        {MARCA[eslabon.estado]}
      </span>
      <div className="eslabon-cuerpo">
        <b>{t(`gate.g${eslabon.gate}`)}</b>
        <span className="eslabon-estado" data-testid="detalle-ticket.firmas.estado">
          {t(`td.firmas.${eslabon.estado}`)}
        </span>
        {eslabon.estado === "firmado" && (
          <>
            <span>
              {eslabon.actor} · <time dateTime={eslabon.fecha}>{fechaCorta(eslabon.fecha)}</time>
            </span>
            {eslabon.comentario && <q className="eslabon-comentario">{eslabon.comentario}</q>}
            {/* Completo en el DOM (se lee y se copia entero); la vista lo recorta con «…». */}
            <code
              className="mono eslabon-sello"
              title={eslabon.sello}
              data-testid="detalle-ticket.firmas.sello"
            >
              {t("firma.sello")}: {eslabon.sello}
            </code>
          </>
        )}
      </div>
    </li>
  );
}
