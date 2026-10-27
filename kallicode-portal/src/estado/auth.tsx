/**
 * Contexto de sesión: login/logout, usuario actual y expiración automática.
 * Como los tokens, el usuario vive solo en memoria (KC-PT-02-2): al recargar no hay sesión.
 *
 * El perfil se verifica contra GET /users/me al iniciar sesión y tras cada renovación de
 * tokens (KC-PT-02-5, cierra PT-F21): el backend aplica al instante un cambio de rol, y
 * sin esto la interfaz seguiría mostrando lo que permitía el rol anterior.
 *
 * useAuth() exige estar dentro de <AuthProvider> (KC-PT-03-4, cierra PT-F19): fuera de él
 * falla al instante con un mensaje claro, en vez de entregar un contexto vacío disfrazado.
 */
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, alExpirarSesion, alRenovarSesion, cerrarSesion, fijarSesion } from "../api/client";
import {
  PerfilPropioEsquema,
  SesionEsquema,
  type LoginIn,
  type PerfilPropio,
  type Rol,
  type Usuario,
} from "../api/tipos";

interface AuthCtx {
  usuario: Usuario | null;
  restringido: boolean;
  cargando: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

const pedirPerfil = () => api.get("/api/v1/users/me", { esquema: PerfilPropioEsquema });

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
  const consultas = useQueryClient();

  // Sin sesión (logout o expiración) se descarta la caché de servidor (KC-PT-04-1): quien
  // entre después en este navegador no debe ver, ni por un instante, datos de la anterior.
  useEffect(() => {
    if (!usuario) consultas.clear();
  }, [usuario, consultas]);

  useEffect(() => {
    alExpirarSesion(() => {
      setUsuario(null);
      setRestringido(false);
    });
    alRenovarSesion(({ restringido }) => {
      // La suspensión puede empezar o terminar durante la sesión: la informa cada refresh.
      setRestringido(restringido);
      // Con la suscripción suspendida el backend no atiende /users/me (solo Consumo y plan).
      if (restringido) return;
      pedirPerfil()
        .then((perfil) => setUsuario((u) => (u ? conPerfil(u, perfil) : u)))
        // Un 401 ya lo gestiona el cliente; otro fallo no debe tumbar una sesión válida.
        .catch(() => {});
    });
    return () => alRenovarSesion(null);
  }, []);

  async function login(email: string, password: string) {
    setCargando(true);
    try {
      const credenciales: LoginIn = { email, password };
      const s = await api.post("/api/v1/auth/login", credenciales, { esquema: SesionEsquema });
      fijarSesion(s.access_token, s.refresh_token);
      if (s.acceso_restringido) {
        // El backend responde 403 en /users/me a una cuenta suspendida (PT-F07): se entra
        // con el perfil del login, confinado a Consumo.
        setUsuario(s.usuario);
        setRestringido(true);
        return;
      }
      let perfil: PerfilPropio;
      try {
        perfil = await pedirPerfil();
      } catch (e) {
        // Sin perfil verificado no se entra: se revoca la sesión recién abierta.
        await cerrarSesion();
        throw e;
      }
      setUsuario(conPerfil(s.usuario, perfil));
      setRestringido(false);
    } finally {
      setCargando(false);
    }
  }

  function logout() {
    setUsuario(null);
    setRestringido(false);
    // La revocación en el servidor sigue en segundo plano: la UI no la espera.
    void cerrarSesion();
  }

