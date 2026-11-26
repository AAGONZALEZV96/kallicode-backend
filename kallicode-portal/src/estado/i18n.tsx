/**
 * i18n del portal: es (base), en y pt, con las claves del wireframe.
 *
 * Un diccionario por idioma (KC-PT-11-1): el español va en el paquete inicial y es el respaldo
 * de cualquier clave; el inglés y el portugués se descargan solo cuando se eligen. main.tsx
 * descarga el idioma guardado antes de pintar, para que no se vea un instante en español.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { esUnoDe } from "../tipado";
import es from "./diccionarios/es";

export type Idioma = "es" | "en" | "pt";

const IDIOMAS: Idioma[] = ["es", "en", "pt"];

/** Diccionarios ya descargados (el español, siempre). */
const cargados: Partial<Record<Idioma, Record<string, string>>> = { es };

const DESCARGAS: Record<
  Exclude<Idioma, "es">,
  () => Promise<{ default: Record<string, string> }>
> = {
  en: () => import("./diccionarios/en"),
  pt: () => import("./diccionarios/pt"),
};

/** ¿Es uno de los idiomas del portal? (un <select>, el backend o localStorage). */
export const esIdioma = (valor: string | null | undefined): valor is Idioma =>
  esUnoDe(IDIOMAS, valor);

/** El idioma guardado en este navegador; uno desconocido vuelve al español. */
export function idiomaGuardado(): Idioma {
  const guardado = localStorage.getItem("kc_idioma");
  return esIdioma(guardado) ? guardado : "es";
}

/** Descarga el diccionario de un idioma, una sola vez. */
export async function cargarIdioma(idioma: Idioma): Promise<void> {
  if (idioma === "es" || cargados[idioma]) return;
  cargados[idioma] = (await DESCARGAS[idioma]()).default;
}

function interpolar(texto: string, valores?: Record<string, string>): string {
  if (!valores) return texto;
  return texto.replace(/\{(\w+)\}/g, (marca, nombre: string) => valores[nombre] ?? marca);
}

interface I18nCtx {
  idioma: Idioma;
  /** `valores` reemplaza los marcadores {nombre} del texto. */
  t: (clave: string, valores?: Record<string, string>) => string;
  cambiarIdioma: (i: Idioma) => void;
}

const Ctx = createContext<I18nCtx>({ idioma: "es", t: (k) => k, cambiarIdioma: () => {} });

export function I18nProvider({ children }: { children: ReactNode }) {
  // El idioma que se muestra: solo cambia cuando su diccionario ya está descargado.
  const [idioma, setIdioma] = useState<Idioma>(() => {
    const guardado = idiomaGuardado();
    return cargados[guardado] ? guardado : "es";
  });
  // Si se elige otro idioma antes de que llegue el anterior, gana el último elegido.
  const pedido = useRef<Idioma>(idioma);

  const cambiarIdioma = useCallback((i: Idioma) => {
    pedido.current = i;
    // Ya descargado: el cambio es inmediato (los formatos y textos cambian en el mismo clic).
    if (cargados[i]) {
      localStorage.setItem("kc_idioma", i);
      setIdioma(i);
      return;
    }
    // Mientras se descarga se sigue viendo el idioma anterior; si la descarga falla, también.
    cargarIdioma(i).then(
      () => {
        if (pedido.current !== i) return;
        localStorage.setItem("kc_idioma", i);
        setIdioma(i);
      },
      () => {},
    );
  }, []);

  // El idioma guardado que main.tsx no alcanzó a descargar (p. ej. si la descarga falló).
  useEffect(() => {
    const guardado = idiomaGuardado();
    if (!cargados[guardado]) cambiarIdioma(guardado);
  }, [cambiarIdioma]);

  const t = useCallback(
    (clave: string, valores?: Record<string, string>) =>
      interpolar(cargados[idioma]?.[clave] ?? es[clave] ?? clave, valores),
    [idioma],
  );

  // El lector de pantalla pronuncia según el lang del documento (KC-PT-05-4, PT-F12).
  useEffect(() => {
    document.documentElement.lang = idioma;
  }, [idioma]);

  const valor = useMemo(() => ({ idioma, t, cambiarIdioma }), [idioma, t, cambiarIdioma]);
  return <Ctx.Provider value={valor}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
