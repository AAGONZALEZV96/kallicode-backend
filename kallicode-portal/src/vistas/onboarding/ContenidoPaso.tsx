/**
 * Lo que ya está configurado en cada paso del asistente (KC-PT-07-1). Por ahora resume y
 * enlaza a Conexiones; los formularios de cada paso llegan con KC-PT-07-2 a KC-PT-07-5.
 */
import type { Conexion } from "../../api/tipos";
import { useI18n } from "../../estado/i18n";
import { CATEGORIAS_HERRAMIENTAS, type DatosOnboarding, type Paso } from "./pasos";

function ListaConexiones({ conexiones }: { conexiones: Conexion[] }) {
  const { t } = useI18n();
  // Un estado que el portal no conoce se muestra tal cual.
  const estadoVisible = (estado: string) => {
    const clave = `asis.estado_conexion.${estado}`;
    return t(clave) === clave ? estado : t(clave);
  };
  if (conexiones.length === 0) {
    return (
      <p className="note-inline" data-testid="onboarding.resumen.vacio">
        {t("asis.sin_conexiones")}
      </p>
    );
  }
  return (
    <ul className="asistente-lista">
      {conexiones.map((c) => (
        <li key={c.id} data-testid="onboarding.resumen.conexion">
          <b>{c.proveedor}</b>
          {c.nombre && <span className="note-inline"> · {c.nombre}</span>}{" "}
          <span className={`res ${c.estado === "conectada" ? "pass" : "fail"}`}>
            {estadoVisible(c.estado)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function ContenidoPaso({ paso, datos }: { paso: Paso; datos: DatosOnboarding }) {
  const { t } = useI18n();
  const deCategoria = (categoria: string) =>
    datos.conexiones.conexiones.filter((c) => c.categoria === categoria);

  switch (paso) {
    case "repositorio":
      return <ListaConexiones conexiones={deCategoria("repositorio")} />;

    case "herramientas":
      return (
        <dl className="asistente-categorias">
          {CATEGORIAS_HERRAMIENTAS.map((categoria) => (
            <div key={categoria}>
              <dt>{t(`asis.categoria.${categoria}`)}</dt>
              <dd>
                <ListaConexiones conexiones={deCategoria(categoria)} />
              </dd>
            </div>
          ))}
        </dl>
      );

    case "ramas": {
      const mapa = datos.conexiones.mapa_ramas;
      return mapa ? (
        <p className="mono" data-testid="onboarding.resumen.ramas">
          {mapa.feature} → {mapa.staging} → {mapa.produccion}
        </p>
      ) : (
        <p className="note-inline" data-testid="onboarding.resumen.vacio">
          {t("asis.sin_ramas")}
        </p>
      );
    }

    case "documentacion": {
      const documentos = datos.grafo?.metricas.documentos ?? 0;
      return (
        <p
          className={documentos ? undefined : "note-inline"}
          data-testid="onboarding.resumen.documentos"
        >
          {t("asis.documentos", { n: String(documentos) })}
        </p>
      );
    }

    case "activacion":
      return (
        <>
          {datos.grafo && (
            <p data-testid="onboarding.resumen.grafo">
              {t("asis.grafo", { porcentaje: String(Math.round(datos.grafo.progreso * 100)) })}
            </p>
          )}
          <p data-testid="onboarding.resumen.fabrica">
            {datos.fabricaActiva ? t("asis.fabrica_activa") : t("asis.fabrica_inactiva")}
          </p>
        </>
      );
  }
}
