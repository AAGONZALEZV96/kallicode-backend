/**
 * Contexto de sesión: login/logout, usuario actual y expiración automática.
 * Como los tokens, el usuario vive solo en memoria (KC-PT-02-2): al recargar no hay sesión.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, alExpirarSesion, fijarSesion, limpiarSesion } from "../api/client";
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
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const [restringido, setRestringido] = useState(false);
  const [cargando, setCargando] = useState(false);

  useEffect(() => {
    alExpirarSesion(() => setUsuario(null));
  }, []);

  async function login(email: string, password: string) {
    setCargando(true);
    try {
      const s = await api.post<Sesion>("/api/v1/auth/login", { email, password });
      fijarSesion(s.access_token, s.refresh_token);
      setUsuario(s.usuario);
      setRestringido(s.acceso_restringido);
    } finally {
      setCargando(false);
    }
  }

  function logout() {
    limpiarSesion();
    setUsuario(null);
  }

  return (
    <Ctx.Provider value={{ usuario, restringido, cargando, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAuth = () => useContext(Ctx);
