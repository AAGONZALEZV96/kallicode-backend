/** Layout del portal: header (idioma, tema, sesión) + nav con badge de
 * aprobaciones, actualizado por polling (D12: sin tiempo real). */
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { api } from "../api/client";
import type { Aprobaciones, Notificaciones } from "../api/tipos";
import { useAuth, usePermiso } from "../estado/auth";
import { useI18n, type Idioma } from "../estado/i18n";
import LimiteError from "./LimiteError";

export default function Layout() {
  const { pathname } = useLocation();
  const { usuario, logout, restringido } = useAuth(); // <-- Agrega restringido
  const { t, idioma, cambiarIdioma } = useI18n();
  const { tieneAccesoARuta } = usePermiso();
  const [oscuro, setOscuro] = useState(localStorage.getItem("kc_tema") === "dark");
  const [pendientes, setPendientes] = useState(0);
  const [noLeidas, setNoLeidas] = useState(0);
  // Booleano, no la función: así el polling solo se reinicia si cambia el permiso.
  const puedeAprobar = tieneAccesoARuta("/aprobaciones");

  useEffect(() => {
    document.body.classList.toggle("dark", oscuro);
    localStorage.setItem("kc_tema", oscuro ? "dark" : "light");
  }, [oscuro]);

  // Polling de badges (aprobaciones + notificaciones) cada 30 s.
  useEffect(() => {
    let vivo = true;
    async function refrescar() {
      try {
        if (puedeAprobar) {
          const ap = await api.get<Aprobaciones>("/api/v1/approvals/pending");
          if (vivo) setPendientes(ap.items.length);
        }
        const nt = await api.get<Notificaciones>("/api/v1/notifications?page_size=1");
        if (vivo) setNoLeidas(nt.no_leidas);
      } catch {
        /* silencioso: el polling no molesta al usuario */
      }
    }
    refrescar();
    const timer = setInterval(refrescar, 30_000);
    return () => {
      vivo = false;
      clearInterval(timer);
    };
  }, [usuario, puedeAprobar]);

  const iniciales =
    usuario?.nombre
      .split(" ")
      .map((p) => p[0])
      .slice(0, 2)
      .join("") ?? "?";
  const enlace = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

  return (
    <div id="app">
      <header className="kc">
        <div className="logo">
          <div className="mark">&lt;×&gt;</div>
          <div>
            kallicode<small>{t("login.titulo")}</small>
          </div>
        </div>
        <div className="spacer" />
        <div className="hdr-ctl">
          <select value={idioma} onChange={(e) => cambiarIdioma(e.target.value as Idioma)}>
            <option value="es">ES · Español</option>
            <option value="en">EN · English</option>
            <option value="pt">PT · Português</option>
          </select>
          <button className="icon-btn" onClick={() => setOscuro(!oscuro)}>
            {oscuro ? `☀ ${t("hdr.light")}` : `🌙 ${t("hdr.dark")}`}
          </button>
          {noLeidas > 0 && <span className="chip gate">🔔 {noLeidas}</span>}
          <div className="avatar" title={usuario?.nombre}>
            {iniciales}
          </div>
          <button className="icon-btn" onClick={logout}>
            {t("hdr.salir")}
          </button>
        </div>
      </header>

      <nav className="kc">
        <div className="nav-sec">{t("nav.operate")}</div>

        {tieneAccesoARuta("/") && (
          <NavLink to="/" end className={enlace}>
            <span className="ico">▦</span>
            {t("nav.dashboard")}
          </NavLink>
        )}

        {tieneAccesoARuta("/tickets") && (
          <NavLink to="/tickets" className={enlace}>
            <span className="ico">◧</span>
            {t("nav.tickets")}
          </NavLink>
        )}

        {tieneAccesoARuta("/nuevo-ticket") && (
          <NavLink to="/nuevo-ticket" className={enlace}>
            <span className="ico">＋</span>
            {t("nav.newticket")}
          </NavLink>
        )}

        {tieneAccesoARuta("/nueva-funcionalidad") && (
          <NavLink to="/nueva-funcionalidad" className={enlace}>
            <span className="ico">✦</span>
            {t("nav.newfeature")}
          </NavLink>
        )}

        {tieneAccesoARuta("/aprobaciones") && (
          <NavLink to="/aprobaciones" className={enlace}>
            <span className="ico">✓</span>
            {t("nav.approvals")}
            {pendientes > 0 && <span className="badge">{pendientes}</span>}
          </NavLink>
        )}

        {tieneAccesoARuta("/auditoria") && (
          <NavLink to="/auditoria" className={enlace}>
            <span className="ico">🗎</span>
            {t("nav.audit")}
          </NavLink>
        )}

        {/* La sección de Setup se muestra solo si tiene acceso a al menos una de sus rutas */}
        {(tieneAccesoARuta("/conexiones") ||
          tieneAccesoARuta("/modelos") ||
          tieneAccesoARuta("/consumo")) && <div className="nav-sec">{t("nav.setup")}</div>}

        {tieneAccesoARuta("/conexiones") && (
          <NavLink to="/conexiones" className={enlace}>
            <span className="ico">⛓</span>
            {t("nav.onboarding")}
          </NavLink>
        )}

        {tieneAccesoARuta("/modelos") && (
          <NavLink to="/modelos" className={enlace}>
            <span className="ico">⚙</span>
            {t("nav.models")}
          </NavLink>
        )}

        {tieneAccesoARuta("/consumo") && (
          <NavLink to="/consumo" className={enlace}>
            <span className="ico">◔</span>
            {t("nav.usage")}
          </NavLink>
        )}
      </nav>

      <main className="kc">
        {/* BANNER DE SUSPENSIÓN - Solo visible si la cuenta está restringida por deudas */}
        {restringido && (
          <div
            style={{
              backgroundColor: "#fee2e2",
              color: "#991b1b",
              padding: "1rem",
              border: "1px solid #f87171",
              borderRadius: "0.5rem",
              marginBottom: "1.5rem",
              textAlign: "center",
              fontWeight: "bold",
            }}
          >
            ⚠️ Tu organización tiene la suscripción suspendida por impago. Por favor, regulariza el
            pago para restaurar el acceso al portal.
          </div>
        )}

        {/* Límite por ruta: un error en una vista no tumba header ni navegación. */}
        <LimiteError nivel="ruta" claveReinicio={pathname}>
          <Outlet />
        </LimiteError>
      </main>
    </div>
  );
}
