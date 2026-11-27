/** Conexiones y onboarding (§13): lista, prueba y encendido de la fábrica. */
import { useId, useState } from "react";

import {
  useConexiones,
  useEncenderFabrica,
  useOrganizacion,
  useProbarConexion,
} from "../api/consultas";
import { EsqueletoLista } from "../componentes/Esqueleto";
import { EstadoVacio } from "../componentes/EstadoVacio";
import { CajaError } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useFormato } from "../estado/formato";
import { useI18n } from "../estado/i18n";

const CATEGORIAS = ["repositorio", "tickets", "cicd", "database"];

export default function Conexiones() {
  const fmt = useFormato();
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  /** Estado de una conexión traducido; uno que el portal no conoce, tal cual. */
  const estadoConexion = (estado: string) => {
    const clave = `asis.estado_conexion.${estado}`;
    return t(clave) === clave ? estado : t(clave);
  };
  const conexiones = useConexiones();
  // Sin la organización solo se oculta el botón de encender: no es motivo de error.
  const { data: org } = useOrganizacion();
  const probarConexion = useProbarConexion();
  const encenderFabrica = useEncenderFabrica();
  const [msj, setMsj] = useState("");
  const idFeature = useId();
  const idStaging = useId();
  const idProduccion = useId();
  const idCategoria = useId();

  // Encender la fábrica es solo del owner; probar conexiones, de owner y admin.
  const puedeEncender = puedeHacer({ tipo: "encender-fabrica" });
  const puedeProbar = puedeHacer({ tipo: "probar-conexion" });
  const datos = conexiones.data;
  const error = conexiones.error ?? probarConexion.error ?? encenderFabrica.error;

  function probar(id: string) {
    encenderFabrica.reset();
    probarConexion.mutate(id, { onSuccess: (r) => setMsj(`Test: ${r.estado}`) });
  }

  function encender() {
    probarConexion.reset();
    encenderFabrica.mutate(undefined, {
      onSuccess: (r) => setMsj(`Fábrica activada · ${r.lineas_activadas} líneas`),
    });
  }

  // Antes, si la carga fallaba, la vista se quedaba en «Cargando…» para siempre.
  if (!datos)
    return error ? (
      <div className="view" data-testid="conexiones.vista.raiz">
        <CajaError error={error} />
      </div>
    ) : (
      // Mientras carga: el título y la silueta (sin vista.raiz, que indica que la vista cargó).
      <div className="view">
        <div className="view-head">
          <div>
            <h1>{t("ob.title")}</h1>
            <p>{t("ob.sub")}</p>
          </div>
        </div>
        <div className="card">
          <EsqueletoLista elementos={3} />
        </div>
      </div>
    );

  return (
    <div className="view" data-testid="conexiones.vista.raiz">
      <div className="view-head">
        <div>
          <h1>{t("ob.title")}</h1>
          <p>{t("ob.sub")}</p>
        </div>
        <div className="spacer" />
        {puedeEncender && org && !org.fabrica_activa && (
          <button
            className="btn primary"
            onClick={encender}
            data-testid="conexiones.cabecera.encender"
          >
            {t("ob.encender")}
          </button>
        )}
      </div>
      <CajaError error={error} />
      {msj && (
        <div className="aviso-box" role="status" data-testid="conexiones.avisos.mensaje">
          {msj}
        </div>
      )}

      {/* Una tarjeta por categoría: el elemento lleva la categoría (conexiones.categorias.cicd). */}
      {CATEGORIAS.map((cat) => {
        const lista = datos.conexiones.filter((c) => c.categoria === cat);
        return (
          <section
            className="card mb-14"
            key={cat}
            aria-labelledby={`${idCategoria}-${cat}`}
            data-testid={`conexiones.categorias.${cat}`}
          >
            <p className="caption" id={`${idCategoria}-${cat}`}>
              {cat}
            </p>
            {lista.length === 0 && (
              <EstadoVacio
                compacto
                testid="conexiones.lista.vacio"
                titulo={t("vacio.conexiones")}
              />
            )}
            {lista.map((c) => (
              <div key={c.id} className="conexion-lista-fila" data-testid="conexiones.lista.fila">
                <div className="avatar" aria-hidden="true">
                  {c.proveedor.slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1">
                  <b>{c.proveedor}</b>
                  <p className="texto-tenue fs-12">
                    {c.nombre ?? "—"} · {t("cx.ultimo_test", { fecha: fmt.fecha(c.ultimo_test) })}
                  </p>
                </div>
                <span
                  className={`res ${c.estado === "conectada" ? "pass" : "fail"}`}
                  data-testid="conexiones.lista.estado"
                >
                  ● {estadoConexion(c.estado)}
                </span>
                {puedeProbar && (
                  <button
                    className="btn sm"
                    onClick={() => probar(c.id)}
                    aria-label={t("ob.probar", { proveedor: c.proveedor })}
                    data-testid="conexiones.lista.probar"
                  >
                    {t("cx.probar")}
                  </button>
                )}
              </div>
            ))}
          </section>
        );
      })}

      {datos.mapa_ramas && (
        <div className="card">
          <p className="caption">{t("cx.mapa_ramas")}</p>
          <div className="grid3 mt-6">
            <div>
              <label htmlFor={idFeature} className="sin-mayusculas">
                feature →
              </label>
              <input
                id={idFeature}
                value={datos.mapa_ramas.feature}
                readOnly
                data-testid="conexiones.ramas.feature"
              />
            </div>
            <div>
              <label htmlFor={idStaging} className="sin-mayusculas">
                staging →
              </label>
              <input
                id={idStaging}
                value={datos.mapa_ramas.staging}
                readOnly
                data-testid="conexiones.ramas.staging"
              />
            </div>
            <div>
              <label htmlFor={idProduccion} className="sin-mayusculas">
                {t("cx.rama_produccion")} →
              </label>
              <input
                id={idProduccion}
                value={datos.mapa_ramas.produccion}
                readOnly
                data-testid="conexiones.ramas.produccion"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
