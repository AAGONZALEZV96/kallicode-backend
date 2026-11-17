/**
 * Contenido de cada paso del asistente. Repositorio y herramientas ya se gestionan aquí
 * (KC-PT-07-2); los demás resumen lo configurado hasta que lleguen sus formularios
 * (KC-PT-07-3 a KC-PT-07-5).
 */
import { useI18n } from "../../estado/i18n";
import ConexionesPaso from "./ConexionesPaso";
import { CATEGORIAS_HERRAMIENTAS, type DatosOnboarding, type Paso } from "./pasos";

export default function ContenidoPaso({ paso, datos }: { paso: Paso; datos: DatosOnboarding }) {
  const { t } = useI18n();
  const todas = datos.conexiones.conexiones;

  switch (paso) {
    case "repositorio":
      return <ConexionesPaso categorias={["repositorio"]} conexiones={todas} />;

    case "herramientas":
      return <ConexionesPaso categorias={CATEGORIAS_HERRAMIENTAS} conexiones={todas} />;

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
