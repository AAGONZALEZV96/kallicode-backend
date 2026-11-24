/**
 * Equipo (KC-PT-10-5): las personas de la organización (GET /users) con búsqueda, filtros por
 * rol y estado en la URL y paginación; owner y admin invitan por correo y cambian el rol o el
 * estado de otras personas. Architect y approver ven la lista sin gestionarla.
 *
 * - Las reglas son las del backend (usePermiso): a un owner o admin solo lo gestiona el owner,
 *   y nadie se gestiona a sí mismo aquí (su perfil va en /perfil).
 * - Las invitaciones pendientes no aparecen: la API no las lista (la persona aparece al aceptar).
 */
import { useState } from "react";

import { useEquipo } from "../api/consultas";
import type { Rol, UsuarioOrg } from "../api/tipos";
import { EsqueletoTabla } from "../componentes/Esqueleto";
import { FilaVacia } from "../componentes/EstadoVacio";
import Paginacion from "../componentes/Paginacion";
import { CajaError } from "../componentes/ui";
import { rolesAsignables, rolActual, useAuth, usePermiso } from "../estado/auth";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";
import { useFiltrosUrl, useTextoEnUrl } from "../useFiltrosUrl";
import { usePaginaUrl } from "../usePaginaUrl";
import ModalCambio, { type CambioUsuario } from "./equipo/ModalCambio";
import ModalInvitar from "./equipo/ModalInvitar";

const ROLES: Rol[] = ["owner", "admin", "architect", "approver", "member", "viewer"];
// users_estado_chk: «invitado» existe en la tabla, aunque hoy las invitaciones van aparte.
const ESTADOS = ["activo", "invitado", "desactivado"];
const FILTROS = ["q", "rol", "estado"] as const;

