/** Enrutado del portal: login sin sesión; con sesión, layout + 12 vistas. */
import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./componentes/Layout";
import { useAuth } from "./estado/auth";
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

export default function App() {
  const { usuario } = useAuth();
  if (!usuario) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
