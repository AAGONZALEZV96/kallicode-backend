/** Explorador de auditoría (§11.1) con filtros; clic abre el expediente. */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useEventosAuditoria } from "../api/consultas";
import Paginacion from "../componentes/Paginacion";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { useI18n } from "../estado/i18n";
import { useDebounce } from "../useDebounce";
import { usePaginaUrl } from "../usePaginaUrl";

export default function Auditoria() {
  const { t } = useI18n();
  const nav = useNavigate();
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  // La página vive en la URL (PT-F16); un filtro nuevo vuelve a la primera.
  const [page, irAPagina] = usePaginaUrl();
  const aLaPrimera = () => irAPagina(1, { reemplazar: true });
  // Se busca al dejar de escribir, no con cada tecla (PT-F08).
  const busqueda = useDebounce(q.trim());
  const { data: datos, error } = useEventosAuditoria({ actor, q: busqueda, page });

  return (
    <div className="view" data-testid="auditoria.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("au.title")}</h1>
          <p>{t("au.sub")}</p>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 14, display: "flex", gap: 10 }}>
        <input
          type="search"
          placeholder={t("tk.buscar")}
          style={{ maxWidth: 240 }}
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            aLaPrimera();
          }}
          data-testid="auditoria.filtros.busqueda"
        />
        <select
          style={{ width: "auto" }}
          value={actor}
          onChange={(e) => {
            setActor(e.target.value);
            aLaPrimera();
          }}
          data-testid="auditoria.filtros.actor"
        >
          <option value="">{t("au.col.actor")}: *</option>
          <option value="humano">👤 Humano</option>
          <option value="agente">⚙ Agente IA</option>
          <option value="sistema">Sistema</option>
        </select>
      </div>
      <CajaError error={error} />
      {!datos ? (
        <Cargando />
      ) : (
        <>
          <div className="card" style={{ padding: 0 }}>
            <table className="kc" data-testid="auditoria.lista.tabla">
              <thead>
                <tr>
                  <th>{t("au.col.fecha")}</th>
                  <th>Ticket</th>
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
                    onClick={() => e.ticket_id && nav(`/auditoria/${e.ticket_id}`)}
                    data-testid="auditoria.lista.fila"
                  >
                    <td>{fechaCorta(e.fecha)}</td>
                    <td>
                      <b>{e.ticket_id ?? "—"}</b>
                    </td>
                    <td>
                      {e.actor_tipo === "humano" ? "👤" : "⚙"} {e.actor_id ?? "sistema"}
                    </td>
                    <td>{e.evento}</td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {e.modelo ?? "—"}
                    </td>
                    <td className="mono" style={{ fontSize: 11 }}>
                      {e.sello.slice(0, 4)}…{e.sello.slice(-4)} ✓
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
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
            {datos.cadena.eventos_hoy} eventos hoy
          </p>
        </>
      )}
    </div>
  );
}
