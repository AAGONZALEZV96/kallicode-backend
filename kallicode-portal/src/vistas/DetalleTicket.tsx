/**
 * Detalle de ticket (§9.6): stepper del pipeline, 5 pestañas, panel de gate y modal de
 * firma (§10). Las pestañas cargan bajo demanda.
 *
 * Esta vista solo arma la pantalla; cada parte es un componente de vistas/detalle-ticket/
 * (KC-PT-06-7), con sus data-testid de la vista detalle-ticket.
 */
import { useId, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useTicket, type Pestana } from "../api/consultas";
import { CajaError, Cargando } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import BotonCancelar from "./detalle-ticket/BotonCancelar";
import Cabecera from "./detalle-ticket/Cabecera";
import ListaPestanas from "./detalle-ticket/ListaPestanas";
import ModalFirma, { type SolicitudFirma } from "./detalle-ticket/ModalFirma";
import PanelGate from "./detalle-ticket/PanelGate";
import { ETAPAS_SIN_CANCELACION } from "./detalle-ticket/pestanas";
import Pipeline from "./detalle-ticket/Pipeline";
import Resumen from "./detalle-ticket/Resumen";
import TabActividad from "./detalle-ticket/TabActividad";
import TabConsumo from "./detalle-ticket/TabConsumo";
import TabEvidencia from "./detalle-ticket/TabEvidencia";
import TabSeguridad from "./detalle-ticket/TabSeguridad";
import TabSpec from "./detalle-ticket/TabSpec";
import Visualizadores from "./detalle-ticket/Visualizadores";

export default function DetalleTicket() {
  const { numero = "" } = useParams();
  const { puedeHacer } = usePermiso();
  const nav = useNavigate();
  const { data: tk, error } = useTicket(numero);
  const [pestana, setPestana] = useState<Pestana>("spec");
  // La firma se guarda aquí: el panel de gate desaparece al firmar y el modal debe seguir.
  const [firma, setFirma] = useState<{ gate: number; solicitud: SolicitudFirma } | null>(null);
  const id = useId();

  if (error)
    return (
      <div className="view" data-testid="detalle-ticket.vista.raiz">
        <CajaError error={error} />
      </div>
    );
  if (!tk) return <Cargando />;

  const gate = tk.gate_pendiente;
  const puedeFirmar = gate != null && puedeHacer({ tipo: "firmar-gate", gate });
  const puedeCancelar =
    puedeHacer({ tipo: "cancelar-ticket", creadorId: tk.reportado_por?.id ?? null }) &&
    !ETAPAS_SIN_CANCELACION.includes(tk.etapa);

  return (
    <div className="view" data-testid="detalle-ticket.vista.raiz">
      <Cabecera ticket={tk} />
      <Pipeline ticket={tk} />

      <div className="grid2 detalle">
        <div className="card">
          <ListaPestanas activa={pestana} alCambiar={setPestana} idBase={id} />
          <div
            role="tabpanel"
            id={`${id}-panel`}
            aria-labelledby={`${id}-tab-${pestana}`}
            // El panel recibe el foco con Tab cuando su contenido no tiene nada enfocable.
            tabIndex={0}
          >
            {pestana === "spec" && <TabSpec numero={numero} />}
            {pestana === "evidence" && <TabEvidencia numero={numero} />}
            {pestana === "security" && <TabSeguridad numero={numero} />}
            {pestana === "usage" && <TabConsumo numero={numero} />}
            {pestana === "activity" && <TabActividad numero={numero} />}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {/* La clave reinicia el panel (comentario, alternativa) al pasar a otro gate. */}
          {puedeFirmar && gate && (
            <PanelGate
              key={gate}
              numero={numero}
              gate={gate}
              alSolicitar={(solicitud) => setFirma({ gate, solicitud })}
            />
          )}
          <Resumen ticket={tk} />
          <Visualizadores ticket={tk} />
          {puedeCancelar && <BotonCancelar numero={numero} alCancelar={() => nav("/tickets")} />}
        </div>
      </div>

      {firma && (
        <ModalFirma
          numero={numero}
          gate={firma.gate}
          solicitud={firma.solicitud}
          alCerrar={() => setFirma(null)}
        />
      )}
    </div>
  );
}
