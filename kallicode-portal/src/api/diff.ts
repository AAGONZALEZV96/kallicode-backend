/**
 * Diff unificado de un archivo del expediente (KC-PT-08-1): sus líneas con numeración y la
 * verificación de su SHA-256 contra el que registró el backend (KC-BE-08-6).
 */

export type TipoLinea = "cabecera" | "bloque" | "contexto" | "agregada" | "eliminada" | "aviso";

export interface LineaDiff {
  tipo: TipoLinea;
  /** El texto sin el signo inicial (+, - o espacio); entero en cabeceras, bloques y avisos. */
  texto: string;
  /** Número de línea en la versión anterior y en la nueva (null si no aplica). */
  antes: number | null;
  despues: number | null;
}

export interface Diff {
  lineas: LineaDiff[];
  agregadas: number;
  eliminadas: number;
}

const BLOQUE = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Interpreta un diff unificado (git diff / diff -u). Lo que no entiende lo deja como cabecera. */
export function parsearDiff(diff: string): Diff {
  const crudas = diff.split("\n");
  // El salto de línea final no es una línea más.
  if (crudas.at(-1) === "") crudas.pop();
  const lineas: LineaDiff[] = [];
  let antes = 0;
  let despues = 0;
  let enBloque = false;
  let agregadas = 0;
  let eliminadas = 0;

  for (const [i, cruda] of crudas.entries()) {
    const bloque = BLOQUE.exec(cruda);
    // Otro archivo dentro del mismo diff: «--- a/x» seguido de «+++ b/x» no es una línea borrada.
    const otroArchivo = cruda.startsWith("--- ") && crudas[i + 1]?.startsWith("+++ ");
    if (bloque) {
      antes = Number(bloque[1]);
      despues = Number(bloque[2]);
      enBloque = true;
      lineas.push({ tipo: "bloque", texto: cruda, antes: null, despues: null });
    } else if (!enBloque || otroArchivo || cruda.startsWith("diff --git ")) {
      enBloque = false;
      lineas.push({ tipo: "cabecera", texto: cruda, antes: null, despues: null });
    } else if (cruda.startsWith("\\")) {
      // «\ No newline at end of file»
      lineas.push({ tipo: "aviso", texto: cruda, antes: null, despues: null });
    } else if (cruda.startsWith("+")) {
      agregadas++;
      lineas.push({ tipo: "agregada", texto: cruda.slice(1), antes: null, despues: despues++ });
    } else if (cruda.startsWith("-")) {
      eliminadas++;
      lineas.push({ tipo: "eliminada", texto: cruda.slice(1), antes: antes++, despues: null });
    } else {
      lineas.push({ tipo: "contexto", texto: cruda.slice(1), antes: antes++, despues: despues++ });
    }
  }
  return { lineas, agregadas, eliminadas };
}

export type Verificacion = "coincide" | "no-coincide" | "no-disponible";

/**
 * Compara el SHA-256 del diff (UTF-8, como lo calcula el backend) con el registrado. Sin Web
 * Crypto (contexto no seguro) no se puede comprobar: «no-disponible», nunca «coincide».
 */
export async function verificarDiff(diff: string, hashEsperado: string): Promise<Verificacion> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return "no-disponible";
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(diff));
  const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  return hex === hashEsperado ? "coincide" : "no-coincide";
}
