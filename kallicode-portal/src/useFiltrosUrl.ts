/**
 * Filtros de un listado guardados en la URL (KC-PT-08-5, cierra PT-F16), igual que la página
 * en usePaginaUrl: `?etapa=build&desde=2026-11-01` se puede compartir o recargar, y al volver
 * de un detalle el listado sigue filtrado.
 *
 * - Cambiar un filtro vuelve a la página 1 y reemplaza la entrada del historial: «atrás» no
 *   pasa por cada filtro intermedio.
 * - Un filtro vacío no se escribe: la URL queda limpia.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { registroDe } from "./tipado";
import { useDebounce } from "./useDebounce";

export function useFiltrosUrl<K extends string>(claves: readonly K[], parametroPagina = "page") {
  const [params, setParams] = useSearchParams();
  const valores = registroDe(claves, (k) => params.get(k) ?? "");

  const cambiar = useCallback(
    (cambios: Partial<Record<K, string>>) =>
      setParams(
        (actuales) => {
          const siguientes = new URLSearchParams(actuales);
          for (const clave of claves) {
            if (!(clave in cambios)) continue;
            const valor = cambios[clave];
            if (valor) siguientes.set(clave, valor);
            else siguientes.delete(clave);
          }
          siguientes.delete(parametroPagina);
          return siguientes;
        },
        { replace: true },
      ),
    [setParams, parametroPagina, claves],
  );

  return [valores, cambiar] as const;
}

/**
 * Campo de texto de un filtro en la URL: se escribe libre y llega a la URL al dejar de
 * teclear (useDebounce, PT-F08). Si la URL cambia por otro lado (atrás, un enlace, «Limpiar»),
 * el campo la sigue.
 */
export function useTextoEnUrl(valorUrl: string, alConfirmar: (valor: string) => void) {
  const [texto, setTexto] = useState(valorUrl);
  const diferido = useDebounce(texto.trim());

  // URL → campo, solo cuando la URL cambió por otro lado.
  const ultimoUrl = useRef(valorUrl);
  useEffect(() => {
    if (valorUrl === ultimoUrl.current) return;
    ultimoUrl.current = valorUrl;
    setTexto(valorUrl);
  }, [valorUrl]);

  // Campo → URL, solo cuando cambió lo escrito (no cuando cambió la URL).
  const escrito = useRef(diferido);
  useEffect(() => {
    if (diferido === escrito.current) return;
    escrito.current = diferido;
    if (diferido === valorUrl) return;
    ultimoUrl.current = diferido;
    alConfirmar(diferido);
  }, [diferido, valorUrl, alConfirmar]);

  return [texto, setTexto] as const;
}
