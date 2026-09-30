/** Conexiones y onboarding (§13): lista, prueba y encendido de la fábrica. */
import { useCallback, useEffect, useId, useState } from "react";

import { api } from "../api/client";
import { CajaError, Cargando, fechaCorta } from "../componentes/ui";
import { useAuth } from "../estado/auth";
import { useI18n } from "../estado/i18n";

interface Conexion {
  id: string;
  categoria: string;
  proveedor: string;
  nombre: string | null;
  estado: string;
  ultimo_test: string | null;
}
interface Datos {
  conexiones: Conexion[];
  mapa_ramas: { feature: string; staging: string; produccion: string } | null;
}
interface Org {
  fabrica_activa: boolean;
}

const CATEGORIAS = ["repositorio", "tickets", "cicd", "database"];

export default function Conexiones() {
  const { t } = useI18n();
  const { usuario } = useAuth();
  const [datos, setDatos] = useState<Datos | null>(null);
  const [org, setOrg] = useState<Org | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [msj, setMsj] = useState("");
  const idFeature = useId();
  const idStaging = useId();
  const idProduccion = useId();

  const esAdmin = ["owner", "admin"].includes(usuario?.rol ?? "");

  const cargar = useCallback(() => {
    api.get<Datos>("/api/v1/connections").then(setDatos).catch(setError);
    api
      .get<Org>("/api/v1/organization")
      .then(setOrg)
      .catch(() => {});
  }, []);
  useEffect(cargar, [cargar]);

  async function probar(id: string) {
    setError(null);
    try {
      const r = await api.post<{ estado: string }>(`/api/v1/connections/${id}/test`);
      setMsj(`Test: ${r.estado}`);
      cargar();
    } catch (e) {
      setError(e);
    }
  }

  async function encender() {
    setError(null);
    try {
      const r = await api.post<{ activado: boolean; lineas_activadas: number }>(
        "/api/v1/onboarding/complete",
      );
      setMsj(`Fábrica activada · ${r.lineas_activadas} líneas`);
      cargar();
    } catch (e) {
      setError(e);
    }
  }

  if (!datos) return <Cargando />;

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("ob.title")}</h1>
          <p>{t("ob.sub")}</p>
        </div>
        <div className="spacer" />
        {esAdmin && org && !org.fabrica_activa && (
          <button className="btn primary" onClick={encender}>
            {t("ob.encender")}
          </button>
        )}
      </div>
      <CajaError error={error} />
      {msj && <div className="aviso-box">{msj}</div>}

      {CATEGORIAS.map((cat) => {
        const lista = datos.conexiones.filter((c) => c.categoria === cat);
        return (
          <div className="card" key={cat} style={{ marginBottom: 14 }}>
            <label>{cat}</label>
            {lista.length === 0 && (
              <p className="note" style={{ marginTop: 6 }}>
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
              >
                <div className="avatar">{c.proveedor.slice(0, 2).toUpperCase()}</div>
                <div style={{ flex: 1 }}>
                  <b>{c.proveedor}</b>
                  <p style={{ fontSize: 12, color: "var(--muted)" }}>
                    {c.nombre ?? "—"} · test: {fechaCorta(c.ultimo_test)}
                  </p>
                </div>
                <span className={`res ${c.estado === "conectada" ? "pass" : "fail"}`}>
                  ● {c.estado}
                </span>
                {esAdmin && (
                  <button className="btn sm" onClick={() => probar(c.id)}>
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
              <input id={idFeature} value={datos.mapa_ramas.feature} readOnly />
            </div>
            <div>
              <label htmlFor={idStaging} style={{ textTransform: "none" }}>
                staging →
              </label>
              <input id={idStaging} value={datos.mapa_ramas.staging} readOnly />
            </div>
            <div>
              <label htmlFor={idProduccion} style={{ textTransform: "none" }}>
                producción →
              </label>
              <input id={idProduccion} value={datos.mapa_ramas.produccion} readOnly />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
