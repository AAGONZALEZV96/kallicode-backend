/** Conexiones y onboarding (§13): lista, prueba y encendido de la fábrica. */
import { useId, useState } from "react";

import {
  useConexiones,
  useEncenderFabrica,
  useOrganizacion,
  useProbarConexion,
} from "../api/consultas";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { usePermiso } from "../estado/auth";
import { useI18n } from "../estado/i18n";

const CATEGORIAS = ["repositorio", "tickets", "cicd", "database"];

export default function Conexiones() {
  const { t } = useI18n();
  const { puedeHacer } = usePermiso();
  const conexiones = useConexiones();
  // Sin la organización solo se oculta el botón de encender: no es motivo de error.
  const { data: org } = useOrganizacion();
  const probarConexion = useProbarConexion();
  const encenderFabrica = useEncenderFabrica();
  const [msj, setMsj] = useState("");
  const idFeature = useId();
  const idStaging = useId();
  const idProduccion = useId();

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
      <Cargando />
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
        <div className="aviso-box" data-testid="conexiones.avisos.mensaje">
          {msj}
        </div>
      )}

      {/* Una tarjeta por categoría: el elemento lleva la categoría (conexiones.categorias.cicd). */}
      {CATEGORIAS.map((cat) => {
        const lista = datos.conexiones.filter((c) => c.categoria === cat);
        return (
          <div
            className="card"
            key={cat}
            style={{ marginBottom: 14 }}
            data-testid={`conexiones.categorias.${cat}`}
          >
            <label>{cat}</label>
            {lista.length === 0 && (
              <p className="note" style={{ marginTop: 6 }} data-testid="conexiones.lista.vacio">
                {t("comun.vacio")}
              </p>
            )}
            {lista.map((c) => (
              <div
                key={c.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "10px 0",
                  borderBottom: "1px solid var(--border)",
                }}
                data-testid="conexiones.lista.fila"
              >
                <div className="avatar">{c.proveedor.slice(0, 2).toUpperCase()}</div>
                <div style={{ flex: 1 }}>
                  <b>{c.proveedor}</b>
                  <p style={{ fontSize: 12, color: "var(--muted)" }}>
                    {c.nombre ?? "—"} · test: {fechaCorta(c.ultimo_test)}
                  </p>
                </div>
                <span
                  className={`res ${c.estado === "conectada" ? "pass" : "fail"}`}
                  data-testid="conexiones.lista.estado"
                >
                  ● {c.estado}
                </span>
                {puedeProbar && (
                  <button
                    className="btn sm"
                    onClick={() => probar(c.id)}
                    data-testid="conexiones.lista.probar"
                  >
                    Test
                  </button>
                )}
              </div>
            ))}
          </div>
        );
      })}

      {datos.mapa_ramas && (
        <div className="card">
          <p className="caption">Mapa de ramas</p>
          <div className="grid3" style={{ marginTop: 6 }}>
            <div>
              <label htmlFor={idFeature} style={{ textTransform: "none" }}>
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
              <label htmlFor={idStaging} style={{ textTransform: "none" }}>
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
              <label htmlFor={idProduccion} style={{ textTransform: "none" }}>
                producción →
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
