/**
 * Control de acceso por rol a las vistas (cierra PT-F06): una ruta del mapa
 * MAPA_RUTAS_ROLES que el rol no puede abrir redirige al tablero. El backend sigue
 * siendo quien autoriza cada endpoint; esto evita mostrar vistas que responderían 403.
 *
 * Con la suscripción suspendida (acceso_restringido) solo se puede estar en /consumo
 * (cierra PT-F07).
 */
import { matchPath, Navigate, Outlet, useLocation } from "react-router-dom";

import { MAPA_RUTAS_ROLES, puedeVerRuta, rolActual, useAuth } from "../estado/auth";

export default function RutaProtegida() {
  const { usuario, restringido } = useAuth();
  const rol = rolActual(usuario);
  const { pathname } = useLocation();

  if (restringido && pathname !== "/consumo") {
    return <Navigate to="/consumo" replace state={{ errorAcceso: "Suscripción suspendida." }} />;
  }

  // Patrón del mapa que corresponde a la URL (incluye parámetros como :numero).
  const patron = Object.keys(MAPA_RUTAS_ROLES).find((p) =>
    matchPath({ path: p, end: true }, pathname),
  );

  // Fuera del mapa no se decide aquí: la ruta "*" lleva al tablero.
  if (!patron || puedeVerRuta(rol, patron)) return <Outlet />;

  return (
    <Navigate
      to="/"
      replace
      state={{ errorAcceso: `Tu rol (${rol}) no permite acceder a ${pathname}` }}
    />
  );
}
