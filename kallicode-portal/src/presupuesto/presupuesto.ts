/**
 * Presupuesto del paquete inicial (KC-PT-11-2, PT-F20): lo que descarga el navegador para
 * mostrar el login y para mostrar el tablero, comprimido con gzip, no puede pasar de estos topes.
 *
 * Se calcula sobre el manifiesto que escribe `vite build` (dist/.vite/manifest.json): el login
 * es la entrada index.html con todo lo que importa de forma estática (JS y CSS); el tablero suma
 * el layout y la vista Tablero, que se descargan al entrar (KC-PT-11-1), con lo que importan.
 * Los diccionarios en/pt y las demás vistas no cuentan: se descargan solo si se usan.
 */

/** Topes en bytes comprimidos (kB = 1000 bytes, como los informa `vite build`). */
export const PRESUPUESTOS = { login: 180_000, tablero: 280_000 } as const;

/** Lo que el tablero agrega al login (claves del manifiesto: rutas de los archivos fuente). */
export const ENTRADAS_TABLERO = ["src/componentes/Layout.tsx", "src/vistas/Tablero.tsx"];

/** Una entrada de dist/.vite/manifest.json (solo los campos que se usan aquí). */
export interface EntradaManifiesto {
  file: string;
  css?: string[];
  imports?: string[];
  isEntry?: boolean;
}
export type Manifiesto = Record<string, EntradaManifiesto>;

/** Archivos que descarga una entrada: su JS, su CSS y, recursivamente, sus imports estáticos. */
export function cierre(
  manifiesto: Manifiesto,
  clave: string,
  vistos = new Set<string>(),
): Set<string> {
  const entrada = manifiesto[clave];
  if (!entrada)
    throw new Error(`El manifiesto no tiene «${clave}»: ¿cambió el nombre del archivo?`);
  if (vistos.has(clave)) return new Set();
  vistos.add(clave);
  const archivos = new Set([entrada.file, ...(entrada.css ?? [])]);
  for (const importado of entrada.imports ?? [])
    for (const archivo of cierre(manifiesto, importado, vistos)) archivos.add(archivo);
  return archivos;
}

export interface Medicion {
  pantalla: keyof typeof PRESUPUESTOS;
  bytes: number;
  tope: number;
  archivos: string[];
}

/**
 * Mide el login y el tablero. `tamano` devuelve los bytes comprimidos de un archivo de dist
 * (el index.html también se descarga y suma).
 */
export function medirPaquete(
  manifiesto: Manifiesto,
  tamano: (archivo: string) => number,
): Medicion[] {
  const login = new Set(["index.html", ...cierre(manifiesto, "index.html")]);
  const tablero = new Set(login);
  for (const clave of ENTRADAS_TABLERO) for (const a of cierre(manifiesto, clave)) tablero.add(a);
  const medir = (pantalla: Medicion["pantalla"], archivos: Set<string>): Medicion => ({
    pantalla,
    archivos: [...archivos].sort(),
    bytes: [...archivos].reduce((total, a) => total + tamano(a), 0),
    tope: PRESUPUESTOS[pantalla],
  });
  return [medir("login", login), medir("tablero", tablero)];
}

export const kB = (bytes: number) => `${(bytes / 1000).toFixed(1)} kB`;
