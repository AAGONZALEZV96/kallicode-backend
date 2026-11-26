/**
 * Enrutado del portal: /login sin sesión; con sesión, layout + 12 vistas.
 * Sin sesión, cualquier ruta lleva a /login?siguiente=<ruta> y, al entrar, se vuelve a
 * ella (KC-PT-02-5): enlaces directos, recargas y sesiones expiradas no pierden el destino.
 * Las pantallas públicas (KC-PT-10-6) se abren con o sin sesión: llegan desde un correo.
 * Cada vista se descarga al entrar en su ruta (KC-PT-11-1, vistasDiferidas.ts); el login va en el
 * paquete inicial porque es lo primero que se ve.
 */
import { Suspense } from "react";
import { Navigate, Route, Routes, useLocation, useSearchParams } from "react-router-dom";

import CargandoPagina from "./componentes/CargandoPagina";
import RutaProtegida from "./componentes/RutaProtegida";
import TituloDocumento from "./componentes/TituloDocumento";
import { useAuth } from "./estado/auth";
import { destinoSeguro } from "./rutas";
import Login from "./vistas/Login";
import {
  AceptarInvitacion,
  Aprobaciones,
  Auditoria,
  Conexiones,
  Consumo,
  DetalleTicket,
  Equipo,
  Expediente,
  Layout,
  ModelosIA,
  NuevaFuncionalidad,
  NuevoTicket,
  Onboarding,
  Organizacion,
  Perfil,
  RecuperarClave,
  RestablecerClave,
  Tablero,
  Tickets,
} from "./vistasDiferidas";

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
    <>
      {/* Título de la pestaña según la vista (WCAG 2.4.2). */}
      <TituloDocumento />
      <Suspense fallback={<CargandoPagina />}>
        <Routes>
          <Route path="/login" element={usuario ? <SalirDeLogin /> : <Login />} />
          <Route path="/recuperar-clave" element={<RecuperarClave />} />
          <Route path="/restablecer-clave" element={<RestablecerClave />} />
          <Route path="/invitacion" element={<AceptarInvitacion />} />
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
              <Route path="/onboarding" element={<Onboarding />} />
              <Route path="/conexiones" element={<Conexiones />} />
              <Route path="/modelos" element={<ModelosIA />} />
              <Route path="/consumo" element={<Consumo />} />
              <Route path="/perfil" element={<Perfil />} />
              <Route path="/organizacion" element={<Organizacion />} />
              <Route path="/equipo" element={<Equipo />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </>
  );
}
