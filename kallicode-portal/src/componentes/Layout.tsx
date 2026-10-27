/** Layout del portal: header (idioma, tema, sesión) + nav con badge de
 * aprobaciones, actualizado por polling (D12: sin tiempo real). */
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAprobaciones, useIntervalosPolling, useNoLeidas } from "../api/consultas";
import { useAuth, usePermiso } from "../estado/auth";
import { useI18n, type Idioma } from "../estado/i18n";
import { leerAvisoAcceso } from "../rutas";
import LimiteError from "./LimiteError";

export default function Layout() {
  const { pathname, state } = useLocation();
  const avisoAcceso = leerAvisoAcceso(state);
  const { usuario, logout, restringido } = useAuth();
  const { t, idioma, cambiarIdioma } = useI18n();
  const { tieneAccesoARuta } = usePermiso();
  const [oscuro, setOscuro] = useState(localStorage.getItem("kc_tema") === "dark");

  // Polling de badges al intervalo de notificaciones que publica GET /organization (PT-F15).
  // Un fallo es silencioso y conserva el último valor. La consulta de aprobaciones es la
  // misma de la vista Aprobaciones: comparten petición y caché.
  const { notificacionesMs } = useIntervalosPolling();
  const aprobaciones = useAprobaciones({
    habilitada: tieneAccesoARuta("/aprobaciones"),
    intervaloMs: notificacionesMs,
  });
  // Con la suscripción suspendida el backend responde 403: no se consulta.
  const noLeidas = useNoLeidas({ habilitada: !restringido, intervaloMs: notificacionesMs });
  const pendientes = aprobaciones.data?.items.length ?? 0;
  const sinLeer = restringido ? 0 : (noLeidas.data ?? 0);

  useEffect(() => {
    document.body.classList.toggle("dark", oscuro);
    localStorage.setItem("kc_tema", oscuro ? "dark" : "light");
  }, [oscuro]);

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
          <select
            value={idioma}
            onChange={(e) => cambiarIdioma(e.target.value as Idioma)}
            data-testid="layout.cabecera.idioma"
          >
            <option value="es">ES · Español</option>
            <option value="en">EN · English</option>
            <option value="pt">PT · Português</option>
          </select>
          <button
            className="icon-btn"
            onClick={() => setOscuro(!oscuro)}
            data-testid="layout.cabecera.tema"
          >
            {oscuro ? `☀ ${t("hdr.light")}` : `🌙 ${t("hdr.dark")}`}
          </button>
          {sinLeer > 0 && (
            <span className="chip gate" data-testid="layout.cabecera.notificaciones">
              🔔 {sinLeer}
            </span>
          )}
          <div className="avatar" title={usuario?.nombre} data-testid="layout.cabecera.avatar">
            {iniciales}
          </div>
          <button className="icon-btn" onClick={logout} data-testid="layout.cabecera.salir">
            {t("hdr.salir")}
          </button>
        </div>
      </header>

      {/* Cada enlace usa como elemento el nombre de la vista de destino. */}
      <nav className="kc">
        <div className="nav-sec">{t("nav.operate")}</div>

        {tieneAccesoARuta("/") && (
          <NavLink to="/" end className={enlace} data-testid="layout.nav.tablero">
            <span className="ico">▦</span>
            {t("nav.dashboard")}
          </NavLink>
        )}

        {tieneAccesoARuta("/tickets") && (
          <NavLink to="/tickets" className={enlace} data-testid="layout.nav.tickets">
            <span className="ico">◧</span>
            {t("nav.tickets")}
          </NavLink>
        )}

        {tieneAccesoARuta("/nuevo-ticket") && (
          <NavLink to="/nuevo-ticket" className={enlace} data-testid="layout.nav.nuevo-ticket">
            <span className="ico">＋</span>
            {t("nav.newticket")}
          </NavLink>
        )}

        {tieneAccesoARuta("/nueva-funcionalidad") && (
          <NavLink
            to="/nueva-funcionalidad"
            className={enlace}
            data-testid="layout.nav.nueva-funcionalidad"
          >
            <span className="ico">✦</span>
            {t("nav.newfeature")}
          </NavLink>
        )}

        {tieneAccesoARuta("/aprobaciones") && (
          <NavLink to="/aprobaciones" className={enlace} data-testid="layout.nav.aprobaciones">
            <span className="ico">✓</span>
            {t("nav.approvals")}
            {pendientes > 0 && (
              <span className="badge" data-testid="layout.nav.pendientes">
                {pendientes}
              </span>
            )}
          </NavLink>
        )}

        {tieneAccesoARuta("/auditoria") && (
          <NavLink to="/auditoria" className={enlace} data-testid="layout.nav.auditoria">
            <span className="ico">🗎</span>
            {t("nav.audit")}
          </NavLink>
        )}

        {/* La sección de Setup se muestra solo si tiene acceso a al menos una de sus rutas */}
        {(tieneAccesoARuta("/conexiones") ||
          tieneAccesoARuta("/modelos") ||
          tieneAccesoARuta("/consumo")) && <div className="nav-sec">{t("nav.setup")}</div>}

        {tieneAccesoARuta("/conexiones") && (
          <NavLink to="/conexiones" className={enlace} data-testid="layout.nav.conexiones">
            <span className="ico">⛓</span>
            {t("nav.onboarding")}
          </NavLink>
        )}

        {tieneAccesoARuta("/modelos") && (
          <NavLink to="/modelos" className={enlace} data-testid="layout.nav.modelos-ia">
            <span className="ico">⚙</span>
            {t("nav.models")}
          </NavLink>
        )}

        {tieneAccesoARuta("/consumo") && (
          <NavLink to="/consumo" className={enlace} data-testid="layout.nav.consumo">
            <span className="ico">◔</span>
            {t("nav.usage")}
          </NavLink>
        )}
      </nav>

      <main className="kc">
        {/* Suscripción suspendida (PT-F07): explica por qué solo queda Consumo. */}
        {restringido && (
          <div className="error-box" role="alert" data-testid="layout.avisos.suscripcion">
            {t("sub.banner.suspendida")}
          </div>
        )}
        {/* Redirección por rol (PT-F06): dura hasta la siguiente navegación. */}
        {avisoAcceso && (
          <div className="aviso-box" role="status" data-testid="layout.avisos.acceso">
            {t("acceso.rol", { rol: t(`rol.${avisoAcceso.rol}`) })}
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
