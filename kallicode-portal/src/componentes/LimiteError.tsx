/**
 * Límites de error (KC-PT-01-3, cierra PT-F04).
 *
 * - nivel "raiz": envuelve toda la app; si algo falla fuera de una ruta, muestra
 *   una pantalla de recuperación a página completa en vez de dejarla en blanco.
 * - nivel "ruta": envuelve el <Outlet> del Layout; el error de una vista no tumba
 *   el header ni la navegación. Se reinicia solo al cambiar de ruta (claveReinicio).
 *
 * React exige un componente de clase para capturar errores de render.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

import {
  describirError,
  nuevoIdError,
  reportarErrorCliente,
  type ErrorCliente,
} from "../api/telemetria";
import { useI18n } from "../estado/i18n";

interface Props {
  nivel: ErrorCliente["nivel"];
  /** Si cambia (p. ej. la ruta actual), el límite vuelve a intentar renderizar. */
  claveReinicio?: string;
  children: ReactNode;
}

interface Estado {
  error: unknown;
  idError: string | null;
  claveCaptura?: string;
}

export default class LimiteError extends Component<Props, Estado> {
  state: Estado = { error: null, idError: null };

  static getDerivedStateFromError(error: unknown): Partial<Estado> {
    return { error, idError: nuevoIdError() };
  }

  static getDerivedStateFromProps(props: Props, estado: Estado): Partial<Estado> | null {
    // Primer render tras capturar: recuerda en qué ruta ocurrió el error.
    if (estado.idError && estado.claveCaptura === undefined) {
      return { claveCaptura: props.claveReinicio };
    }
    // Navegar a otra ruta limpia el error.
    if (estado.idError && props.claveReinicio !== estado.claveCaptura) {
      return { error: null, idError: null, claveCaptura: undefined };
    }
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    reportarErrorCliente(
      describirError(error, {
        id: this.state.idError ?? nuevoIdError(),
        nivel: this.props.nivel,
        ruta: window.location.pathname,
        pilaComponentes: info.componentStack ?? undefined,
      }),
    );
  }

  private reintentar = () => this.setState({ error: null, idError: null, claveCaptura: undefined });

  render() {
    if (!this.state.idError) return this.props.children;
    return (
      <PantallaRecuperacion
        nivel={this.props.nivel}
        idError={this.state.idError}
        onReintentar={this.reintentar}
      />
    );
  }
}

function PantallaRecuperacion({
  nivel,
  idError,
  onReintentar,
}: {
  nivel: ErrorCliente["nivel"];
  idError: string;
  onReintentar: () => void;
}) {
  const { t } = useI18n();
  const raiz = nivel === "raiz";
  return (
    <div
      className={raiz ? "recuperacion recuperacion-raiz" : "recuperacion"}
      role="alert"
      data-testid={`error.${nivel}.pantalla`}
    >
      <div className="card">
        <h2>{t("err.titulo")}</h2>
        <p className="note">{t(raiz ? "err.texto_raiz" : "err.texto_ruta")}</p>
        <p className="note mono">
          {t("err.codigo")}: {idError}
        </p>
        <div className="acciones">
          <button
            className="btn primary"
            onClick={onReintentar}
            data-testid={`error.${nivel}.reintentar`}
          >
            {t("err.reintentar")}
          </button>
          {/* <a> y no <Link>: el límite raíz vive fuera del router. */}
          <a className="btn" href="/" data-testid={`error.${nivel}.inicio`}>
            {t("err.inicio")}
          </a>
        </div>
      </div>
    </div>
  );
}
