/**
 * Conexiones de los pasos 1 y 2 del asistente (KC-PT-07-2): por categoría, la lista con
 * probar, renombrar, rotar credenciales (KC-PT-07-4) y eliminar, y el alta; el alta y la
 * rotación usan el mismo formulario de bóveda (FormularioConexion).
 * Crear, renombrar y probar: owner y admin. Eliminar: solo el owner (connections.eliminar),
 * con confirmación porque borra las credenciales de la bóveda.
 *
 * Teclado (KC-PT-07-6): los formularios en línea (alta, renombre y rotación) toman el foco
 * al abrirse, se cierran con Escape y lo devuelven a su botón; al eliminar una conexión, el
 * foco va a «Agregar» de su categoría en vez de perderse con la fila.
 */
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { useEliminarConexion, useProbarConexion, useRenombrarConexion } from "../../api/consultas";
import type { Conexion } from "../../api/tipos";
import { useFocoAlCerrar } from "../../componentes/foco";
import Modal from "../../componentes/Modal";
import { CajaError, fechaCorta } from "../../componentes/ui";
import { usePermiso } from "../../estado/auth";
import { useI18n } from "../../estado/i18n";
import FormularioConexion from "./FormularioConexion";
import type { Categoria } from "./proveedores";

interface Props {
  categorias: readonly Categoria[];
  conexiones: Conexion[];
}

export default function ConexionesPaso({ categorias, conexiones }: Props) {
  return (
    <div className="conexiones-paso">
      {categorias.map((categoria) => (
        <SeccionCategoria
          key={categoria}
          categoria={categoria}
          conexiones={conexiones.filter((c) => c.categoria === categoria)}
          // Con una sola categoría (repositorio) el título del paso ya la nombra: sin h3 ni
          // región propia, para no repetirla al lector de pantalla.
          conTitulo={categorias.length > 1}
        />
      ))}
    </div>
  );
}

function SeccionCategoria({
  categoria,
  conexiones,
  conTitulo,
}: {
  categoria: Categoria;
  conexiones: Conexion[];
  conTitulo: boolean;
}) {
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const id = useId();
  const [agregando, setAgregando] = useState(false);
  const puedeGestionar = puedeHacer({ tipo: "gestionar-conexion" });
  const botonAbrir = useFocoAlCerrar<HTMLButtonElement>(agregando);
  // Conexión que se pidió eliminar: cuando deja de estar en la lista, su fila (y el foco que
  // tenía) desaparece, así que el foco pasa a «Agregar».
  const eliminando = useRef<string | null>(null);

  useEffect(() => {
    if (!eliminando.current || conexiones.some((c) => c.id === eliminando.current)) return;
    eliminando.current = null;
    botonAbrir.current?.focus();
  }, [conexiones, botonAbrir]);

  return (
    <section
      className="conexiones-categoria"
      aria-labelledby={conTitulo ? id : undefined}
      data-testid={`onboarding.categorias.${categoria}`}
    >
      {conTitulo && <h3 id={id}>{t(`asis.categoria.${categoria}`)}</h3>}
      {conexiones.length === 0 ? (
        <p className="note-inline" data-testid="onboarding.resumen.vacio">
          {t("asis.sin_conexiones")}
        </p>
      ) : (
        <ul className="conexiones-lista">
          {conexiones.map((c) => (
            <FilaConexion key={c.id} conexion={c} alEliminar={() => (eliminando.current = c.id)} />
          ))}
        </ul>
      )}
      {puedeGestionar &&
        (agregando ? (
          <FormularioConexion categoria={categoria} alCerrar={() => setAgregando(false)} />
        ) : (
          <button
            ref={botonAbrir}
            type="button"
            className="btn sm"
            onClick={() => setAgregando(true)}
            data-testid="onboarding.formulario.abrir"
          >
            {t(`con.agregar.${categoria}`)}
          </button>
        ))}
    </section>
  );
}

