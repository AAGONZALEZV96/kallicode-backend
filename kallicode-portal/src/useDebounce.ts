/**
 * Debounce de valores para buscadores y previsualizaciones (KC-PT-04-3, cierra PT-F08).
 *
 * Devuelve `valor` solo cuando deja de cambiar durante `esperaMs`: mientras se escribe,
 * la consulta que depende de él no se dispara. Si al estabilizarse queda en vuelo la
 * petición del valor anterior, TanStack Query la cancela con su AbortSignal (KC-PT-04-1).
 *
 * Se llama useDebounce y no usarDebounce: las reglas de hooks exigen el prefijo «use».
 */
import { useEffect, useState } from "react";

/** Pausa al escribir que se considera «terminó de teclear». */
export const ESPERA_DEBOUNCE_MS = 350;

export function useDebounce<T>(valor: T, esperaMs = ESPERA_DEBOUNCE_MS): T {
  const [estable, setEstable] = useState(valor);
  useEffect(() => {
    const temporizador = setTimeout(() => setEstable(valor), esperaMs);
    return () => clearTimeout(temporizador);
  }, [valor, esperaMs]);
  return estable;
}
