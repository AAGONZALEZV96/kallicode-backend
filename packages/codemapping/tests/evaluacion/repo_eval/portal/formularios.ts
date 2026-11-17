// Validación y envío de los formularios del portal.

import { ClienteApi } from "./api";
import { redondear } from "./formato";

export function validar(campo: string): boolean {
  return campo.trim().length > 0;
}

export function validarImporte(valor: string): number {
  if (!validar(valor)) {
    throw new Error("importe obligatorio");
  }
  return redondear(Number(valor));
}

export function enviarPago(api: ClienteApi, facturaId: number, valor: string): Promise<unknown> {
  return api.pagarFactura(facturaId, validarImporte(valor));
}