export default function Equipo() {
  const { t } = useI18n();
  const fmt = useFormato();
  const { usuario } = useAuth();
  const { puedeHacer } = usePermiso();
  const [pagina, irAPagina] = usePaginaUrl();
  const [enUrl, cambiar] = useFiltrosUrl(FILTROS);
  const [q, setQ] = useTextoEnUrl(enUrl.q, (texto) => cambiar({ q: texto }));
  // Un valor desconocido en la URL se ignora.
  const rol = (ROLES as string[]).includes(enUrl.rol) ? enUrl.rol : "";
  const estado = ESTADOS.includes(enUrl.estado) ? enUrl.estado : "";
  const { data, error } = useEquipo({ q: enUrl.q, rol, estado, page: pagina });
  const [invitando, setInvitando] = useState(false);
  const [cambio, setCambio] = useState<CambioUsuario | null>(null);
  const [aviso, setAviso] = useState("");
  const asignables = rolesAsignables(rolActual(usuario));
  const conFiltros = Boolean(enUrl.q || rol || estado);

  const gestionable = (u: UsuarioOrg) => puedeHacer({ tipo: "gestionar-usuario", objetivo: u });
  const hecho = (mensaje: string) => {
    setInvitando(false);
    setCambio(null);
    setAviso(mensaje);
  };

  return (
    <div className="view" data-testid="equipo.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("eq.titulo")}</h1>
          <p>{t("eq.sub")}</p>
        </div>
        <div className="spacer" />
        {puedeHacer({ tipo: "invitar-usuario" }) && (
          <button
            className="btn primary"
            onClick={() => {
              setAviso("");
              setInvitando(true);
            }}
            data-testid="equipo.cabecera.invitar"
          >
            + {t("eq.invitar")}
          </button>
        )}
      </div>
      {aviso && (
        <div className="aviso-ok" role="status" data-testid="equipo.avisos.mensaje">
          {aviso}
        </div>
      )}

      {/* Sin rótulos visibles, como en Tickets: el nombre accesible va en aria-label. */}
      <div className="card filtros" role="search">
        <input
          type="search"
          placeholder={t("eq.buscar")}
          aria-label={t("eq.buscar")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          data-testid="equipo.filtros.busqueda"
        />
        <select
          style={{ width: "auto" }}
          aria-label={t("pf.rol")}
          value={rol}
          onChange={(e) => cambiar({ rol: e.target.value })}
          data-testid="equipo.filtros.rol"
        >
          <option value="">{t("eq.todos_roles")}</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {t(`rol.${r}`)}
            </option>
          ))}
        </select>
        <select
          style={{ width: "auto" }}
          aria-label={t("eq.col.estado")}
          value={estado}
          onChange={(e) => cambiar({ estado: e.target.value })}
          data-testid="equipo.filtros.estado"
        >
          <option value="">{t("eq.todos_estados")}</option>
          {ESTADOS.map((e) => (
            <option key={e} value={e}>
              {t(`eq.estado.${e}`)}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <CajaError error={error} />
      ) : !data ? (
        <EsqueletoTabla columnas={6} filas={5} />
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table className="kc" aria-label={t("eq.titulo")} data-testid="equipo.lista.tabla">
            <thead>
              <tr>
                <th>{t("eq.col.nombre")}</th>
                <th>{t("login.email")}</th>
                <th>{t("pf.rol")}</th>
                <th>{t("eq.col.estado")}</th>
                <th>{t("eq.col.acceso")}</th>
                <th>
                  <span className="solo-lector">{t("eq.col.acciones")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((u) => {
                const editable = gestionable(u);
                return (
                  <tr key={u.id} data-testid="equipo.lista.fila">
                    <td>
                      <b>{u.nombre}</b>
                      {u.id === usuario?.id && <span className="chip tu">{t("eq.tu")}</span>}
                    </td>
                    <td>{u.email}</td>
                    <td>
                      {editable && asignables.includes(u.rol) ? (
                        <select
                          value={u.rol}
                          aria-label={t("eq.rol_de", { nombre: u.nombre })}
                          onChange={(e) =>
                            setCambio({ usuario: u, cambios: { rol: e.target.value as Rol } })
                          }
                          data-testid="equipo.lista.rol"
                        >
                          {asignables.map((r) => (
                            <option key={r} value={r}>
                              {t(`rol.${r}`)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        t(`rol.${u.rol}`)
                      )}
                    </td>
                    <td>
                      <span className={u.estado === "activo" ? "chip estado-ok" : "chip"}>
                        {t(`eq.estado.${u.estado}`)}
                      </span>
                    </td>
                    <td>{u.ultimo_acceso ? fmt.relativo(u.ultimo_acceso) : t("eq.nunca")}</td>
                    <td style={{ textAlign: "right" }}>
                      {/* Activar a un invitado sin que acepte no tiene sentido: solo activo ↔ desactivado. */}
                      {editable && u.estado !== "invitado" && (
                        <button
                          className="btn"
                          onClick={() =>
                            setCambio({
                              usuario: u,
                              cambios: { estado: u.estado === "activo" ? "desactivado" : "activo" },
                            })
                          }
                          aria-label={t(
                            u.estado === "activo" ? "eq.desactivar_a" : "eq.reactivar_a",
                            {
                              nombre: u.nombre,
                            },
                          )}
                          data-testid="equipo.lista.estado"
                        >
                          {t(u.estado === "activo" ? "eq.desactivar" : "eq.reactivar")}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {data.items.length === 0 && (
                <FilaVacia
                  columnas={6}
                  testid="equipo.lista.vacio"
                  titulo={t(conFiltros ? "eq.vacio.filtros" : "eq.vacio")}
                  descripcion={t(conFiltros ? "eq.vacio.filtros.desc" : "eq.vacio.desc")}
                />
              )}
            </tbody>
          </table>
          <Paginacion
            pagina={pagina}
            tamano={data.page_size}
            total={data.total}
            enPagina={data.items.length}
            alCambiar={irAPagina}
            vista="equipo"
          />
        </div>
      )}
      <p className="note" style={{ marginTop: 12 }}>
        {t("eq.nota_invitaciones")}
      </p>

      {invitando && (
        <ModalInvitar roles={asignables} alCerrar={() => setInvitando(false)} alInvitar={hecho} />
      )}
      {cambio && <ModalCambio cambio={cambio} alCerrar={() => setCambio(null)} alCambiar={hecho} />}
    </div>
  );
}
