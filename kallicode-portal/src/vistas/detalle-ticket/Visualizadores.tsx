/**
 * Visualizadores del ticket (KC-PT-06-2): quién recibe sus notificaciones.
 *
 * Sigue las reglas de tickets.add_watcher y del_watcher:
 * - seguir el ticket: todos menos viewer; dejar de seguirlo: cualquiera que lo siga,
 *   salvo el creador (la base siempre lo deja como visualizador);
 * - agregar a otra persona: quien puede listar usuarios (owner, admin, architect,
 *   approver), con un buscador por nombre o correo;
 * - quitar a otra persona: owner, admin o el creador del ticket;
 * - hasta 25 visualizadores por ticket.
 */
import { useId, useRef, useState } from "react";

import {
  MAX_VISUALIZADORES,
  useAgregarVisualizador,
  useQuitarVisualizador,
  useUsuariosActivos,
} from "../../api/consultas";
import type { TicketDetalle } from "../../api/tipos";
import { CajaError } from "../../componentes/ui";
import { useAuth, usePermiso } from "../../estado/auth";
import { useI18n } from "../../estado/i18n";
import { useDebounce } from "../../useDebounce";

const iniciales = (nombre: string) =>
  nombre
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("");

export default function Visualizadores({ ticket }: { ticket: TicketDetalle }) {
  const { t } = useI18n();
  const { usuario } = useAuth();
  const { puedeHacer } = usePermiso();
  const id = useId();
  const lista = useRef<HTMLUListElement>(null);
  const agregar = useAgregarVisualizador(ticket.id);
  const quitar = useQuitarVisualizador(ticket.id);
  const [eligiendo, setEligiendo] = useState(false);

  const yo = usuario?.id ?? null;
  const creadorId = ticket.reportado_por?.id ?? null;
  const visualizadores = ticket.visualizadores;
  const sigo = yo !== null && visualizadores.some((v) => v.id === yo);
  const lleno = visualizadores.length >= MAX_VISUALIZADORES;
  const ocupado = agregar.isPending || quitar.isPending;
  const puedeSeguir = !sigo && !lleno && puedeHacer({ tipo: "agregar-visualizador" });
  const puedeDejar = sigo && yo !== creadorId;
  const puedeElegir = !lleno && puedeHacer({ tipo: "elegir-visualizador" });
  // Un solo botón para seguir y dejar de seguir: cambia su selector, no su elemento.
  const elementoSeguir = sigo ? "dejar-de-seguir" : "seguir";

  function quitarA(userId: string) {
    // El botón desaparece con la persona: el foco pasa a la lista.
    quitar.mutate(userId, { onSuccess: () => lista.current?.focus() });
  }

  return (
    <div className="card">
      <p className="caption" id={id}>
        {t("td.watchers")} ({visualizadores.length})
      </p>
      <ul
        ref={lista}
        className="visualizadores"
        aria-labelledby={id}
        tabIndex={-1}
        data-testid="detalle-ticket.visualizadores.lista"
      >
        {visualizadores.map((v) => (
          <li key={v.id} className="visualizador" data-testid="detalle-ticket.visualizadores.item">
            <span className="avatar" aria-hidden="true">
              {iniciales(v.nombre)}
            </span>
            <span className="visualizador-nombre">
              {v.nombre}
              {v.id === creadorId && <span className="note-inline"> · {t("vis.creador")}</span>}
              {v.id === yo && <span className="note-inline"> · {t("vis.tu")}</span>}
            </span>
            {v.id !== yo &&
              puedeHacer({ tipo: "quitar-visualizador", visualizadorId: v.id, creadorId }) && (
                <button
                  type="button"
                  className="btn sm"
                  aria-label={t("vis.quitar", { nombre: v.nombre })}
                  disabled={ocupado}
                  onClick={() => quitarA(v.id)}
                  data-testid="detalle-ticket.visualizadores.quitar"
                >
                  ✕
                </button>
              )}
          </li>
        ))}
      </ul>

      <CajaError error={agregar.error ?? quitar.error} />
      {lleno && (
        <p className="ayuda-campo" data-testid="detalle-ticket.visualizadores.limite">
          {t("vis.limite", { n: String(MAX_VISUALIZADORES) })}
        </p>
      )}

      <div className="visualizadores-acciones">
        {/* Un solo botón para seguir y dejar de seguir: al cambiar, el foco se queda. */}
        {(puedeSeguir || puedeDejar) && yo && (
          <button
            type="button"
            className="btn sm"
            // aria-disabled y no disabled: un botón deshabilitado pierde el foco en el navegador.
            aria-disabled={ocupado}
            onClick={() => {
              if (ocupado) return;
              if (sigo) quitar.mutate(yo);
              else agregar.mutate(yo);
            }}
            data-testid={`detalle-ticket.visualizadores.${elementoSeguir}`}
          >
            {sigo ? t("vis.dejar") : t("vis.seguir")}
          </button>
        )}
        {puedeElegir && (
          <button
            type="button"
            className="btn sm"
            aria-expanded={eligiendo}
            aria-controls={`${id}-buscador`}
            onClick={() => setEligiendo(!eligiendo)}
            data-testid="detalle-ticket.visualizadores.agregar"
          >
            {t("vis.agregar")}
          </button>
        )}
      </div>

      {puedeElegir && eligiendo && (
        <Buscador
          id={`${id}-buscador`}
          excluir={new Set(visualizadores.map((v) => v.id))}
          ocupado={ocupado}
          alElegir={(userId, alTerminar) => agregar.mutate(userId, { onSuccess: alTerminar })}
        />
      )}
    </div>
  );
}

interface PropsBuscador {
  id: string;
  /** Quienes ya son visualizadores no se ofrecen. */
  excluir: Set<string>;
  ocupado: boolean;
  alElegir: (userId: string, alTerminar: () => void) => void;
}

/** Busca personas activas de la organización y agrega la elegida. */
function Buscador({ id, excluir, ocupado, alElegir }: PropsBuscador) {
  const { t } = useI18n();
  const entrada = useRef<HTMLInputElement>(null);
  const [texto, setTexto] = useState("");
  const consultado = useDebounce(texto.trim());
  const { data, error, isPending } = useUsuariosActivos(consultado, true);
  const candidatos = (data?.items ?? []).filter((u) => !excluir.has(u.id));

  return (
    <div
      id={id}
      className="visualizadores-buscador"
      data-testid="detalle-ticket.visualizadores.buscador"
    >
      <label htmlFor={`${id}-texto`}>{t("vis.buscar")}</label>
      <input
        ref={entrada}
        id={`${id}-texto`}
        type="search"
        value={texto}
        maxLength={100}
        onChange={(e) => setTexto(e.target.value)}
        data-testid="detalle-ticket.visualizadores.buscar"
      />
      <CajaError error={error} />
      {!isPending && !error && candidatos.length === 0 ? (
        <p className="ayuda-campo" data-testid="detalle-ticket.visualizadores.sin-resultados">
          {t("vis.sin_resultados")}
        </p>
      ) : (
        <ul className="visualizadores-resultados" aria-label={t("vis.resultados")}>
          {candidatos.map((u) => (
            <li key={u.id}>
              <button
                type="button"
                className="btn sm"
                disabled={ocupado}
                aria-label={`${t("vis.agregar_a", { nombre: u.nombre })} (${u.email})`}
                // La persona sale de los resultados: el foco vuelve al buscador.
                onClick={() => alElegir(u.id, () => entrada.current?.focus())}
                data-testid="detalle-ticket.visualizadores.resultado"
              >
                {u.nombre} <span className="note-inline">{u.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
