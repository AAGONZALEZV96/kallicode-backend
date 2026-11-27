/**
 * Explorador de auditoría (§11.1) con filtros; clic abre el expediente.
 *
 * Los filtros y la página viven en la URL (KC-PT-08-5, cierra PT-F16): se pueden recargar y
 * compartir, y al volver del expediente el listado sigue filtrado. Son los de
 * GET /audit/events: texto, tipo de actor, ticket, etapa, rango de fechas (máximo 92 días) y
 * orden. Los textos llegan a la URL al dejar de escribir (PT-F08).
 */
import { useId, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useEventosAuditoria } from "../api/consultas";
import { atributosDeError } from "../api/erroresCampo";
import {
  ACTORES,
  CLAVES_FILTRO,
  errorDeRango,
  ETAPAS_AUDITORIA,
  hayFiltros,
  ORDENES,
  pedidoExportacion,
  RANGO_MAXIMO_DIAS,
  sanear,
} from "../api/filtrosAuditoria";
import { ErrorCampo } from "../componentes/ErrorCampo";
import Exportacion from "../componentes/Exportacion";
import Paginacion from "../componentes/Paginacion";
import { EsqueletoTabla } from "../componentes/Esqueleto";
import { EstadoVacio } from "../componentes/EstadoVacio";
import { CajaError, EnlaceCelda } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";
import { useFiltrosUrl, useTextoEnUrl } from "../useFiltrosUrl";
import { usePaginaUrl } from "../usePaginaUrl";

const ICONO_ACTOR: Record<string, string> = { humano: "👤", agente: "⚙", sistema: "⚙" };

