/** Listado de tickets (§9.1) con filtros y paginación. */
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { useTickets } from "../api/consultas";
import Paginacion from "../componentes/Paginacion";
import { EsqueletoTabla } from "../componentes/Esqueleto";
import { FilaVacia } from "../componentes/EstadoVacio";
import { CajaError, ChipEtapa, ChipGate, ChipTipo, EnlaceCelda, Prio } from "../componentes/ui";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";
import { useDebounce } from "../useDebounce";
import { usePaginaUrl } from "../usePaginaUrl";

const ETAPAS = [
  "triage",
  "design",
  "build",
  "qa",
  "security",
  "deploy",
  "produccion",
  "en_cola_por_cuota",
  "cancelado",
];

export default function Tickets() {
  const fmt = useFormato();
  const { t } = useI18n();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [prioridad, setPrioridad] = useState("");
  // La página vive en la URL (PT-F16); un filtro nuevo vuelve a la primera.
  const [page, irAPagina] = usePaginaUrl();
  // La etapa también (?etapa=en_cola_por_cuota): los avisos de cuota enlazan a los tickets en
  // cola (KC-PT-09-5). Una etapa desconocida en la URL se ignora.
  const [params, setParams] = useSearchParams();
  const etapaUrl = params.get("etapa") ?? "";
  const etapa = ETAPAS.includes(etapaUrl) ? etapaUrl : "";
  function cambiarEtapa(valor: string) {
    setParams(
      (actuales) => {
        const siguientes = new URLSearchParams(actuales);
        if (valor) siguientes.set("etapa", valor);
        else siguientes.delete("etapa");
        // Un filtro nuevo vuelve a la primera página, en la misma entrada del historial.
        siguientes.delete("page");
        return siguientes;
      },
      { replace: true },
    );
  }
  const aLaPrimera = () => irAPagina(1, { reemplazar: true });
  // Se busca al dejar de escribir, no con cada tecla (PT-F08).
  const busqueda = useDebounce(q.trim());
  const { data: datos, error } = useTickets({ q: busqueda, etapa, prioridad, page });
  // Vacío por la búsqueda o los filtros, o porque todavía no hay tickets.
  const conFiltros = Boolean(busqueda || etapa || prioridad);

  return (
    <div className="view" data-testid="tickets.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("tk.title")}</h1>
          <p>{t("tk.sub")}</p>
        </div>
        <div className="spacer" />
        <button
          className="btn"
          onClick={() => nav("/nueva-funcionalidad")}
          data-testid="tickets.cabecera.nueva-funcionalidad"
        >
          ✦ {t("nav.newfeature")}
        </button>
        <button
          className="btn primary"
          onClick={() => nav("/nuevo-ticket")}
          data-testid="tickets.cabecera.nuevo-ticket"
        >
          + {t("nav.newticket")}
        </button>
      </div>

      {/* Sin rótulos visibles (diseño del wireframe): el nombre accesible va en aria-label. */}
      <div className="card filtros" role="search">
        <input
          type="search"
          placeholder={t("tk.buscar")}
          aria-label={t("tk.buscar")}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            aLaPrimera();
          }}
          data-testid="tickets.filtros.busqueda"
        />
        <select
          style={{ width: "auto" }}
          aria-label={t("tk.col.etapa")}
          value={etapa}
          onChange={(e) => cambiarEtapa(e.target.value)}
          data-testid="tickets.filtros.etapa"
        >
          <option value="">{t("tk.todas")}</option>
          {ETAPAS.map((e) => (
            <option key={e} value={e}>
              {t(`st.${e}`)}
            </option>
          ))}
        </select>
        <select
          style={{ width: "auto" }}
          aria-label={t("tk.col.prio")}
          value={prioridad}
          onChange={(e) => {
            setPrioridad(e.target.value);
            aLaPrimera();
          }}
          data-testid="tickets.filtros.prioridad"
        >
          <option value="">{t("tk.toda_prio")}</option>
          {["alta", "media", "baja"].map((p) => (
            <option key={p} value={p}>
              {t(`pr.${p}`)}
            </option>
          ))}
        </select>
      </div>

      <CajaError error={error} />
      {!datos ? (
        <EsqueletoTabla columnas={7} />
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table className="kc" data-testid="tickets.lista.tabla">
            <thead>
              <tr>
                <th>ID</th>
                <th>{t("tk.col.titulo")}</th>
                <th>{t("tk.col.tipo")}</th>
                <th>{t("tk.col.prio")}</th>
                <th>{t("tk.col.etapa")}</th>
                <th>{t("tk.col.gate")}</th>
                <th>{t("tk.col.upd")}</th>
              </tr>
            </thead>
            <tbody>
              {datos.items.map((tk) => (
                <tr
                  key={tk.id}
                  className="click"
                  onClick={() => nav(`/tickets/${tk.id}`)}
                  data-testid="tickets.lista.fila"
                >
                  <td>
                    <EnlaceCelda to={`/tickets/${tk.id}`}>{tk.id}</EnlaceCelda>
                  </td>
                  <td>{tk.titulo}</td>
                  <td>
                    <ChipTipo tipo={tk.tipo} />
                  </td>
                  <td>
                    <Prio p={tk.prioridad} />
                  </td>
                  <td>
                    <ChipEtapa etapa={tk.etapa} />
                  </td>
                  <td>
                    <ChipGate gate={tk.gate_pendiente} />
                  </td>
                  <td>{fmt.fecha(tk.actualizado_en)}</td>
                </tr>
              ))}
              {datos.items.length === 0 && (
                <FilaVacia
                  columnas={7}
                  testid="tickets.lista.vacio"
                  titulo={t(conFiltros ? "vacio.tickets.filtros" : "vacio.tickets")}
                  descripcion={t(conFiltros ? "vacio.tickets.filtros.desc" : "vacio.tickets.desc")}
                />
              )}
            </tbody>
          </table>
          <Paginacion
            pagina={page}
            tamano={datos.page_size}
            total={datos.total}
            enPagina={datos.items.length}
            alCambiar={irAPagina}
            vista="tickets"
          />
        </div>
      )}
    </div>
  );
}
