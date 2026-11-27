/**
 * Aviso de borrador recuperado (KC-PT-11-6): el formulario arrancó con lo que se había escrito;
 * se puede descartar. data-testid: <vista>.borrador.aviso y <vista>.borrador.descartar.
 */
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";

export default function AvisoBorrador({
  vista,
  guardado,
  alDescartar,
}: {
  vista: string;
  guardado: Date;
  alDescartar: () => void;
}) {
  const { t } = useI18n();
  const fmt = useFormato();
  return (
    <div className="aviso-box aviso-borrador" role="status" data-testid={`${vista}.borrador.aviso`}>
      <span>
        {t("borrador.recuperado", { fecha: fmt.fecha(guardado.toISOString()) })}{" "}
        {t("borrador.adjuntos")}
      </span>
      <button
        type="button"
        className="btn sm"
        onClick={alDescartar}
        data-testid={`${vista}.borrador.descartar`}
      >
        {t("borrador.descartar")}
      </button>
    </div>
  );
}