export default function Auditoria() {
  const fmt = useFormato();
  const { t } = useI18n();
  const nav = useNavigate();
  const { search } = useLocation();
  const id = useId();
  const busqueda = useRef<HTMLInputElement>(null);
  const { puedeHacer } = usePermiso();
  const [page, irAPagina] = usePaginaUrl();
  const [enUrl, cambiar] = useFiltrosUrl(CLAVES_FILTRO);
  const filtros = sanear(enUrl);
  const [q, setQ] = useTextoEnUrl(filtros.q, (q) => cambiar({ q }));
  const [ticket, setTicket] = useTextoEnUrl(filtros.ticket, (ticket) => cambiar({ ticket }));

  const errorRango = errorDeRango(filtros.desde, filtros.hasta);
  const mensajeRango = errorRango && t(`au.error.${errorRango}`, { n: String(RANGO_MAXIMO_DIAS) });
  const idErrorRango = `${id}-rango`;
  // Con un rango inválido no se consulta: el backend respondería RANGO_EXCESIVO.
  const { data: datos, error } = useEventosAuditoria({ ...filtros, page }, !errorRango);
  // El expediente vuelve aquí con los mismos filtros.
  const alExpediente = { volver: search };

  function limpiar() {
    cambiar(Object.fromEntries(CLAVES_FILTRO.map((k) => [k, ""])));
    // El botón desaparece: el foco pasa a la búsqueda para no perderse.
    busqueda.current?.focus();
  }

  return (
    <div className="view" data-testid="auditoria.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("au.title")}</h1>
          <p>{t("au.sub")}</p>
        </div>
      </div>
      {/* Sin rótulos visibles (diseño del wireframe): el nombre accesible va en aria-label. */}
      <div className="card filtros" role="search">
        <input
          ref={busqueda}
          type="search"
          placeholder={t("au.buscar")}
          aria-label={t("au.buscar")}
          value={q}
          maxLength={200}
          onChange={(e) => setQ(e.target.value)}
          data-testid="auditoria.filtros.busqueda"
        />
        <input
          placeholder={t("au.filtro.ticket")}
          aria-label={t("au.filtro.ticket")}
          value={ticket}
          maxLength={40}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => setTicket(e.target.value)}
          data-testid="auditoria.filtros.ticket"
        />
        <select
          className="ancho-auto"
          aria-label={t("au.col.actor")}
          value={filtros.actor}
          onChange={(e) => cambiar({ actor: e.target.value })}
          data-testid="auditoria.filtros.actor"
        >
          <option value="">{t("au.filtro.todos", { campo: t("au.col.actor") })}</option>
          {ACTORES.map((a) => (
            <option key={a} value={a}>
              {t(`au.actor.${a}`)}
            </option>
          ))}
        </select>
        <select
          className="ancho-auto"
          aria-label={t("au.filtro.etapa")}
          value={filtros.etapa}
          onChange={(e) => cambiar({ etapa: e.target.value })}
          data-testid="auditoria.filtros.etapa"
        >
          <option value="">{t("au.filtro.todas", { campo: t("au.filtro.etapa") })}</option>
          {ETAPAS_AUDITORIA.map((e) => (
            <option key={e} value={e}>
              {t(`st.${e}`)}
            </option>
          ))}
        </select>
        <input
          type="date"
          className="ancho-auto"
          aria-label={t("au.filtro.desde")}
          title={t("au.filtro.desde")}
          value={filtros.desde}
          max={filtros.hasta || undefined}
          onChange={(e) => cambiar({ desde: e.target.value })}
          {...atributosDeError(idErrorRango, mensajeRango)}
          data-testid="auditoria.filtros.desde"
        />
        <input
          type="date"
          className="ancho-auto"
          aria-label={t("au.filtro.hasta")}
          title={t("au.filtro.hasta")}
          value={filtros.hasta}
          min={filtros.desde || undefined}
          onChange={(e) => cambiar({ hasta: e.target.value })}
          {...atributosDeError(idErrorRango, mensajeRango)}
          data-testid="auditoria.filtros.hasta"
        />
        <select
          className="ancho-auto"
          aria-label={t("au.filtro.orden")}
          value={filtros.orden}
          onChange={(e) => cambiar({ orden: e.target.value })}
          data-testid="auditoria.filtros.orden"
        >
          <option value="">{t("au.orden.recientes")}</option>
          {Object.keys(ORDENES).map((o) => (
            <option key={o} value={o}>
              {t(`au.orden.${o}`)}
            </option>
          ))}
        </select>
        {hayFiltros(filtros) && (
          <button
            type="button"
            className="btn sm"
            onClick={limpiar}
            data-testid="auditoria.filtros.limpiar"
          >
            {t("au.filtro.limpiar")}
          </button>
        )}
      </div>
      <ErrorCampo id={idErrorRango} error={mensajeRango} testid="auditoria.filtros.error" />
      {/* CSV con los filtros aplicados (KC-PT-08-3): sin el límite de 92 días de la lista. */}
      {puedeHacer({ tipo: "exportar-auditoria" }) && (
        <Exportacion
          vista="auditoria"
          formatos={["csv"]}
          pedido={pedidoExportacion(filtros)}
          bloqueo={errorRango === "invertido" ? mensajeRango : undefined}
          ayuda={t("au.exp.ayuda_filtros")}
        />
      )}
      <CajaError error={error} />
      {errorRango ? null : !datos ? (
        <EsqueletoTabla columnas={6} />
      ) : (
        <>
          <div className="card p-0">
            <table className="kc" data-testid="auditoria.lista.tabla">
              <thead>
                <tr>
                  <th>{t("au.col.fecha")}</th>
                  <th>{t("au.col.ticket")}</th>
                  <th>{t("au.col.actor")}</th>
                  <th>{t("au.col.evento")}</th>
                  <th>{t("au.col.modelo")}</th>
                  <th>{t("au.col.sello")}</th>
                </tr>
              </thead>
              <tbody>
                {datos.items.map((e) => (
                  <tr
                    key={e.id}
                    className={e.ticket_id ? "click" : ""}
                    onClick={() =>
                      e.ticket_id && nav(`/auditoria/${e.ticket_id}`, { state: alExpediente })
                    }
                    data-testid="auditoria.lista.fila"
                  >
                    <td>{fmt.fecha(e.fecha)}</td>
                    <td>
                      {e.ticket_id ? (
                        <EnlaceCelda to={`/auditoria/${e.ticket_id}`} state={alExpediente}>
                          {e.ticket_id}
                        </EnlaceCelda>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <span aria-hidden="true">{ICONO_ACTOR[e.actor_tipo] ?? "⚙"} </span>
                      {e.actor_id ?? t("au.actor.sistema")}
                    </td>
                    <td>{e.evento}</td>
                    <td className="mono fs-11">{e.modelo ?? "—"}</td>
                    <td className="mono fs-11">
                      {e.sello.slice(0, 4)}…{e.sello.slice(-4)} ✓
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {datos.items.length === 0 && (
              <EstadoVacio
                testid="auditoria.lista.vacio"
                titulo={t("au.sin_resultados")}
                descripcion={t("vacio.auditoria.desc")}
              />
            )}
          </div>
          {/* El tamaño de página sale de la respuesta: antes estaba fijo en 50 (PT-F16). */}
          <Paginacion
            pagina={page}
            tamano={datos.page_size}
            total={datos.total}
            enPagina={datos.items.length}
            alCambiar={irAPagina}
            vista="auditoria"
          />
          <p className="note" data-testid="auditoria.cadena.resumen">
            {t("au.eventos_hoy", { n: String(datos.cadena.eventos_hoy) })}
          </p>
        </>
      )}
    </div>
  );
}
