/**
 * Comprueba el presupuesto del paquete sobre el último `vite build` (KC-PT-11-2).
 * Uso: npm run build && npm run presupuesto. Termina con error si el login o el tablero se pasan.
 */
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { gzipSync } from "node:zlib";

import { kB, medirPaquete, type Manifiesto } from "./presupuesto";

const DIST = resolve("dist");
const MANIFIESTO = join(DIST, ".vite", "manifest.json");

if (!existsSync(MANIFIESTO)) {
  console.error(`No existe ${MANIFIESTO}: corre primero «npm run build».`);
  process.exit(1);
}
const manifiesto: Manifiesto = JSON.parse(readFileSync(MANIFIESTO, "utf8"));
const tamano = (archivo: string) => gzipSync(readFileSync(join(DIST, archivo))).length;

let excedido = false;
for (const m of medirPaquete(manifiesto, tamano)) {
  const ok = m.bytes <= m.tope;
  excedido ||= !ok;
  console.log(
    `${ok ? "✓" : "✗"} ${m.pantalla.padEnd(8)} ${kB(m.bytes).padStart(9)} de ${kB(m.tope)} (gzip, ${m.archivos.length} archivos)`,
  );
  for (const a of m.archivos) console.log(`    ${kB(tamano(a)).padStart(9)}  ${a}`);
}
if (excedido) {
  console.error(
    "\nEl paquete se pasa del presupuesto (KC-PT-11-2). Revisa qué se agregó al arranque.",
  );
  process.exit(1);
}
