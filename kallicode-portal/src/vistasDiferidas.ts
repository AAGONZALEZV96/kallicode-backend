/**
 * Las vistas del portal se descargan al entrar en su ruta (KC-PT-11-1, PT-F20): el paquete
 * inicial lleva solo el login, la sesión y el enrutado. Cada import() es un archivo aparte.
 */
import { cargaDiferida } from "./cargaDiferida";

const IMPORTAR = {
  Layout: () => import("./componentes/Layout"),
  Tablero: () => import("./vistas/Tablero"),
  Tickets: () => import("./vistas/Tickets"),
  DetalleTicket: () => import("./vistas/DetalleTicket"),
  NuevoTicket: () => import("./vistas/NuevoTicket"),
  NuevaFuncionalidad: () => import("./vistas/NuevaFuncionalidad"),
  Aprobaciones: () => import("./vistas/Aprobaciones"),
  Auditoria: () => import("./vistas/Auditoria"),
  Expediente: () => import("./vistas/Expediente"),
  Onboarding: () => import("./vistas/Onboarding"),
  Conexiones: () => import("./vistas/Conexiones"),
  ModelosIA: () => import("./vistas/ModelosIA"),
  Consumo: () => import("./vistas/Consumo"),
  Perfil: () => import("./vistas/Perfil"),
  Organizacion: () => import("./vistas/Organizacion"),
  Equipo: () => import("./vistas/Equipo"),
  RecuperarClave: () => import("./vistas/RecuperarClave"),
  RestablecerClave: () => import("./vistas/RestablecerClave"),
  AceptarInvitacion: () => import("./vistas/AceptarInvitacion"),
};

export const Layout = cargaDiferida(IMPORTAR.Layout);
export const Tablero = cargaDiferida(IMPORTAR.Tablero);
export const Tickets = cargaDiferida(IMPORTAR.Tickets);
export const DetalleTicket = cargaDiferida(IMPORTAR.DetalleTicket);
export const NuevoTicket = cargaDiferida(IMPORTAR.NuevoTicket);
export const NuevaFuncionalidad = cargaDiferida(IMPORTAR.NuevaFuncionalidad);
export const Aprobaciones = cargaDiferida(IMPORTAR.Aprobaciones);
export const Auditoria = cargaDiferida(IMPORTAR.Auditoria);
export const Expediente = cargaDiferida(IMPORTAR.Expediente);
export const Onboarding = cargaDiferida(IMPORTAR.Onboarding);
export const Conexiones = cargaDiferida(IMPORTAR.Conexiones);
export const ModelosIA = cargaDiferida(IMPORTAR.ModelosIA);
export const Consumo = cargaDiferida(IMPORTAR.Consumo);
export const Perfil = cargaDiferida(IMPORTAR.Perfil);
export const Organizacion = cargaDiferida(IMPORTAR.Organizacion);
export const Equipo = cargaDiferida(IMPORTAR.Equipo);
export const RecuperarClave = cargaDiferida(IMPORTAR.RecuperarClave);
export const RestablecerClave = cargaDiferida(IMPORTAR.RestablecerClave);
export const AceptarInvitacion = cargaDiferida(IMPORTAR.AceptarInvitacion);

/**
 * Lo que viene después del login (el layout y el tablero): se descarga mientras la persona
 * escribe su correo, para que entrar no espere otra descarga.
 */
export function precargarInicio() {
  return Promise.all([IMPORTAR.Layout(), IMPORTAR.Tablero()]);
}

/** Todas las vistas; las pruebas las cargan una vez al comenzar para no esperar en cada una. */
export function precargarTodas() {
  return Promise.all(Object.values(IMPORTAR).map((importar) => importar()));
}
