import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth, MAPA_RUTAS_ROLES, Rol } from '../estado/auth';

export const RutaProtegida = () => {
  const { usuario, cargando } = useAuth();
  const location = useLocation();
  const rutaActual = location.pathname;

  if (cargando) return <div>Cargando sesión...</div>; // Evita redirigir antes de tener el perfil

  const rolUsuario = (usuario?.rol as Rol) || 'viewer';
  const rolesPermitidos = MAPA_RUTAS_ROLES[rutaActual];
  
  if (!rolesPermitidos) {
    throw new Error(`Error: La ruta ${rutaActual} no está declarada en el mapa RBAC.`);
  }

  if (!rolesPermitidos.includes(rolUsuario)) {
    // Busca una ruta a la que sí tenga acceso (fallback a '/')
    const primeraRutaPermitida = Object.entries(MAPA_RUTAS_ROLES).find(
      ([, roles]) => roles.includes(rolUsuario)
    )?.[0] || '/';

    // Redirige y pasa un mensaje en el estado para mostrar un toast/alerta en la vista de destino
    return (
      <Navigate 
        to={primeraRutaPermitida} 
        replace 
        state={{ errorAcceso: `Tu rol actual (${rolUsuario}) no permite acceder a esta sección.` }} 
      />
    );
  }

  return <Outlet />;
};