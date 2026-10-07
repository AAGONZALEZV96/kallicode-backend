/* eslint-disable react-refresh/only-export-components */
/**
 * Contexto de sesión: login/logout, usuario actual y expiración automática.
 * Como los tokens, el usuario vive solo en memoria (KC-PT-02-2): al recargar no hay sesión.
 *
 * El perfil se verifica contra GET /users/me al iniciar sesión y tras cada renovación de
 * tokens (KC-PT-02-5, cierra PT-F21): el backend aplica al instante un cambio de rol, y
 * sin esto la interfaz seguiría mostrando lo que permitía el rol anterior.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, alExpirarSesion, alRenovarSesion, cerrarSesion, fijarSesion } from "../api/client";
import type { PerfilPropio, Sesion, Usuario } from "../api/tipos";

interface AuthCtx {
  usuario: Usuario | null;
  restringido: boolean;
  cargando: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);

const pedirPerfil = () => api.get<PerfilPropio>("/api/v1/users/me");

/** El perfil del servidor manda; org_id solo viene en la respuesta de login. */
function conPerfil(usuario: Usuario, perfil: PerfilPropio): Usuario {
  if (perfil.id !== usuario.id) return usuario;
  const { nombre, email, rol, idioma } = perfil;
  return { ...usuario, nombre, email, rol, idioma };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const [restringido, setRestringido] = useState(false);
  const [cargando, setCargando] = useState(false);

  useEffect(() => {
    alExpirarSesion(() => setUsuario(null));
    alRenovarSesion(() => {
      pedirPerfil()
        .then((perfil) => {
          setUsuario((u) => (u ? conPerfil(u, perfil) : u));
          // Persiste la restricción en caso de recargar la página
          setRestringido((perfil as unknown as { acceso_restringido?: boolean }).acceso_restringido ?? false);
        })
        .catch(() => {});
    });
    return () => alRenovarSesion(null);
  }, []);

  async function login(email: string, password: string) {
    setCargando(true);
    try {
      const s = await api.post<Sesion>("/api/v1/auth/login", { email, password });
      fijarSesion(s.access_token, s.refresh_token);
      let perfil: PerfilPropio;
      try {
        perfil = await pedirPerfil();
      } catch (e) {
        await cerrarSesion();
        throw e;
      }
      setUsuario(conPerfil(s.usuario, perfil));
      // Priorizamos la restricción del perfil
      setRestringido((perfil as unknown as { acceso_restringido?: boolean }).acceso_restringido ?? s.acceso_restringido);
    } finally {
      setCargando(false);
    }
  }

  function logout() {
    setUsuario(null);
    setRestringido(false);
    void cerrarSesion();
  }

  return (
    <Ctx.Provider value={{ usuario, restringido, cargando, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAuth = () => useContext(Ctx);

// --- CONTROL DE ACCESO (RBAC) ---

export type Rol = 'admin' | 'owner' | 'arquitecto' | 'aprobador' | 'member' | 'viewer';

export const MAPA_RUTAS_ROLES: Record<string, Rol[]> = {
  '/': ['admin', 'owner', 'arquitecto', 'aprobador', 'member', 'viewer'],
  '/tickets': ['admin', 'owner', 'arquitecto', 'aprobador', 'member', 'viewer'],
  '/tickets/:numero': ['admin', 'owner', 'arquitecto', 'aprobador', 'member', 'viewer'],
  '/nuevo-ticket': ['admin', 'owner', 'arquitecto', 'aprobador', 'member'], 
  '/nueva-funcionalidad': ['admin', 'owner', 'arquitecto', 'aprobador', 'member'], 
  '/aprobaciones': ['admin', 'owner', 'arquitecto', 'aprobador'],
  '/auditoria': ['admin', 'owner', 'arquitecto'], 
  '/auditoria/:numero': ['admin', 'owner', 'arquitecto', 'aprobador'], 
  '/conexiones': ['admin', 'owner'], 
  '/modelos': ['admin', 'owner', 'arquitecto'], 
  '/consumo': ['admin', 'owner', 'arquitecto', 'member'], 
};

export function usePermiso() {
  const { usuario, restringido } = useAuth();
  
  const rolUsuario = (usuario?.rol as Rol) || 'viewer'; 

  const tieneAccesoARuta = (ruta: string) => {
    // Confinamiento estricto. Si está restringido, SÓLO pasa a /consumo
    if (restringido) {
      return ruta === '/consumo';
    }
    const rolesPermitidos = MAPA_RUTAS_ROLES[ruta];
    return rolesPermitidos ? rolesPermitidos.includes(rolUsuario) : false;
  };

  const puedeHacer = (accion: 'firmar-gate' | 'cancelar-ticket' | 'probar-conexion') => {
    const mapaAcciones: Record<string, Rol[]> = {
      'firmar-gate': ['admin', 'owner', 'aprobador'],
      'cancelar-ticket': ['admin', 'owner', 'arquitecto', 'member'],
      'probar-conexion': ['admin', 'owner'],
    };
    return mapaAcciones[accion]?.includes(rolUsuario) ?? false;
  };

  return { tieneAccesoARuta, puedeHacer };
}