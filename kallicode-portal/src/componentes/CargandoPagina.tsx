/**
 * Lo que se ve mientras se descarga una vista (KC-PT-11-1): el aviso de carga, sin data-testid
 * (el indicador de cada vista es comun.carga.indicador, que aparece una sola vez por vista).
 */
import { useI18n } from "../estado/i18n";

export default function CargandoPagina() {
  const { t } = useI18n();
  return (
    <p className="cargando-pagina" role="status">
      {t("comun.cargando")}
    </p>
  );
}
