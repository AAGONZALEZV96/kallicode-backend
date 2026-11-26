/** Layout del portal: header (idioma, tema, sesión) + nav con badge de
 * aprobaciones, actualizado por polling (D12: sin tiempo real). */
import { Suspense, useCallback, useEffect, useId, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAprobaciones, useIntervalosPolling, useNoLeidas } from "../api/consultas";
import { useAuth, usePermiso } from "../estado/auth";
import { esIdioma, useI18n } from "../estado/i18n";
import { leerAvisoAcceso } from "../rutas";
import { useDesborda } from "../useDesborda";
import AvisosCuota from "./AvisosCuota";
import BandejaNotificaciones from "./BandejaNotificaciones";
import CargandoPagina from "./CargandoPagina";
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

  // Bandeja de notificaciones (KC-PT-06-1): la campana la abre y la cierra.
  const [bandejaAbierta, setBandejaAbierta] = useState(false);
  const idBandeja = useId();
  const campana = useRef<HTMLButtonElement>(null);
  const anclaBandeja = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLElement>(null);
  const contenido = useRef<HTMLElement>(null);
  // Una vista larga sin nada enfocable (p. ej. Modelos IA) solo se podía desplazar con el mouse:
  // entonces <main> entra en el orden de Tab y se desplaza con las flechas (KC-PT-10-2).
  const { desborda, conEnfocables } = useDesborda(contenido);
  const cerrarBandeja = useCallback((devolverFoco: boolean) => {
    setBandejaAbierta(false);
    if (devolverFoco) campana.current?.focus();
  }, []);

  useEffect(() => {
    document.body.classList.toggle("dark", oscuro);
    localStorage.setItem("kc_tema", oscuro ? "dark" : "light");
  }, [oscuro]);

  // Cada vista empieza arriba: el contenido se desplaza dentro de <main>, que es el mismo en
  // todas las rutas y conservaba el scroll de la vista anterior (KC-PT-10-1). En el celular y
  // la tablet el menú es una barra horizontal: se centra en la vista activa para que se vea.
  useEffect(() => {
    if (contenido.current) contenido.current.scrollTop = 0;
    const barra = menu.current;
    const activo = barra?.querySelector<HTMLElement>("a.active");
    if (!barra || !activo || barra.scrollWidth <= barra.clientWidth) return;
    const caja = barra.getBoundingClientRect();
    const enlace = activo.getBoundingClientRect();
    barra.scrollLeft += enlace.left - caja.left - (caja.width - enlace.width) / 2;
  }, [pathname]);

  const iniciales =
    usuario?.nombre
      .split(" ")
      .map((p) => p[0])
      .slice(0, 2)
      .join("") ?? "?";
  const enlace = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

  return (
    <div id="app">
      {/* Primer elemento enfocable: con teclado evita recorrer el header y el menú (PT-F12). */}
      <a className="saltar" href="#contenido">
        {t("nav.saltar")}
      </a>
      <header className="kc">
        <div className="logo">
          <div className="mark" aria-hidden="true">
            &lt;×&gt;
          </div>
          <div>
            kallicode<small>{t("login.titulo")}</small>
          </div>
        </div>
        <div className="spacer" />
        <div className="hdr-ctl">
          <select
            value={idioma}
            aria-label={t("hdr.idioma")}
            onChange={(e) => {
              if (esIdioma(e.target.value)) cambiarIdioma(e.target.value);
            }}
            data-testid="layout.cabecera.idioma"
          >
            <option value="es">ES · Español</option>
            <option value="en">EN · English</option>
            <option value="pt">PT · Português</option>
          </select>
          <button
            className="icon-btn"
            aria-pressed={oscuro}
            onClick={() => setOscuro(!oscuro)}
            data-testid="layout.cabecera.tema"
          >
            <span aria-hidden="true">{oscuro ? "☀" : "🌙"}</span>{" "}
            {/* En el celular solo queda el ícono; el texto sigue siendo el nombre del botón. */}
            <span className="hdr-etiqueta">{t(oscuro ? "hdr.light" : "hdr.dark")}</span>
          </button>
          {/* Con la suscripción suspendida el backend no atiende /notifications. */}
          {!restringido && (
            <div className="campana" ref={anclaBandeja}>
              <button
                ref={campana}
                type="button"
                className="icon-btn"
                aria-expanded={bandejaAbierta}
                aria-controls={idBandeja}
                aria-label={
                  sinLeer > 0 ? t("hdr.notificaciones", { n: String(sinLeer) }) : t("notif.titulo")
                }
                onClick={() => setBandejaAbierta((abierta) => !abierta)}
                data-testid="layout.cabecera.campana"
              >
                <span aria-hidden="true">🔔</span>
                {sinLeer > 0 && (
                  <span
                    className="chip gate"
                    aria-hidden="true"
                    data-testid="layout.cabecera.notificaciones"
                  >
                    {sinLeer}
                  </span>
                )}
              </button>
              {bandejaAbierta && (
                <BandejaNotificaciones
                  id={idBandeja}
                  intervaloMs={notificacionesMs}
                  ancla={anclaBandeja}
                  alCerrar={cerrarBandeja}
                />
              )}
            </div>
          )}
          <div
            className="avatar"
            title={usuario?.nombre}
            role="img"
            aria-label={usuario?.nombre ?? ""}
            data-testid="layout.cabecera.avatar"
          >
            {iniciales}
          </div>
          <button className="icon-btn" onClick={logout} data-testid="layout.cabecera.salir">
            {t("hdr.salir")}
          </button>
        </div>
      </header>

      {/* Cada enlace usa como elemento el nombre de la vista de destino. */}
      <nav className="kc" aria-label={t("nav.principal")} ref={menu}>
        <div className="nav-sec">{t("nav.operate")}</div>

        {tieneAccesoARuta("/") && (
          <NavLink to="/" end className={enlace} data-testid="layout.nav.tablero">
            <span className="ico" aria-hidden="true">
              ▦
            </span>
            {t("nav.dashboard")}
          </NavLink>
        )}

        {tieneAccesoARuta("/tickets") && (
          <NavLink to="/tickets" className={enlace} data-testid="layout.nav.tickets">
            <span className="ico" aria-hidden="true">
              ◧
            </span>
            {t("nav.tickets")}
          </NavLink>
        )}

        {tieneAccesoARuta("/nuevo-ticket") && (
          <NavLink to="/nuevo-ticket" className={enlace} data-testid="layout.nav.nuevo-ticket">
            <span className="ico" aria-hidden="true">
              ＋
            </span>
            {t("nav.newticket")}
          </NavLink>
        )}

        {tieneAccesoARuta("/nueva-funcionalidad") && (
          <NavLink
            to="/nueva-funcionalidad"
            className={enlace}
            data-testid="layout.nav.nueva-funcionalidad"
          >
            <span className="ico" aria-hidden="true">
              ✦
            </span>
            {t("nav.newfeature")}
          </NavLink>
        )}

        {tieneAccesoARuta("/aprobaciones") && (
          <NavLink to="/aprobaciones" className={enlace} data-testid="layout.nav.aprobaciones">
            <span className="ico" aria-hidden="true">
              ✓
            </span>
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
            <span className="ico" aria-hidden="true">
              🗎
            </span>
            {t("nav.audit")}
          </NavLink>
        )}

        {/* La sección de Setup se muestra solo si tiene acceso a al menos una de sus rutas */}
        {(tieneAccesoARuta("/onboarding") ||
          tieneAccesoARuta("/conexiones") ||
          tieneAccesoARuta("/modelos") ||
          tieneAccesoARuta("/consumo")) && <div className="nav-sec">{t("nav.setup")}</div>}

        {tieneAccesoARuta("/onboarding") && (
          <NavLink to="/onboarding" className={enlace} data-testid="layout.nav.onboarding">
            <span className="ico" aria-hidden="true">
              ➤
            </span>
            {t("nav.asistente")}
          </NavLink>
        )}

        {tieneAccesoARuta("/conexiones") && (
          <NavLink to="/conexiones" className={enlace} data-testid="layout.nav.conexiones">
            <span className="ico" aria-hidden="true">
              ⛓
            </span>
            {t("nav.onboarding")}
          </NavLink>
        )}

        {tieneAccesoARuta("/modelos") && (
          <NavLink to="/modelos" className={enlace} data-testid="layout.nav.modelos-ia">
            <span className="ico" aria-hidden="true">
              ⚙
            </span>
            {t("nav.models")}
          </NavLink>
        )}

        {tieneAccesoARuta("/consumo") && (
          <NavLink to="/consumo" className={enlace} data-testid="layout.nav.consumo">
            <span className="ico" aria-hidden="true">
              ◔
            </span>
            {t("nav.usage")}
          </NavLink>
        )}

        {/* Equipo (KC-PT-10-5), ajustes de la organización y perfil propio (KC-PT-10-4). */}
        {tieneAccesoARuta("/equipo") && (
          <NavLink to="/equipo" className={enlace} data-testid="layout.nav.equipo">
            <span className="ico" aria-hidden="true">
              ☷
            </span>
            {t("nav.equipo")}
          </NavLink>
        )}
        {tieneAccesoARuta("/organizacion") && (
          <NavLink to="/organizacion" className={enlace} data-testid="layout.nav.organizacion">
            <span className="ico" aria-hidden="true">
              ⌂
            </span>
            {t("nav.organizacion")}
          </NavLink>
        )}
        {tieneAccesoARuta("/perfil") && (
          <NavLink to="/perfil" className={enlace} data-testid="layout.nav.perfil">
            <span className="ico" aria-hidden="true">
              ☺
            </span>
            {t("nav.perfil")}
          </NavLink>
        )}
      </nav>

      {/* tabIndex -1: el enlace «saltar al contenido» le puede pasar el foco. */}
      <main
        className="kc"
        id="contenido"
        tabIndex={desborda && !conEnfocables ? 0 : -1}
        ref={contenido}
      >
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
        {/* Cuota de LOC, almacenamiento y tickets en cola (KC-PT-09-5). */}
        <AvisosCuota />

        {/* Límite por ruta: un error en una vista no tumba header ni navegación. */}
        <LimiteError nivel="ruta" claveReinicio={pathname}>
          {/* La vista se descarga al entrar (KC-PT-11-1): header y menú siguen a la vista. */}
          <Suspense fallback={<CargandoPagina />}>
            <Outlet />
          </Suspense>
        </LimiteError>
      </main>
    </div>
  );
}
