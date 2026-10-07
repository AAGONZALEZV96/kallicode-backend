/**
 * Enrutado del portal: /login sin sesión; con sesión, layout + 12 vistas.
 * Sin sesión, cualquier ruta lleva a /login?siguiente=<ruta> y, al entrar, se vuelve a
 * ella (KC-PT-02-5): enlaces directos, recargas y sesiones expiradas no pierden el destino.
 */
import { Navigate, Route, Routes, useLocation, useSearchParams, matchPath, Outlet } from "react-router-dom";

import Layout from "./componentes/Layout";
import { MAPA_RUTAS_ROLES, type Rol, useAuth } from "./estado/auth";
import { destinoSeguro } from "./rutas";
import Aprobaciones from "./vistas/Aprobaciones";
import Auditoria from "./vistas/Auditoria";
import Conexiones from "./vistas/Conexiones";
import Consumo from "./vistas/Consumo";
import DetalleTicket from "./vistas/DetalleTicket";
import Expediente from "./vistas/Expediente";
import Login from "./vistas/Login";
import ModelosIA from "./vistas/ModelosIA";
import NuevaFuncionalidad from "./vistas/NuevaFuncionalidad";
import NuevoTicket from "./vistas/NuevoTicket";
import Tablero from "./vistas/Tablero";
import Tickets from "./vistas/Tickets";

function IrALogin() {
  const { pathname, search } = useLocation();
  const destino = pathname + search;
  const consulta = destino === "/" ? "" : `?siguiente=${encodeURIComponent(destino)}`;
  return <Navigate to={`/login${consulta}`} replace />;
}

function SalirDeLogin() {
  const [parametros] = useSearchParams();
  return <Navigate to={destinoSeguro(parametros.get("siguiente"))} replace />;
}

// NUEVO: Componente RutaProtegida que intercepta y valida contra el RBAC y Confinamiento
function RutaProtegida() {
  const { usuario, restringido } = useAuth(); // <-- Agregamos restringido aquí
  const { pathname } = useLocation();

  // NUEVO: Si la cuenta está restringida y trata de ir a cualquier lugar distinto de /consumo, lo devolvemos a la fuerza
  if (restringido && pathname !== '/consumo') {
    return <Navigate to="/consumo" replace state={{ errorAcceso: "Suscripción suspendida." }} />;
  }

  const rolUsuario = (usuario?.rol as Rol) || "viewer";

  // Buscamos si la URL actual coincide con algún patrón del mapa declarativo (incluyendo :numero)
  const rutaDeclarada = Object.keys(MAPA_RUTAS_ROLES).find((patron) =>
    matchPath({ path: patron, end: true }, pathname)
  );

  // Si no está en el mapa, dejamos que pase (caerá en el "*" y redirigirá a "/")
  if (!rutaDeclarada) return <Outlet />;

  const rolesPermitidos = MAPA_RUTAS_ROLES[rutaDeclarada];

  // Si el rol no tiene permiso, lo devolvemos al inicio de forma segura
  if (!rolesPermitidos.includes(rolUsuario)) {
    return <Navigate to="/" replace state={{ errorAcceso: `Tu rol (${rolUsuario}) no permite acceder a ${pathname}` }} />;
  }

  return <Outlet />;
}

export default function App() {
  const { usuario } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={usuario ? <SalirDeLogin /> : <Login />} />
      <Route element={usuario ? <Layout /> : <IrALogin />}>
        
        {/* NUEVO: Envolvemos todas las vistas protegidas con RutaProtegida */}
        <Route element={<RutaProtegida />}>
          <Route path="/" element={<Tablero />} />
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/tickets/:numero" element={<DetalleTicket />} />
          <Route path="/nuevo-ticket" element={<NuevoTicket />} />
          <Route path="/nueva-funcionalidad" element={<NuevaFuncionalidad />} />
          <Route path="/aprobaciones" element={<Aprobaciones />} />
          <Route path="/auditoria" element={<Auditoria />} />
          <Route path="/auditoria/:numero" element={<Expediente />} />
          <Route path="/conexiones" element={<Conexiones />} />
          <Route path="/modelos" element={<ModelosIA />} />
          <Route path="/consumo" element={<Consumo />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}