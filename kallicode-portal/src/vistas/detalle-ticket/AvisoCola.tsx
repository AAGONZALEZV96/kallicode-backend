/**
 * Ticket en cola por cuota (KC-PT-09-5): por qué no avanza y cuándo lo hará. Entró con la cuota
 * de LOC del ciclo agotada y el worker de renovación (billing_cycle) lo pasa al pipeline al
 * renovarse la cuota. Mejorar el plan no lo libera antes, así que el aviso no lo promete.
 */
import { Link } from "react-router-dom";

import { useResumenConsumo } from "../../api/consultas";
import { usePermiso } from "../../estado/auth";
import { useFormato } from "../../estado/formato";
import { useI18n } from "../../estado/i18n";

export default function AvisoCola() {
  const { t } = useI18n();
  const fmt = useFormato();
  const { tieneAccesoARuta } = usePermiso();
  // La fecha de renovación: si el resumen no llega, el aviso se muestra sin ella.
  const renueva = useResumenConsumo().data?.plan?.renueva_el;
  return (
    <div className="aviso-box aviso-cuota" role="status" data-testid="detalle-ticket.avisos.cola">
      <p>
        {renueva ? t("td.en_cola", { fecha: fmt.dia(renueva) }) : t("td.en_cola.sin_fecha")}{" "}
        {tieneAccesoARuta("/consumo") && (
          <Link to="/consumo" data-testid="detalle-ticket.avisos.cola-enlace">
            {t("aviso.ver_consumo")}
          </Link>
        )}
      </p>
    </div>
  );
}
