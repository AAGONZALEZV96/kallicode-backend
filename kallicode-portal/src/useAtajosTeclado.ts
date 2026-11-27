/**
 * Escucha los atajos de teclado globales (KC-PT-11-6, atajos.ts) mientras hay sesión: navegar
 * (solo a las vistas que el rol puede abrir), ir al buscador de la vista y abrir la ayuda.
 */
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { ESPERA_SECUENCIA_MS, esEditable, resolverTecla, type AccionAtajo } from "./atajos";
import { usePermiso } from "./estado/auth";

export function useAtajosTeclado({
  activos,
  alPedirAyuda,
}: {
  activos: boolean;
  alPedirAyuda: () => void;
}) {
  const nav = useNavigate();
  const { tieneAccesoARuta } = usePermiso();
  const pendiente = useRef<{ tecla: string; desde: number } | null>(null);

  useEffect(() => {
    if (!activos) return;
    function ejecutar(accion: AccionAtajo) {
      if (accion.tipo === "ayuda") alPedirAyuda();
      else if (accion.tipo === "buscar")
        document.querySelector<HTMLInputElement>('main input[type="search"]')?.focus();
      else if (tieneAccesoARuta(accion.ruta)) nav(accion.ruta);
    }
    function tecla(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      if (esEditable(e.target)) return;
      // Con un diálogo abierto, las teclas son del diálogo.
      if (document.querySelector('[aria-modal="true"]')) return;
      const previa = pendiente.current;
      const vigente =
        previa && Date.now() - previa.desde <= ESPERA_SECUENCIA_MS ? previa.tecla : null;
      const { accion, abre } = resolverTecla(e.key, vigente);
      pendiente.current = abre ? { tecla: abre, desde: Date.now() } : null;
      if (!accion) return;
      // «/» no se escribe en el buscador que recibe el foco; «?» no se queda en la página.
      e.preventDefault();
      ejecutar(accion);
    }
    document.addEventListener("keydown", tecla);
    return () => document.removeEventListener("keydown", tecla);
  }, [activos, alPedirAyuda, nav, tieneAccesoARuta]);
}
