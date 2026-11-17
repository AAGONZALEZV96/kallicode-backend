/**
 * Contenido de cada paso del asistente. Repositorio y herramientas (KC-PT-07-2) y el mapa
 * de ramas (KC-PT-07-3) y la activación (KC-PT-07-5) ya se gestionan aquí; la documentación
 * resume lo subido y enlaza a Conexiones.
 */
import { useI18n } from "../../estado/i18n";
import ActivacionPaso from "./ActivacionPaso";
import ConexionesPaso from "./ConexionesPaso";
import MapaRamasPaso from "./MapaRamasPaso";
import { CATEGORIAS_HERRAMIENTAS, type DatosOnboarding, type Paso } from "./pasos";

interface Props {
  paso: Paso;
  datos: DatosOnboarding;
  /** Lleva a otro paso (índice desde 0), con el foco en su título. */
  irAPaso: (indice: number) => void;
}

export default function ContenidoPaso({ paso, datos, irAPaso }: Props) {
  const { t } = useI18n();
  const todas = datos.conexiones.conexiones;

  switch (paso) {
    case "repositorio":
      return <ConexionesPaso categorias={["repositorio"]} conexiones={todas} />;

    case "herramientas":
      return <ConexionesPaso categorias={CATEGORIAS_HERRAMIENTAS} conexiones={todas} />;

    case "ramas":
      return <MapaRamasPaso mapa={datos.conexiones.mapa_ramas} />;

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
      return <ActivacionPaso datos={datos} irAPaso={irAPaso} />;
  }
}
