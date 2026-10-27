/**
 * Enrutado del portal: /login sin sesión; con sesión, layout + 12 vistas.
 * Sin sesión, cualquier ruta lleva a /login?siguiente=<ruta> y, al entrar, se vuelve a
 * ella (KC-PT-02-5): enlaces directos, recargas y sesiones expiradas no pierden el destino.
 */
import { Navigate, Route, Routes, useLocation, useSearchParams } from "react-router-dom";

import Layout from "./componentes/Layout";
import RutaProtegida from "./componentes/RutaProtegida";
import { useAuth } from "./estado/auth";
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

export default function App() {
  const { usuario } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={usuario ? <SalirDeLogin /> : <Login />} />
      <Route element={usuario ? <Layout /> : <IrALogin />}>
        {/* Cada vista se abre solo con un rol de MAPA_RUTAS_ROLES (PT-F06). */}
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