function FilaConexion({ conexion: c, alEliminar }: { conexion: Conexion; alEliminar: () => void }) {
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const id = useId();
  const probar = useProbarConexion();
  const renombrar = useRenombrarConexion();
  const eliminar = useEliminarConexion();
  const [editando, setEditando] = useState(false);
  const [nombre, setNombre] = useState(c.nombre ?? "");
  const [confirmando, setConfirmando] = useState(false);
  const [rotando, setRotando] = useState(false);
  const puedeGestionar = puedeHacer({ tipo: "gestionar-conexion" });
  // Al cerrar el renombre o la rotación, el foco vuelve a su botón y no se pierde.
  const botonRenombrar = useFocoAlCerrar<HTMLButtonElement>(editando);
  const botonRotar = useFocoAlCerrar<HTMLButtonElement>(rotando);
  const campoNombre = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editando) campoNombre.current?.focus();
  }, [editando]);

  function tecladoRenombre(e: KeyboardEvent) {
    if (e.key !== "Escape" || renombrar.isPending) return;
    e.stopPropagation();
    setEditando(false);
  }
  const proveedor =
    t(`con.proveedor.${c.proveedor}`) === `con.proveedor.${c.proveedor}`
      ? c.proveedor
      : t(`con.proveedor.${c.proveedor}`);
  const claveEstado = `asis.estado_conexion.${c.estado}`;
  const estado = t(claveEstado) === claveEstado ? c.estado : t(claveEstado);

  function guardarNombre(e: FormEvent) {
    e.preventDefault();
    renombrar.mutate({ id: c.id, nombre: nombre.trim() }, { onSuccess: () => setEditando(false) });
  }

  return (
    <li className="conexion-fila" data-testid="onboarding.resumen.conexion">
      <div className="conexion-datos">
        <b>{proveedor}</b>
        {c.nombre && <span className="note-inline"> · {c.nombre}</span>}{" "}
        <span className={`res ${c.estado === "conectada" ? "pass" : "fail"}`}>{estado}</span>
        <span className="note-inline">
          {" "}
          · {t("con.ultima_prueba", { fecha: fechaCorta(c.ultimo_test) })}
        </span>
      </div>

      {editando ? (
        // Escape equivale a «Cancelar».
        // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
        <form className="conexion-renombrar" onSubmit={guardarNombre} onKeyDown={tecladoRenombre}>
          <label htmlFor={`${id}-nombre`}>{t("con.nombre")}</label>
          <input
            ref={campoNombre}
            id={`${id}-nombre`}
            value={nombre}
            maxLength={120}
            required
            onChange={(e) => setNombre(e.target.value)}
            data-testid="onboarding.conexiones.nombre"
          />
          <button
            type="submit"
            className="btn sm primary"
            disabled={renombrar.isPending || !nombre.trim()}
            data-testid="onboarding.conexiones.guardar"
          >
            {t("con.guardar")}
          </button>
          <button type="button" className="btn sm" onClick={() => setEditando(false)}>
            {t("comun.cancelar")}
          </button>
        </form>
      ) : rotando ? (
        <FormularioConexion rotar={c} alCerrar={() => setRotando(false)} />
      ) : (
        <div className="conexion-acciones">
          {puedeGestionar && (
            <>
              <button
                type="button"
                className="btn sm"
                aria-label={t("con.probar_a", { proveedor })}
                disabled={probar.isPending}
                onClick={() => probar.mutate(c.id)}
                data-testid="onboarding.conexiones.probar"
              >
                {probar.isPending ? t("con.probando") : t("con.probar")}
              </button>
              <button
                ref={botonRenombrar}
                type="button"
                className="btn sm"
                aria-label={t("con.renombrar_a", { proveedor })}
                onClick={() => setEditando(true)}
                data-testid="onboarding.conexiones.renombrar"
              >
                {t("con.renombrar")}
              </button>
              <button
                ref={botonRotar}
                type="button"
                className="btn sm"
                aria-label={t("con.rotar_a", { proveedor })}
                onClick={() => setRotando(true)}
                data-testid="onboarding.conexiones.rotar"
              >
                {t("con.rotar")}
              </button>
            </>
          )}
          {puedeHacer({ tipo: "eliminar-conexion" }) && (
            <button
              type="button"
              className="btn sm warn"
              aria-label={t("con.eliminar_a", { proveedor })}
              aria-haspopup="dialog"
              onClick={() => setConfirmando(true)}
              data-testid="onboarding.conexiones.eliminar"
            >
              {t("con.eliminar")}
            </button>
          )}
        </div>
      )}

      {probar.data && (
        <p
          role="status"
          className={probar.data.estado === "conectada" ? "res pass" : "res fail"}
          data-testid="onboarding.conexiones.resultado"
        >
          {probar.data.estado === "conectada"
            ? t("con.prueba_ok")
            : t("con.prueba_error", { detalle: probar.data.detalle?.mensaje ?? "" })}
        </p>
      )}
      <CajaError error={probar.error ?? renombrar.error} />

      {confirmando && (
        <Modal
          titulo={t("con.eliminar_titulo", { proveedor })}
          alCerrar={() => setConfirmando(false)}
          bloqueado={eliminar.isPending}
          testid="onboarding.eliminacion.dialogo"
        >
          <p>{t("con.eliminar_aviso")}</p>
          <CajaError error={eliminar.error} />
          <div className="modal-acciones">
            <button
              type="button"
              className="btn"
              disabled={eliminar.isPending}
              onClick={() => setConfirmando(false)}
              data-testid="onboarding.eliminacion.cancelar"
            >
              {t("comun.cancelar")}
            </button>
            <button
              type="button"
              className="btn warn"
              disabled={eliminar.isPending}
              onClick={() => {
                alEliminar();
                eliminar.mutate(c.id, { onSuccess: () => setConfirmando(false) });
              }}
              data-testid="onboarding.eliminacion.confirmar"
            >
              {t("con.eliminar")}
            </button>
          </div>
        </Modal>
      )}
    </li>
  );
}
