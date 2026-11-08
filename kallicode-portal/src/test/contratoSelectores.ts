/**
 * Contrato de selectores con Testing (KC-PT-05-5): contratos/selectores-portal-v1.json.
 * Se valida con Zod al leerlo, así un campo mal escrito falla aquí y no en Testing.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { z } from "zod";

import { RolEsquema } from "../api/tipos";

// Vitest corre desde kallicode-portal/.
export const RUTA_CONTRATO = "../contratos/selectores-portal-v1.json";

const SelectorEsquema = z.strictObject({
  testid: z.string(),
  // Mismos tipos que SelectorKind de packages/testing (inventory_client.py).
  tipo: z.enum(["button", "input", "table", "card", "link", "modal"]),
  accion: z.enum(["click", "escribir", "seleccionar", "subir", "soltar", "verificar"]),
  descripcion: z.string().min(1),
  condicion: z.string().min(1).optional(),
  /** Un elemento por fila o ítem: el mismo testid aparece varias veces. */
  repetido: z.literal(true).optional(),
});

const VistaEsquema = z.strictObject({
  vista: z.string(),
  ruta: z.string(),
  roles: z.array(RolEsquema),
  descripcion: z.string().min(1),
  selectores: z.array(SelectorEsquema).min(1),
});

const ContratoEsquema = z.strictObject({
  $comment: z.string(),
  version: z.literal(1),
  convencion: z.literal("vista.zona.elemento"),
  formato: z.string(),
  tipos: z.array(z.string()),
  acciones: z.array(z.string()),
  vistas: z.array(VistaEsquema).min(1),
});

export type ContratoSelectores = z.infer<typeof ContratoEsquema>;

export function leerContrato(): ContratoSelectores {
  return ContratoEsquema.parse(JSON.parse(readFileSync(resolve(RUTA_CONTRATO), "utf-8")));
}

/** Todos los testid del contrato. */
export const testidsDelContrato = (contrato = leerContrato()) =>
  new Set(contrato.vistas.flatMap((v) => v.selectores.map((s) => s.testid)));