  return (
    <Ctx.Provider value={{ usuario, restringido, cargando, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth() debe usarse dentro de <AuthProvider>.");
  return ctx;
}

// --- CONTROL DE ACCESO (RBAC) ---

/**
 * Roles que pueden abrir cada ruta. Los nombres son los del backend
 * (core.users.rol: owner, admin, architect, approver, member, viewer); un nombre
 * distinto deja al rol sin acceso sin que nada avise.
 */
export const MAPA_RUTAS_ROLES: Record<string, Rol[]> = {
  "/": ["admin", "owner", "architect", "approver", "member", "viewer"],
  "/tickets": ["admin", "owner", "architect", "approver", "member", "viewer"],
  "/tickets/:numero": ["admin", "owner", "architect", "approver", "member", "viewer"],
  "/nuevo-ticket": ["admin", "owner", "architect", "approver", "member"],
  "/nueva-funcionalidad": ["admin", "owner", "architect", "approver", "member"],
  "/aprobaciones": ["admin", "owner", "architect", "approver"],
  "/auditoria": ["admin", "owner", "architect"],
  "/auditoria/:numero": ["admin", "owner", "architect", "approver"],
  "/conexiones": ["admin", "owner"],
  "/modelos": ["admin", "owner", "architect"],
  "/consumo": ["admin", "owner", "architect", "member"],
};

/**
 * Controles de las vistas que el backend restringe (KC-PT-03-2). Algunos dependen del
 * contexto además del rol: el número de gate o quién creó el ticket.
 */
export type Accion =
  | { tipo: "firmar-gate"; gate: number }
  | { tipo: "cancelar-ticket"; creadorId: string | null }
  | { tipo: "probar-conexion" }
  | { tipo: "encender-fabrica" }
  | { tipo: "solicitar-upgrade" };

/** kallicode_core.seguridad.ROLES_FIRMA_G12; el Gate 3 es solo de architect (ROLES_FIRMA_G3). */
const ROLES_FIRMA_G12: Rol[] = ["owner", "admin", "architect", "approver"];
const ADMINISTRAN: Rol[] = ["owner", "admin"];

/**
 * Lo que exige el backend para cada acción. Con la suscripción suspendida el backend solo
 * atiende Consumo y plan (deps.usuario_actual), así que solo queda pedir un upgrade.
 */
export function permiteAccion(
  usuario: Pick<Usuario, "id" | "rol"> | null,
  accion: Accion,
  restringido = false,
): boolean {
  if (!usuario) return false;
  if (restringido && accion.tipo !== "solicitar-upgrade") return false;
  const { rol } = usuario;
  switch (accion.tipo) {
    case "firmar-gate": // gates.py
      return accion.gate === 3 ? rol === "architect" : ROLES_FIRMA_G12.includes(rol);
    case "cancelar-ticket": // tickets.actualizar: el creador o un administrador
      return (
        ADMINISTRAN.includes(rol) || (accion.creadorId !== null && accion.creadorId === usuario.id)
      );
    case "probar-conexion": // POST /connections/{id}/test
    case "solicitar-upgrade": // POST /plans/upgrade-request
      return ADMINISTRAN.includes(rol);
    case "encender-fabrica": // POST /onboarding/complete
      return rol === "owner";
  }
}

/** Una ruta fuera del mapa no da acceso. */
export function puedeVerRuta(rol: Rol, ruta: string): boolean {
  return MAPA_RUTAS_ROLES[ruta]?.includes(rol) ?? false;
}

/** Sin usuario cargado se asume el rol con menos permisos. */
export const rolActual = (usuario: Usuario | null): Rol => usuario?.rol ?? "viewer";

/**
 * Permisos del usuario actual: rutas (PT-F06, PT-F07) y controles de vista (KC-PT-03-2).
 * Se llama usePermiso y no usarPermiso: las reglas de hooks de React exigen el prefijo «use».
 */
export function usePermiso() {
  const { usuario, restringido } = useAuth();
  const rol = rolActual(usuario);
  return {
    // Confinamiento estricto (PT-F07): con la suscripción suspendida solo queda /consumo.
    tieneAccesoARuta: (ruta: string) =>
      restringido ? ruta === "/consumo" : puedeVerRuta(rol, ruta),
    puedeHacer: (accion: Accion) => permiteAccion(usuario, accion, restringido),
  };
}
