/** Listado de tickets (§9.1) con filtros y paginación. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useTickets } from "../api/consultas";
import Paginacion from "../componentes/Paginacion";
import {
  CajaError,
  Cargando,
  ChipEtapa,
  ChipGate,
  ChipTipo,
  Prio,
  fechaCorta,
} from "../componentes/ui";
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
  const { t } = useI18n();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [etapa, setEtapa] = useState("");
  const [prioridad, setPrioridad] = useState("");
  // La página vive en la URL (PT-F16); un filtro nuevo vuelve a la primera.
  const [page, irAPagina] = usePaginaUrl();
  const aLaPrimera = () => irAPagina(1, { reemplazar: true });
  // Se busca al dejar de escribir, no con cada tecla (PT-F08).
  const busqueda = useDebounce(q.trim());
  const { data: datos, error } = useTickets({ q: busqueda, etapa, prioridad, page });

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

      <div className="card" style={{ marginBottom: 14, display: "flex", gap: 10 }}>
        <input
          type="search"
          placeholder={t("tk.buscar")}
          style={{ maxWidth: 280 }}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            aLaPrimera();
          }}
          data-testid="tickets.filtros.busqueda"
        />
        <select
          style={{ width: "auto" }}
          value={etapa}
          onChange={(e) => {
            setEtapa(e.target.value);
            aLaPrimera();
          }}
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
        <Cargando />
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
                    <b>{tk.id}</b>
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
                  <td>{fechaCorta(tk.actualizado_en)}</td>
                </tr>
              ))}
              {datos.items.length === 0 && (
                <tr data-testid="tickets.lista.vacio">
                  <td colSpan={7} style={{ color: "var(--muted)" }}>
                    {t("comun.vacio")}
                  </td>
                </tr>
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
