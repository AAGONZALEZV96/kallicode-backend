/**
 * Bandeja desplegable de notificaciones (KC-PT-06-1): las más recientes, lectura individual
 * (leída / no leída) y marcado masivo. Se abre desde la campana del Layout y se cierra con
 * Escape (el foco vuelve a la campana), al hacer clic afuera o al abrir una notificación.
 */
import { useEffect, useId, type RefObject } from "react";
import { Link } from "react-router-dom";

import { TAMANO_BANDEJA, useBandeja, useLeerTodas, useMarcarNotificacion } from "../api/consultas";
import type { Notificacion } from "../api/tipos";
import { useI18n } from "../estado/i18n";
import { CajaError, Cargando, fechaCorta } from "./ui";

/** Ícono por tipo (nt_tipo_chk de core.notifications); un tipo nuevo usa el genérico. */
const ICONOS: Record<string, string> = {
  cambio_etapa: "↻",
  gate_pendiente: "⛨",
  ticket_cerrado: "✓",
  aviso_cuota: "◔",
  seguridad: "⚠",
  sistema: "ℹ",
};

interface Props {
  id: string;
  intervaloMs: number;
  /** Contenedor de la campana y la bandeja: un clic fuera de él la cierra. */
  ancla: RefObject<HTMLElement | null>;
  /** `devolverFoco`: con Escape el foco vuelve a la campana. */
  alCerrar: (devolverFoco: boolean) => void;
}

export default function BandejaNotificaciones({ id, intervaloMs, ancla, alCerrar }: Props) {
  const { t } = useI18n();
  const idTitulo = useId();
  const { data: datos, error } = useBandeja({ intervaloMs });
  const marcar = useMarcarNotificacion();
  const leerTodas = useLeerTodas();

  useEffect(() => {
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") alCerrar(true);
    };
    const clic = (e: MouseEvent) => {
      if (e.target instanceof Node && !ancla.current?.contains(e.target)) alCerrar(false);
    };
    document.addEventListener("keydown", tecla);
    document.addEventListener("mousedown", clic);
    return () => {
      document.removeEventListener("keydown", tecla);
      document.removeEventListener("mousedown", clic);
    };
  }, [alCerrar, ancla]);

  function abrir(n: Notificacion) {
    if (!n.leida) marcar.mutate({ id: n.id, leida: true });
    alCerrar(false);
  }

  return (
    <section
      id={id}
      className="bandeja"
      aria-labelledby={idTitulo}
      data-testid="bandeja-notificaciones.panel.contenedor"
    >
      <div className="bandeja-cabecera">
        <h2 id={idTitulo}>{t("notif.titulo")}</h2>
        <button
          type="button"
          className="btn sm"
          disabled={!datos || datos.no_leidas === 0 || leerTodas.isPending}
          onClick={() => leerTodas.mutate()}
          data-testid="bandeja-notificaciones.cabecera.leer-todas"
        >
          {t("notif.leer_todas")}
        </button>
      </div>
      <CajaError error={error ?? marcar.error ?? leerTodas.error} />
      {!datos && !error && <Cargando />}
      {datos?.items.length === 0 && (
        <p className="note" data-testid="bandeja-notificaciones.lista.vacio">
          {t("notif.vacio")}
        </p>
      )}
      {datos && datos.items.length > 0 && (
        <ul className="bandeja-lista">
          {datos.items.map((n) => (
            <li
              key={n.id}
              className={n.leida ? undefined : "no-leida"}
              data-testid="bandeja-notificaciones.lista.item"
            >
              <span className="bandeja-icono" aria-hidden="true">
                {ICONOS[n.tipo] ?? "•"}
              </span>
              <div className="bandeja-texto">
                {/* Sin el número visible del ticket no hay a dónde enlazar (ver tipos.ts). */}
                {n.ticket_numero ? (
                  <Link
                    to={`/tickets/${n.ticket_numero}`}
                    onClick={() => abrir(n)}
                    data-testid="bandeja-notificaciones.lista.abrir"
                  >
                    {n.titulo}
                  </Link>
                ) : (
                  <b>{n.titulo}</b>
                )}
                {n.cuerpo && <p>{n.cuerpo}</p>}
                <span className="bandeja-meta">
                  {!n.leida && <span className="chip gate">{t("notif.nueva")}</span>}
                  {fechaCorta(n.creada_en)}
                </span>
              </div>
              <button
                type="button"
                className="btn sm"
                aria-label={t(n.leida ? "notif.marcar_no_leida" : "notif.marcar_leida", {
                  titulo: n.titulo,
                })}
                disabled={marcar.isPending}
                onClick={() => marcar.mutate({ id: n.id, leida: !n.leida })}
                data-testid="bandeja-notificaciones.lista.alternar"
              >
                {n.leida ? "○" : "✓"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {datos && datos.items.length >= TAMANO_BANDEJA && (
        <p className="note">{t("notif.recientes", { n: String(TAMANO_BANDEJA) })}</p>
      )}
    </section>
  );
}
