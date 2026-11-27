/**
 * Borrador de un formulario (KC-PT-11-6, borradores.ts). Sin sesión no hace nada.
 *
 * - useBorrador: el que había al abrir la vista (con eso arranca el formulario) y cómo
 *   borrarlo al enviar o descartar.
 * - useGuardarBorrador: guarda el formulario cada vez que cambia; uno vacío no deja borrador.
 */
import { useCallback, useEffect, useState } from "react";
import type { ZodType } from "zod";

import { borrarBorrador, guardarBorrador, leerBorrador } from "./borradores";
import { useUsuarioOpcional } from "./estado/auth";

export function useBorrador<T>(formulario: string, esquema: ZodType<T>) {
  const usuario = useUsuarioOpcional();
  // Se lee una sola vez, al abrir.
  const [recuperado] = useState(() =>
    usuario ? leerBorrador(usuario, formulario, esquema) : null,
  );
  const borrar = useCallback(() => {
    if (usuario) borrarBorrador(usuario, formulario);
  }, [usuario, formulario]);
  return { recuperado, borrar };
}

export function useGuardarBorrador(formulario: string, datos: unknown, vacio: boolean) {
  const usuario = useUsuarioOpcional();
  const id = usuario?.id;
  const org = usuario?.org_id;
  // Se compara el contenido, no la referencia: un objeto nuevo con lo mismo no vuelve a guardar.
  const serializado = JSON.stringify(datos);
  useEffect(() => {
    if (!id || !org) return;
    const dueno = { id, org_id: org };
    if (vacio) borrarBorrador(dueno, formulario);
    else guardarBorrador(dueno, formulario, JSON.parse(serializado));
  }, [id, org, formulario, serializado, vacio]);
}
