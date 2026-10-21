/**
 * Página actual de un listado, guardada en la URL (KC-PT-04-5, cierra PT-F16).
 *
 * `?page=3` se puede compartir o recargar, y «atrás» del navegador vuelve a la página
 * anterior. Un valor ausente o inválido en la URL equivale a la página 1.
 */
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export interface OpcionesIrAPagina {
  /**
   * Reemplaza la entrada del historial en vez de agregar una: para volver a la página 1
   * al cambiar un filtro, donde «atrás» no debe pasar por cada filtro intermedio.
   */
  reemplazar?: boolean;
}

export function usePaginaUrl(parametro = "page") {
  const [params, setParams] = useSearchParams();
  const leida = Number(params.get(parametro));
  const pagina = Number.isInteger(leida) && leida >= 1 ? leida : 1;

  const irAPagina = useCallback(
    (nueva: number, { reemplazar = false }: OpcionesIrAPagina = {}) =>
      setParams(
        (actuales) => {
          const siguientes = new URLSearchParams(actuales);
          // La página 1 no se escribe: la URL del listado queda limpia.
          if (nueva > 1) siguientes.set(parametro, String(nueva));
          else siguientes.delete(parametro);
          return siguientes;
        },
        { replace: reemplazar },
      ),
    [setParams, parametro],
  );

  return [pagina, irAPagina] as const;
}
