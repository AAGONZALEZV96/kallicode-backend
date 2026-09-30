/** Contexto de sesión: login/logout, usuario actual y expiración automática. */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, alExpirarSesion, fijarSesion, haySesion, limpiarSesion } from "../api/client";
import type { Sesion, Usuario } from "../api/tipos";

interface AuthCtx {
  usuario: Usuario | null;
  restringido: boolean;
  cargando: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [usuario, setUsuario] = useState<Usuario | null>(() => {
    const crudo = sessionStorage.getItem("kc_usuario");
    return crudo ? (JSON.parse(crudo) as Usuario) : null;
  });
  const [restringido, setRestringido] = useState(false);
  const [cargando, setCargando] = useState(false);

  useEffect(() => {
    alExpirarSesion(() => {
      setUsuario(null);
      sessionStorage.removeItem("kc_usuario");
    });
    if (!haySesion()) setUsuario(null);
  }, []);

  async function login(email: string, password: string) {
    setCargando(true);
    try {
      const s = await api.post<Sesion>("/api/v1/auth/login", { email, password });
      fijarSesion(s.access_token, s.refresh_token);
      sessionStorage.setItem("kc_usuario", JSON.stringify(s.usuario));
      setUsuario(s.usuario);
      setRestringido(s.acceso_restringido);
    } finally {
      setCargando(false);
    }
  }

  function logout() {
    limpiarSesion();
    sessionStorage.removeItem("kc_usuario");
    setUsuario(null);
  }

  return (
    <Ctx.Provider value={{ usuario, restringido, cargando, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAuth = () => useContext(Ctx);
