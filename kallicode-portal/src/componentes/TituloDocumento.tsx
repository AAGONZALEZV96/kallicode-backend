/**
 * Título de la pestaña del navegador según la vista (KC-PT-10-2, WCAG 2.4.2): «Tickets ·
 * Kallicode», «KC-1003 · Tickets · Kallicode». Con un único título para todo el portal, el
 * historial, las pestañas y el lector de pantalla no distinguían una vista de otra.
 */
import { useEffect } from "react";
import { matchPath, useLocation } from "react-router-dom";

import { useI18n } from "../estado/i18n";

const MARCA = "Kallicode";

/** Clave del título de cada ruta; las de detalle anteponen el número del ticket. */
const TITULOS: [string, string][] = [
  ["/", "nav.dashboard"],
  ["/tickets", "nav.tickets"],
  ["/tickets/:numero", "nav.tickets"],
  ["/nuevo-ticket", "nav.newticket"],
  ["/nueva-funcionalidad", "nav.newfeature"],
  ["/aprobaciones", "nav.approvals"],
  ["/auditoria", "nav.audit"],
  ["/auditoria/:numero", "nav.audit"],
  ["/onboarding", "nav.asistente"],
  ["/conexiones", "nav.onboarding"],
  ["/modelos", "nav.models"],
  ["/consumo", "nav.usage"],
  ["/perfil", "nav.perfil"],
  ["/organizacion", "nav.organizacion"],
  ["/equipo", "nav.equipo"],
  ["/login", "login.h1"],
  ["/recuperar-clave", "rc.titulo"],
  ["/restablecer-clave", "rs.titulo"],
  ["/invitacion", "ai.titulo"],
];

export default function TituloDocumento() {
  const { pathname } = useLocation();
  const { t } = useI18n();
  useEffect(() => {
    for (const [patron, clave] of TITULOS) {
      const coincide = matchPath(patron, pathname);
      if (!coincide) continue;
      const partes = [coincide.params.numero, t(clave), MARCA].filter(Boolean);
      document.title = partes.join(" · ");
      return;
    }
    document.title = MARCA;
  }, [pathname, t]);
  return null;
}
